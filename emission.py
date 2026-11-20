#!/usr/bin/env python3
"""Emission: Baude's browser, a GTK window of its own (and, later, one of Baude's side panel's pages): tabs, back and
forward, reload, an address that's a search too, and profiles, each its own cookies and logins kept (a login made once,
stored, as a Bot's identity will use it). Its pages are WebKit's: on macOS Apple's WKWebView (MacWeb), put in the
window through the Objective-C runtime (macos.objc_send: there's no PyObjC); on Linux WebKitGTK 6.0 (GtkWeb), a GTK
widget of its own. On a Linux host with no display it starts one of its own (xvfb-run).

    python3 emission.py [URL] [--profile NAME]
    python3 emission.py --install | --uninstall   # app-menu launcher with the logo (per user)
"""
import argparse
import base64
import ctypes
import hashlib
import html
import ipaddress
import json
import math
import os
import platform
import shutil
import socket
import sys
import threading
import time
import urllib.parse
import uuid
import zlib

import macos  # noqa: E402  (no GTK: before it's loaded)
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

APP_ID = "dev.baude.Emission"
HOME_PAGE = "https://duckduckgo.com/"
SEARCH = "https://duckduckgo.com/?q={}"
POLL_MS = 200  # (each page's title, address, progress, back and forward read this often, the same on both engines)
SCREEN = (3840, 2400)  # (the virtual display a Linux agent's Emission is on: resize's limits fit in it)
AGENT_IDLE_S = 30 * 60  # (an Emission started for a Bot: gone after this long without a request)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from baudelib import applist  # noqa: E402  (Emission in the list of apps, as Record is)
from baudelib import tokens  # noqa: E402  (its pointer's sizes and times: schema/tokens, the Android app's too)
from baudelib import wire  # noqa: E402  (its screenshots' sizes and qualities, as the phone's: the wire's schema)
from baudelib import wording  # noqa: E402  (its words, Baude's browser's: schema/copy)
from baudelib.emissionlink import (DEFAULT, HELD, add_profile, bot_profile, call, call_bot, clear_leftovers,  # noqa
                                   data_home, profile_socket, forget_profile, hand_over, leftovers, lock_profile,
                                   note_leftover, open_pages, pending_note, profile_id, profiles, rename_profile,
                                   unlock_profile)


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
    """What's typed in the address bar as a URL: one as it is; a path as a file; a local host or an IP address given
    http://, another host https://; anything else searched for."""
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
            local = host == "localhost" or host.endswith(".localhost")
        if local:
            return "http://" + text
        if "." in host:
            return "https://" + text
    return SEARCH.format(urllib.parse.quote_plus(text))


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


class Blocks:
    """Objective-C blocks through ctypes: calling one WebKit hands a delegate (kept past the call with copy, as a
    dialog answers later), and making one to hand WebKit."""
    lib = None
    kept = []

    @classmethod
    def system(cls):
        if cls.lib is None:
            cls.lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
            cls.lib._Block_copy.restype = ctypes.c_void_p
            cls.lib._Block_copy.argtypes = [ctypes.c_void_p]
            cls.lib._Block_release.argtypes = [ctypes.c_void_p]
        return cls.lib

    @classmethod
    def copy(cls, block):
        return cls.system()._Block_copy(block)

    @classmethod
    def call(cls, block, *args, types=(), release=False):
        """block(*args): its function is the fourth field of its literal (isa, flags, reserved, invoke)."""
        if not block:
            return
        invoke = ctypes.c_void_p.from_address(block + 16).value
        ctypes.CFUNCTYPE(None, ctypes.c_void_p, *types)(invoke)(block, *args)
        if release:
            cls.system()._Block_release(block)

    live = {}  # (blocks for one call, kept till they've run: WebKit has only their address)

    @classmethod
    def once(cls, fn, types=()):
        """A block for one call (a completion handler's): kept till it's run, then let go of."""
        held = {}

        def run(*args):
            try:
                fn(*args)
            except Exception:  # (none can cross back into WebKit)
                sys.excepthook(*sys.exc_info())
            finally:
                GLib.idle_add(lambda: cls.live.pop(id(held), None) and False)  # (not while it's running)
        held["block"] = cls.make(run, types, keep=held)
        cls.live[id(held)] = held
        return held["block"]

    @classmethod
    def make(cls, fn, types=(), keep=None):
        """A global block running fn(*args) (kept for good: they're few)."""
        class Desc(ctypes.Structure):
            _fields_ = [("reserved", ctypes.c_ulong), ("size", ctypes.c_ulong)]

        class Literal(ctypes.Structure):
            _fields_ = [("isa", ctypes.c_void_p), ("flags", ctypes.c_int), ("reserved", ctypes.c_int),
                        ("invoke", ctypes.c_void_p), ("desc", ctypes.POINTER(Desc))]
        imp = ctypes.CFUNCTYPE(None, ctypes.c_void_p, *types)(lambda _b, *a: fn(*a))
        desc = Desc(0, ctypes.sizeof(Literal))
        isa = ctypes.addressof(ctypes.c_void_p.in_dll(cls.system(), "_NSConcreteGlobalBlock"))
        lit = Literal(isa, 1 << 28, 0, ctypes.cast(imp, ctypes.c_void_p).value, ctypes.pointer(desc))
        if keep is None:
            cls.kept.extend((imp, desc, lit))
        else:
            keep["parts"] = (imp, desc, lit)
        return ctypes.addressof(lit)


class MacPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


MAC_MODS = {"shift": 1 << 17, "ctrl": 1 << 18, "alt": 1 << 19, "cmd": 1 << 20}
MAC_KEYS = {"Enter": (36, "\r"), "Tab": (48, "\t"), "Space": (49, " "), "Backspace": (51, "\x7f"),
            "Escape": (53, "\x1b"), "Delete": (117, "\uf728"), "Left": (123, "\uf702"), "Right": (124, "\uf703"),
            "Down": (125, "\uf701"), "Up": (126, "\uf700"), "Home": (115, "\uf729"), "End": (119, "\uf72b"),
            "PageUp": (116, "\uf72c"), "PageDown": (121, "\uf72d")}
MAC_CHAR_CODES = dict(zip("asdfhgzxcv bqweryt123465=97-80]ou[ip lj'k;\\,/nm.", range(0, 48)))
MAC_CHAR_CODES.pop(" ", None)
MAC_CHAR_CODES["`"] = 50
MAC_SHIFTED = dict(zip('!@#$%^&*()_+{}|:"<>?~', "1234567890-=[]\\;',./`"))  # (a US keyboard's Shift)
MAC_COMMANDS = {("Enter", ()): b"insertNewline:",  # (a window that isn't key runs every other key's editing from its
                ("a", ("cmd",)): b"selectAll:", ("c", ("cmd",)): b"copy:",  # events: only Return's isn't)
                ("x", ("cmd",)): b"cut:", ("v", ("cmd",)): b"paste:", ("z", ("cmd",)): b"undo:",
                ("z", ("cmd", "shift")): b"redo:", ("Left", ("cmd",)): b"moveToBeginningOfLine:",
                ("Right", ("cmd",)): b"moveToEndOfLine:", ("Up", ("cmd",)): b"moveToBeginningOfDocument:",
                ("Down", ("cmd",)): b"moveToEndOfDocument:"}


class MacWeb:
    """One page in a WKWebView, laid over its place in a GTK window (place: the widget it's drawn over), with the
    store of its profile (WKWebsiteDataStore dataStoreForIdentifier:, macOS 14+: kept on disk, apart from others').
    Its tab sets window (what asks the user: an alert, a sign-in, files to upload, a download saved), popup (a page
    this one opens in a window of its own: a new MacWeb, given the configuration WebKit made for it) and closed."""
    loaded = False
    delegate = None
    views = {}  # (each view's MacWeb, by its pointer: what the delegate's told about)
    downloads = {}  # (each download's path, by its pointer)
    kept = []  # (the delegate's methods: kept while it's there)

    @classmethod
    def load_kit(cls):
        if not cls.loaded:
            ctypes.cdll.LoadLibrary("/System/Library/Frameworks/WebKit.framework/WebKit")
            ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Foundation.framework/Foundation")
            cls.loaded = True

    @classmethod
    def make_delegate(cls):
        """One object, every view's navigation, UI and download delegate."""
        if cls.delegate is not None:
            return cls.delegate
        rt = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
        rt.objc_getClass.restype = rt.sel_registerName.restype = rt.objc_allocateClassPair.restype = ctypes.c_void_p
        rt.objc_getClass.argtypes = rt.sel_registerName.argtypes = [ctypes.c_char_p]
        rt.objc_allocateClassPair.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
        rt.class_addMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p]
        rt.objc_registerClassPair.argtypes = [ctypes.c_void_p]
        klass = rt.objc_getClass(b"EmissionDelegate")
        if not klass:
            klass = rt.objc_allocateClassPair(rt.objc_getClass(b"NSObject"), b"EmissionDelegate", 0)
            P = ctypes.c_void_p

            def add(name, fn, nargs, ret=None):
                def safe(*a):
                    try:
                        return fn(*a[2:])
                    except Exception:  # (an exception can't cross back into WebKit: said, and let go)
                        sys.excepthook(*sys.exc_info())
                        return None
                imp = ctypes.CFUNCTYPE(ret, *[P] * (nargs + 2))(safe)
                cls.kept.append(imp)
                rt.class_addMethod(klass, rt.sel_registerName(name), ctypes.cast(imp, P).value,
                                   (b"@" if ret else b"v") + b"@:" + b"@" * nargs)

            add(b"webView:didFailProvisionalNavigation:withError:", lambda v, _n, e: cls.failed(v, e), 3)
            add(b"webView:didFailNavigation:withError:", lambda v, _n, e: cls.failed(v, e), 3)
            add(b"webView:decidePolicyForNavigationAction:decisionHandler:", cls.decide_action, 3)
            add(b"webView:decidePolicyForNavigationResponse:decisionHandler:", cls.decide_response, 3)
            add(b"webView:navigationAction:didBecomeDownload:", lambda _v, _a, d: cls.downloading(d), 3)
            add(b"webView:navigationResponse:didBecomeDownload:", lambda _v, _r, d: cls.downloading(d), 3)
            add(b"webView:didReceiveAuthenticationChallenge:completionHandler:", cls.challenged, 3)
            add(b"webView:createWebViewWithConfiguration:forNavigationAction:windowFeatures:",
                lambda v, config, _a, _f: cls.opened(v, config), 4, ret=P)
            add(b"webViewDidClose:", lambda v: cls.views.get(v) and GLib.idle_add(cls.views[v].closed), 1)
            add(b"webView:runJavaScriptAlertPanelWithMessage:initiatedByFrame:completionHandler:",
                lambda v, msg, _f, done: cls.js_dialog(v, "alert", msg, None, done), 4)
            add(b"webView:runJavaScriptConfirmPanelWithMessage:initiatedByFrame:completionHandler:",
                lambda v, msg, _f, done: cls.js_dialog(v, "confirm", msg, None, done), 4)
            add(b"webView:runJavaScriptTextInputPanelWithPrompt:defaultText:initiatedByFrame:completionHandler:",
                lambda v, msg, text, _f, done: cls.js_dialog(v, "prompt", msg, text, done), 5)
            add(b"webView:runOpenPanelWithParameters:initiatedByFrame:completionHandler:", cls.choose_files, 4)
            add(b"download:decideDestinationUsingResponse:suggestedFilename:completionHandler:", cls.destination, 4)
            add(b"downloadDidFinish:", lambda d: cls.download_done(d, None), 1)
            add(b"download:didFailWithError:resumeData:", lambda d, e, _r: cls.download_done(d, e), 3)
            rt.objc_registerClassPair(klass)
        cls.delegate = macos.objc_send(macos.objc_send(b"EmissionDelegate", b"alloc"), b"init")
        return cls.delegate

    # (what the delegate's told)

    @classmethod
    def failed(cls, view, error):
        web = cls.views.get(view)
        send = macos.objc_send
        domain = cls._text(send(send(error, b"domain"), b"description"))
        code = send(error, b"code", ret=ctypes.c_long)
        if web is None or (domain == "NSURLErrorDomain" and code == -999) or \
                (domain == "WebKitErrorDomain" and code in (102, 204)):
            return  # (a load stopped, or handed to a download or a plug-in: no failure)
        info = send(error, b"userInfo")
        url = cls._text(send(info, b"objectForKey:", macos.ns_string("NSErrorFailingURLStringKey"),
                             types=(ctypes.c_void_p,))) or web.asked
        web.show_failure(url, cls._text(send(error, b"localizedDescription")))

    # (each of these answers WebKit on every path: a completion handler never called aborts the app)

    @classmethod
    def decide_action(cls, _view, action, decide):
        policy = 1  # (WKNavigationActionPolicy: allow)
        try:
            if macos.objc_send(action, b"shouldPerformDownload", ret=ctypes.c_bool):
                policy = 2  # (download)
        finally:
            Blocks.call(decide, policy, types=(ctypes.c_long,))

    @classmethod
    def decide_response(cls, _view, response, decide):
        policy = 1  # (WKNavigationResponsePolicy: allow)
        try:
            send = macos.objc_send
            show = send(response, b"canShowMIMEType", ret=ctypes.c_bool)
            r = send(response, b"response")
            if show and send(r, b"respondsToSelector:", objc_sel(b"valueForHTTPHeaderField:"),
                             types=(ctypes.c_void_p,), ret=ctypes.c_bool):
                how = cls._text(send(r, b"valueForHTTPHeaderField:", macos.ns_string("Content-Disposition"),
                                     types=(ctypes.c_void_p,)))
                show = not how.strip().lower().startswith("attachment")
            policy = 1 if show else 2  # (download)
        finally:
            Blocks.call(decide, policy, types=(ctypes.c_long,))

    @classmethod
    def downloading(cls, download):
        macos.objc_send(download, b"setDelegate:", cls.make_delegate(), types=(ctypes.c_void_p,))

    @classmethod
    def destination(cls, download, _response, name, done):
        url = None  # (none: the download's cancelled)
        try:
            path = download_path(cls._text(name))
            cls.downloads[download] = path
            url = macos.objc_send(b"NSURL", b"fileURLWithPath:", macos.ns_string(path), types=(ctypes.c_void_p,))
        except OSError as e:
            web = cls.views.get(macos.objc_send(download, b"webView"))
            if web is not None and web.window is not None:
                web.window.saved(cls._text(name) or "download", e.strerror or str(e))
        finally:
            Blocks.call(done, url, types=(ctypes.c_void_p,))

    @classmethod
    def download_done(cls, download, error):
        path = cls.downloads.pop(download, None)
        web = cls.views.get(macos.objc_send(download, b"webView"))
        if path and web is not None and web.window is not None:
            why = cls._text(macos.objc_send(error, b"localizedDescription")) if error else None
            web.window.saved(path, why)

    @classmethod
    def challenged(cls, view, challenge, done):
        send = macos.objc_send
        held = None
        try:
            space = send(challenge, b"protectionSpace")
            method = cls._text(send(space, b"authenticationMethod"))
            web = cls.views.get(view)
            if web is None or web.window is None or method not in (
                    "NSURLAuthenticationMethodHTTPBasic", "NSURLAuthenticationMethodHTTPDigest",
                    "NSURLAuthenticationMethodDefault"):
                return  # (default handling, below: TLS and the like)
            held = Blocks.copy(done)

            def answer(user, password):
                if user is None:
                    Blocks.call(held, 2, None, types=(ctypes.c_long, ctypes.c_void_p), release=True)  # (cancelled)
                    return
                cred = send(b"NSURLCredential", b"credentialWithUser:password:persistence:", macos.ns_string(user),
                            macos.ns_string(password), 1, types=(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong))
                Blocks.call(held, 0, cred, types=(ctypes.c_long, ctypes.c_void_p), release=True)  # (use it)
            answer = once(answer)
            try:
                web.window.sign_in(web, cls._text(send(space, b"host")), cls._text(send(space, b"realm")),
                                   send(challenge, b"previousFailureCount", ret=ctypes.c_long) > 0, answer)
            except Exception:
                answer(None, None)
                raise
        finally:
            if held is None:
                Blocks.call(done, 1, None, types=(ctypes.c_long, ctypes.c_void_p))  # (default handling)

    @classmethod
    def opened(cls, view, config):
        web = cls.views.get(view)
        new = web.popup(config) if web is not None and web.popup else None
        return new.view if new is not None else None

    @classmethod
    def js_dialog(cls, view, kind, message, text, done):
        web = cls.views.get(view)
        done = Blocks.copy(done)
        if kind == "alert":
            def finish(_answer):
                Blocks.call(done, release=True)
        elif kind == "confirm":
            def finish(answer):
                Blocks.call(done, bool(answer), types=(ctypes.c_bool,), release=True)
        else:
            def finish(answer):
                Blocks.call(done, macos.ns_string(answer) if answer is not None else None, types=(ctypes.c_void_p,),
                            release=True)
        finish = once(finish)
        try:
            if web is None or web.window is None:
                finish(None)
                return
            web.window.page_dialog(web, kind, cls._text(message), cls._text(text), finish)
        except Exception:
            finish(None)
            raise

    @classmethod
    def choose_files(cls, view, params, _frame, done):
        web = cls.views.get(view)
        send = macos.objc_send
        done = Blocks.copy(done)

        def finish(paths):
            arr = None
            if paths:
                arr = send(b"NSMutableArray", b"array")
                for p in paths:
                    send(arr, b"addObject:", send(b"NSURL", b"fileURLWithPath:", macos.ns_string(p),
                                                  types=(ctypes.c_void_p,)), types=(ctypes.c_void_p,))
            Blocks.call(done, arr, types=(ctypes.c_void_p,), release=True)
        finish = once(finish)
        try:
            if web is None or web.window is None:
                finish(None)
                return
            web.window.choose_files(send(params, b"allowsMultipleSelection", ret=ctypes.c_bool),
                                    send(params, b"allowsDirectories", ret=ctypes.c_bool), finish, web)
        except Exception:
            finish(None)
            raise

    # (one page)

    def __init__(self, place, profile, opener=None):
        """profile: its id. opener: the configuration WebKit made for a page another opened (its store in it)."""
        self.load_kit()
        send, R = macos.objc_send, macos._rect()
        self.place, self.view, self.shown = place, None, True
        self.window = self.popup = self.closed = None
        self.asked, self.failure = "", None
        if opener:
            config = opener
        else:
            config = send(send(b"WKWebViewConfiguration", b"alloc"), b"init")
            nsuuid = send(send(b"NSUUID", b"alloc"), b"initWithUUIDString:", macos.ns_string(profile),
                          types=(ctypes.c_void_p,))
            store = send(b"WKWebsiteDataStore", b"dataStoreForIdentifier:", nsuuid, types=(ctypes.c_void_p,))
            send(nsuuid, b"release")
            if not store:
                raise RuntimeError("WebKit gave no store for this profile")
            send(config, b"setWebsiteDataStore:", store, types=(ctypes.c_void_p,))
            send(config, b"setApplicationNameForUserAgent:", macos.ns_string(self.user_agent()),
                 types=(ctypes.c_void_p,))
        if not opener and for_bots(profile):
            script = send(send(b"WKUserScript", b"alloc"), b"initWithSource:injectionTime:forMainFrameOnly:",
                          macos.ns_string(CONSOLE_JS), 0, True, types=(ctypes.c_void_p, ctypes.c_long, ctypes.c_bool))
            send(send(config, b"userContentController"), b"addUserScript:", script, types=(ctypes.c_void_p,))
            send(script, b"release")
        prefs = send(config, b"preferences")
        for sel in (b"_setHiddenPageDOMTimerThrottlingEnabled:", b"_setPageVisibilityBasedProcessSuppressionEnabled:")\
                if AGENT["on"] or for_bots(profile) else ():
            if send(prefs, b"respondsToSelector:", objc_sel(sel), types=(ctypes.c_void_p,), ret=ctypes.c_bool):
                send(prefs, sel, False, types=(ctypes.c_bool,))  # (a page out of sight runs as one in sight)
        self.view = send(send(b"WKWebView", b"alloc"), b"initWithFrame:configuration:", R(0, 0, 10, 10), config,
                         types=(R, ctypes.c_void_p))
        if (AGENT["on"] or for_bots(profile)) and send(self.view, b"respondsToSelector:", objc_sel(
                b"_setWindowOcclusionDetectionEnabled:"),
                                types=(ctypes.c_void_p,), ret=ctypes.c_bool):
            send(self.view, b"_setWindowOcclusionDetectionEnabled:", False, types=(ctypes.c_bool,))  # (an agent's
            # window is never in front of anyone: its pages drawn as they change while it's in, unseen: in_unseen)
        if (agent := os.environ.get("EMISSION_USER_AGENT")):
            send(self.view, b"setCustomUserAgent:", macos.ns_string(agent), types=(ctypes.c_void_p,))
        if not opener:
            send(config, b"release")  # (the view keeps its own copy)
        send(self.view, b"setAllowsBackForwardNavigationGestures:", True, types=(ctypes.c_bool,))
        send(self.view, b"setAllowsMagnification:", True, types=(ctypes.c_bool,))  # (a pinch zooms it, as Safari's)
        send(self.view, b"setAutoresizingMask:", 2 | 16, types=(ctypes.c_ulong,))  # (sized with the window between
        delegate = self.make_delegate()                                             # polls: its margins kept)
        send(self.view, b"setNavigationDelegate:", delegate, types=(ctypes.c_void_p,))
        send(self.view, b"setUIDelegate:", delegate, types=(ctypes.c_void_p,))
        self.views[self.view] = self
        self.attached = self.attached_root = self.blocker = None
        self.overlay, self.tab_id = None, None  # (overlay: what's drawn over it, hosted: MacOverlay)

    @staticmethod
    def jar(profile):
        """profile's cookie store (its WKHTTPCookieStore)."""
        MacWeb.load_kit()
        send = macos.objc_send
        nsuuid = send(send(b"NSUUID", b"alloc"), b"initWithUUIDString:", macos.ns_string(profile),
                      types=(ctypes.c_void_p,))
        store = send(b"WKWebsiteDataStore", b"dataStoreForIdentifier:", nsuuid, types=(ctypes.c_void_p,))
        send(nsuuid, b"release")
        return send(store, b"httpCookieStore")

    @classmethod
    def cookies(cls, profile, done):
        """As GtkWeb's: profile's cookies, done(list, None) or (None, why)."""
        send = macos.objc_send

        def got(array):
            out = []
            for i in range(send(array, b"count", ret=ctypes.c_ulong) if array else 0):
                c = send(array, b"objectAtIndex:", i, types=(ctypes.c_ulong,))
                exp = send(c, b"expiresDate")
                out.append({"name": cls._text(send(c, b"name")), "value": cls._text(send(c, b"value")),
                            "domain": cls._text(send(c, b"domain")), "path": cls._text(send(c, b"path")),
                            "expires": send(exp, b"timeIntervalSince1970", ret=ctypes.c_double) if exp else None,
                            "secure": send(c, b"isSecure", ret=ctypes.c_bool),
                            "http_only": send(c, b"isHTTPOnly", ret=ctypes.c_bool),
                            "same_site": cls._text(send(c, b"sameSitePolicy")).lower() or None})
            GLib.idle_add(lambda: done(out, None) and False)
        send(cls.jar(profile), b"getAllCookies:", Blocks.once(got, types=(ctypes.c_void_p,)), types=(ctypes.c_void_p,))

    @classmethod
    def add_cookies(cls, profile, cookies, done):
        """As GtkWeb's: cookies put in profile's store, done(how many, None) or (None, why)."""
        send, jar, left = macos.objc_send, cls.jar(profile), list(cookies)

        def next_one(*_):
            if not left:
                GLib.idle_add(lambda: done(len(cookies), None) and False)
                return
            c = left.pop(0)
            props = send(b"NSMutableDictionary", b"dictionary")
            for key, value in (("Name", c["name"]), ("Value", c.get("value") or ""), ("Domain", c["domain"]),
                               ("Path", c.get("path") or "/")):
                send(props, b"setObject:forKey:", macos.ns_string(str(value)), macos.ns_string(key),
                     types=(ctypes.c_void_p, ctypes.c_void_p))
            if c.get("expires"):
                send(props, b"setObject:forKey:", send(b"NSDate", b"dateWithTimeIntervalSince1970:",
                                                       float(c["expires"]), types=(ctypes.c_double,)),
                     macos.ns_string("Expires"), types=(ctypes.c_void_p, ctypes.c_void_p))
            for key, on in (("Secure", c.get("secure")), ("HttpOnly", c.get("http_only"))):
                if on:
                    send(props, b"setObject:forKey:", macos.ns_string("TRUE"), macos.ns_string(key),
                         types=(ctypes.c_void_p, ctypes.c_void_p))
            if c.get("same_site") in ("lax", "strict"):
                send(props, b"setObject:forKey:", macos.ns_string(c["same_site"]), macos.ns_string("SameSite"),
                     types=(ctypes.c_void_p, ctypes.c_void_p))
            made = send(b"NSHTTPCookie", b"cookieWithProperties:", props, types=(ctypes.c_void_p,))
            if not made:
                next_one()
                return
            send(jar, b"setCookie:completionHandler:", made, Blocks.once(next_one),
                 types=(ctypes.c_void_p, ctypes.c_void_p))
        next_one()

    @staticmethod
    def remove_store(profile, done):
        """profile's store (its id) deleted, then done(why it couldn't be, or None)."""
        MacWeb.load_kit()
        send = macos.objc_send
        nsuuid = send(send(b"NSUUID", b"alloc"), b"initWithUUIDString:", macos.ns_string(profile),
                      types=(ctypes.c_void_p,))

        def finished(error):
            why = MacWeb._text(send(error, b"localizedDescription")) if error else None
            GLib.idle_add(lambda: done(why) and False)
        send(b"WKWebsiteDataStore", b"removeDataStoreForIdentifier:completionHandler:", nsuuid,
             Blocks.make(finished, types=(ctypes.c_void_p,)), types=(ctypes.c_void_p, ctypes.c_void_p))
        send(nsuuid, b"release")

    awake = None

    @classmethod
    def stay_awake(cls):
        """An agent's Emission kept running at full speed though none of its windows is on screen: no App Nap
        (an activity begun, as a user's would be, the Mac still free to sleep)."""
        send = macos.objc_send
        info = send(b"NSProcessInfo", b"processInfo")
        cls.awake = send(send(info, b"beginActivityWithOptions:reason:", 0x00EFFFFF,
                              macos.ns_string("A Bot's browser, at work"), types=(ctypes.c_ulonglong, ctypes.c_void_p)),
                         b"retain")

    @staticmethod
    def keys_to_gtk(window):
        """Keys back to GTK's widgets (a page that had them, given up when something of GTK's is clicked)."""
        ns = macos.native_window(window)
        if ns:
            macos.objc_send(ns, b"makeFirstResponder:", macos.objc_send(ns, b"contentView"), types=(ctypes.c_void_p,))

    @staticmethod
    def gtk_has_keys(window):
        """Whether GTK's widgets have the keys, not a page (a page clicked into takes them from AppKit without GTK
        knowing: its entry keeps GTK's focus)."""
        ns = macos.native_window(window)
        return not ns or macos.objc_send(ns, b"firstResponder") == macos.objc_send(ns, b"contentView")

    def focus(self):
        if self.attached is not None:
            macos.objc_send(macos.objc_send(self.view, b"window"), b"makeFirstResponder:", self.view,
                            types=(ctypes.c_void_p,))

    def attach(self):
        """Put in its window's content view, once the window's shown."""
        if self.attached is not None:
            return True
        root = self.place.get_root()
        ns = macos.native_window(root) if root is not None else None
        if not ns:
            return False
        content = macos.objc_send(ns, b"contentView")
        macos.objc_send(content, b"addSubview:", self.view, types=(ctypes.c_void_p,))
        self.attached, self.attached_root = content, root
        return True

    parked_in = None  # (the unseen window hosted pages out of sight are kept in: parking)
    GESTURES = {22: b"scrollWheel:", 30: b"magnifyWithEvent:", 31: b"swipeWithEvent:", 18: b"rotateWithEvent:",
                32: b"smartMagnifyWithEvent:"}  # (an NSEvent's type: what a view's given it with)
    gesture_to = None  # (a trackpad gesture's, from its start to its momentum's end: its page's view, or "gtk")

    @classmethod
    def take_event(cls, event):
        """A wheel's turn or a trackpad's gesture over a page (in Emission's window, or hosted in Baude's; not
        read-only), given to its view as it comes, momentum and all: GTK keeps these for its own surface, so the page
        would never see them. True if it's taken (macos.route_events)."""
        send = macos.objc_send
        kind = send(event, b"type", ret=ctypes.c_ulong)
        sel = cls.GESTURES.get(kind)
        if sel is None:
            return False
        R = macos._rect()
        phase = send(event, b"phase", ret=ctypes.c_ulong)
        momentum = send(event, b"momentumPhase", ret=ctypes.c_ulong)
        began = bool(phase & (1 | 32))  # (Began, MayBegin: a gesture's start says where all of it goes, momentum and
        if not began and (phase or momentum) and cls.gesture_to is not None:  # all, whatever's under it after)
            to = cls.gesture_to
            if to == "gtk" or to not in cls.views:
                return False
            send(to, sel, event, types=(ctypes.c_void_p,))
            return True
        f = ctypes.CFUNCTYPE(MacPoint, ctypes.c_void_p, ctypes.c_void_p)(
            ctypes.cast(send.rt.objc_msgSend, ctypes.c_void_p).value)
        at = f(event, objc_sel(b"locationInWindow"))
        ns = send(event, b"window")
        if not ns:  # (one of no window yet: its place on the screen, the window there, its place in that)
            app = send(b"NSApplication", b"sharedApplication")
            number = send(event, b"windowNumber", ret=ctypes.c_long)
            if number <= 0:
                h = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, MacPoint, ctypes.c_long)(
                    ctypes.cast(send.rt.objc_msgSend, ctypes.c_void_p).value)
                number = h(send.rt.objc_getClass(b"NSWindow"), objc_sel(b"windowNumberAtPoint:belowWindowWithWindowNumber:"),
                           at, 0)
            ns = send(app, b"windowWithWindowNumber:", number, types=(ctypes.c_long,)) if number > 0 else None
            if not ns:
                return False
            k = ctypes.CFUNCTYPE(MacPoint, ctypes.c_void_p, ctypes.c_void_p, MacPoint)(
                ctypes.cast(send.rt.objc_msgSend, ctypes.c_void_p).value)
            at = k(ns, objc_sel(b"convertPointFromScreen:"), at)
        g = ctypes.CFUNCTYPE(MacPoint, ctypes.c_void_p, ctypes.c_void_p, MacPoint, ctypes.c_void_p)(
            ctypes.cast(send.rt.objc_msgSend, ctypes.c_void_p).value)
        for view, web in list(cls.views.items()):  # (the hosted page under it, shown in that window)
            if web.attached_root in (None, "parked"):
                continue
            if send(view, b"window") != ns or send(view, b"isHidden", ret=ctypes.c_bool):
                continue
            p = g(view, objc_sel(b"convertPoint:fromView:"), at, None)
            box = send(view, b"bounds", ret=R)
            if box.x <= p.x <= box.x + box.w and box.y <= p.y <= box.y + box.h:
                if getattr(web.window, "blocked", False):  # (read-only: not the page's, nor GTK's)
                    if began:
                        cls.gesture_to = "gtk"
                    return True
                if began:
                    cls.gesture_to = view
                send(view, sel, event, types=(ctypes.c_void_p,))
                return True
        if began:
            cls.gesture_to = "gtk"
        return False

    @classmethod
    def parking(cls):
        """The content view of a window of this process's that's in, where no one sees it (see-through, clicked
        through, at the desktop's level, out of Mission Control and window cycling): a hosted page out of sight is
        kept in it, so WebKit draws it on (one hidden, or in no window, isn't drawn)."""
        send, R = macos.objc_send, macos._rect()
        if cls.parked_in is None:
            w = send(send(b"NSWindow", b"alloc"), b"initWithContentRect:styleMask:backing:defer:", R(0, 0, 1600, 1200),
                     0, 2, False, types=(R, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_bool))  # (0: borderless; 2: buffered)
            send(w, b"setReleasedWhenClosed:", False, types=(ctypes.c_bool,))
            send(w, b"setAlphaValue:", 0.0, types=(ctypes.c_double,))
            send(w, b"setIgnoresMouseEvents:", True, types=(ctypes.c_bool,))
            send(w, b"setHasShadow:", False, types=(ctypes.c_bool,))
            cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
            cg.CGWindowLevelForKey.restype, cg.CGWindowLevelForKey.argtypes = ctypes.c_int32, [ctypes.c_int32]
            send(w, b"setLevel:", cg.CGWindowLevelForKey(2), types=(ctypes.c_long,))  # (2: the desktop's)
            send(w, b"setCollectionBehavior:", (1 << 3) | (1 << 4) | (1 << 6), types=(ctypes.c_ulong,))
            send(w, b"orderWindow:relativeTo:", -1, 0, types=(ctypes.c_long, ctypes.c_long))  # (-1: below the rest)
            cls.parked_in = w
        return send(cls.parked_in, b"contentView")

    def park(self):
        """A hosted page whose place isn't shown: kept in the unseen window (parking), at the Bot's own size (its
        window's page_size: what it's laid out at when no one's looking, as an agent's is), hidden only if it isn't
        its window's selected tab."""
        content = self.parking()
        send, R = macos.objc_send, macos._rect()
        if self.attached != content:
            self.detach()
            send(content, b"addSubview:", self.view, types=(ctypes.c_void_p,))
            self.attached, self.attached_root = content, "parked"
        w, h = getattr(self.window, "fit_size", None) or getattr(self.window, "page_size", None) or (1280, 900)
        now = send(self.view, b"frame", ret=R)
        if (now.w, now.h) != (w, h):
            send(self.view, b"setFrame:", R(0, 0, w, h), types=(R,))
        send(self.view, b"setHidden:", not self.shown, types=(ctypes.c_bool,))

    def detach(self):
        """Out of the window it was in (its place moved to another's): the keys back to GTK's, if it had them."""
        if self.attached is None:
            return
        ns = macos.objc_send(self.view, b"window")
        if ns and macos.objc_send(ns, b"firstResponder") == self.view:
            macos.objc_send(ns, b"makeFirstResponder:", macos.objc_send(ns, b"contentView"), types=(ctypes.c_void_p,))
        self.block(False)
        macos.objc_send(self.view, b"removeFromSuperview")
        self.attached = self.attached_root = None

    def block(self, on):
        """Clicks, scrolls and keys kept from the page (on), by a view over it that takes them (and gives them to
        GTK's, under it), or given it again."""
        send = macos.objc_send
        if on and self.blocker is None and self.attached is not None:
            self.blocker = send(send(b"NSView", b"alloc"), b"initWithFrame:", send(self.view, b"frame",
                                                                                   ret=macos._rect()),
                                types=(macos._rect(),))
            send(self.attached, b"addSubview:positioned:relativeTo:", self.blocker, 1, self.view,
                 types=(ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p))  # (1: NSWindowAbove)
        elif not on and self.blocker is not None:
            send(self.blocker, b"removeFromSuperview")
            send(self.blocker, b"release")
            self.blocker = None

    def follow(self):
        """Its frame kept over its place (the window resized, a bar's height changed)."""
        if self.view is None:  # (closed)
            return
        root = self.place.get_root()
        hosted = getattr(self.window, "hosted", False)
        if root is None or not self.place.get_mapped():
            if hosted:  # (out of sight: drawn on, unseen)
                return self.park()
            if self.attached is not None and root is not self.attached_root:
                self.detach()
            if self.attached is not None:
                macos.objc_send(self.view, b"setHidden:", True, types=(ctypes.c_bool,))
                self.block(False)
            return
        if self.attached is not None and root is not self.attached_root:  # (shown again, or in another window)
            self.detach()
        if not self.attach():
            return
        send, R = macos.objc_send, macos._rect()
        ok, b = self.place.compute_bounds(root)
        if not ok:
            return
        cbox = send(self.attached, b"bounds", ret=R)
        flipped = send(self.attached, b"isFlipped", ret=ctypes.c_bool)
        y = b.get_y() if flipped else cbox.h - b.get_y() - b.get_height()
        frame = R(b.get_x(), y, b.get_width(), b.get_height())
        now = send(self.view, b"frame", ret=R)
        if (now.x, now.y, now.w, now.h) != (frame.x, frame.y, frame.w, frame.h):
            send(self.view, b"setFrame:", frame, types=(R,))
        dialog = getattr(root, "get_visible_dialog", None)  # (an Adw dialog in the window: drawn under the page)
        hidden = not self.shown or (dialog is not None and dialog() is not None)
        send(self.view, b"setHidden:", hidden, types=(ctypes.c_bool,))
        self.block(bool(getattr(self.window, "blocked", False)) and not hidden)
        if self.blocker is not None:
            send(self.blocker, b"setFrame:", frame, types=(R,))
        if hosted and not hidden:
            self.draw_over()

    def draw_over(self):
        """The Bot's pointer and the glow drawn over the page (its window's BotPointer), as they are now."""
        pointer = getattr(self.window, "pointer", None)
        if pointer is None:
            return
        if self.overlay is None:
            if not (pointer.shown() or pointer.acting or pointer.glow):  # (made once there's something to draw)
                return
            self.overlay = MacOverlay(self.view)
        accent = getattr(self.window, "accent", None) or (0.21, 0.52, 0.89)
        if not pointer.busy() and self.overlay.drawn is not None and self.overlay.rgb == accent:
            return  # (nothing's moved, and the accent's as it was drawn in)
        look = pointer.look()
        self.overlay.draw(look, accent, mine=look["tab"] in (None, self.tab_id))

    def show(self, on):
        self.shown = on
        if self.attached is not None:
            parked = self.attached_root == "parked"  # (out of sight but drawn: only its window's other tabs hidden)
            macos.objc_send(self.view, b"setHidden:", not on or not (parked or self.place.get_mapped()),
                            types=(ctypes.c_bool,))

    def show_failure(self, url, why):
        """The failure page in place of url's (its history entry url's, as a page that couldn't load)."""
        send = macos.objc_send
        page = macos.ns_string(failure_page(url, why))
        nsurl = send(b"NSURL", b"URLWithString:", macos.ns_string(url), types=(ctypes.c_void_p,))
        alt = objc_sel(b"_loadAlternateHTMLString:baseURL:forUnreachableURL:")
        if nsurl and send(self.view, b"respondsToSelector:", alt, types=(ctypes.c_void_p,), ret=ctypes.c_bool):
            self.failure = None
            send(self.view, b"_loadAlternateHTMLString:baseURL:forUnreachableURL:", page, None, nsurl,
                 types=(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p))
        else:
            self.failure = url
            send(self.view, b"loadHTMLString:baseURL:", page, None, types=(ctypes.c_void_p, ctypes.c_void_p))

    def load(self, url):
        send = macos.objc_send
        self.asked, self.failure = url, None
        nsurl = send(b"NSURL", b"URLWithString:", macos.ns_string(url), types=(ctypes.c_void_p,))
        if nsurl and url.startswith("file:"):
            send(self.view, b"loadFileURL:allowingReadAccessToURL:", nsurl, nsurl,  # (it reads itself, no more)
                 types=(ctypes.c_void_p, ctypes.c_void_p))
        elif nsurl:
            send(self.view, b"loadRequest:", send(b"NSURLRequest", b"requestWithURL:", nsurl,
                                                  types=(ctypes.c_void_p,)), types=(ctypes.c_void_p,))
        else:
            self.failure = url
            send(self.view, b"loadHTMLString:baseURL:", macos.ns_string(failure_page(url, "That isn't an address.")),
                 None, types=(ctypes.c_void_p, ctypes.c_void_p))

    def back(self):
        self.failure = None
        macos.objc_send(self.view, b"goBack")

    def forward(self):
        self.failure = None
        macos.objc_send(self.view, b"goForward")

    def reload(self):
        if self.failure:
            self.load(self.failure)
        else:
            macos.objc_send(self.view, b"reload")

    def stop(self):
        macos.objc_send(self.view, b"stopLoading")

    @staticmethod
    def _text(ns):
        if not ns:
            return ""
        raw = macos.objc_send(ns, b"UTF8String", ret=ctypes.c_char_p)
        return raw.decode("utf-8", "replace") if raw else ""

    def state(self):
        """{title, url, progress, loading, back, forward}: as the page is now."""
        send = macos.objc_send
        url = send(self.view, b"URL")
        return {"title": self._text(send(self.view, b"title")),
                "url": self.failure or (self._text(send(url, b"absoluteString")) if url else ""),
                "progress": send(self.view, b"estimatedProgress", ret=ctypes.c_double),
                "loading": send(self.view, b"isLoading", ret=ctypes.c_bool),
                "back": send(self.view, b"canGoBack", ret=ctypes.c_bool),
                "forward": send(self.view, b"canGoForward", ret=ctypes.c_bool)}

    # (what an agent's tools do with it: emission's Control)

    worlds = {}

    def run_js(self, body, done, page=False):
        """body (an async function's) run in the page (page: in its own world, where its scripts are; else in
        Emission's, apart from them, the same DOM): done(its result as JSON text, None) or (None, what went wrong)."""
        send = macos.objc_send
        if not self.view:  # (its tab closed meanwhile: a message to nothing would never call back)
            return done(None, "its tab's closed")
        if page:
            world = send(b"WKContentWorld", b"pageWorld")
        else:
            world = self.worlds.get("emission") or self.worlds.setdefault(
                "emission", send(send(b"WKContentWorld", b"worldWithName:", macos.ns_string("emission"),
                                      types=(ctypes.c_void_p,)), b"retain"))  # (kept: it's made once)
        code = f"return JSON.stringify(await (async () => {{ {body}\n}})() ?? null);"

        def finished(result, error):
            if error:
                info = send(error, b"userInfo")
                why = self._text(send(info, b"objectForKey:", macos.ns_string("WKJavaScriptExceptionMessage"),
                                      types=(ctypes.c_void_p,))) or self._text(send(error, b"localizedDescription"))
                GLib.idle_add(lambda: done(None, why) and False)
            else:
                text = self._text(result) if result else "null"
                GLib.idle_add(lambda: done(text, None) and False)
        send(self.view, b"callAsyncJavaScript:arguments:inFrame:inContentWorld:completionHandler:",
             macos.ns_string(code), None, None, world, Blocks.once(finished, types=(ctypes.c_void_p, ctypes.c_void_p)),
             types=(ctypes.c_void_p,) * 5)

    def snapshot(self, done):
        """The page as it's shown: done(PNG bytes, None) or (None, why not)."""
        send = macos.objc_send
        if not self.view:
            return done(None, "its tab's closed")

        def got(image, error):
            try:
                made(image, error)
            except Exception as e:  # (said, as a screenshot that failed)
                GLib.idle_add(lambda: done(None, f"{type(e).__name__}: {e}") and False)

        def made(image, error):
            if not image:
                why = self._text(send(error, b"localizedDescription")) if error else "no picture"
                GLib.idle_add(lambda: done(None, why) and False)
                return
            rep = send(b"NSBitmapImageRep", b"imageRepWithData:", send(image, b"TIFFRepresentation"),
                       types=(ctypes.c_void_p,))
            png = send(rep, b"representationUsingType:properties:", 4, send(b"NSDictionary", b"dictionary"),
                       types=(ctypes.c_ulong, ctypes.c_void_p))  # (4: PNG)
            data = ctypes.string_at(send(png, b"bytes"), send(png, b"length", ret=ctypes.c_ulong))
            GLib.idle_add(lambda: done(data, None) and False)
        send(self.view, b"takeSnapshotWithConfiguration:completionHandler:", None,
             Blocks.once(got, types=(ctypes.c_void_p, ctypes.c_void_p)), types=(ctypes.c_void_p, ctypes.c_void_p))

    cg = None

    @classmethod
    def coregraphics(cls):
        if cls.cg is None:
            cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
            cg.CGColorSpaceCreateDeviceRGB.restype = ctypes.c_void_p
            cg.CGBitmapContextCreate.restype = ctypes.c_void_p
            cg.CGBitmapContextCreate.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t,
                                                 ctypes.c_size_t, ctypes.c_void_p, ctypes.c_uint32]
            cg.CGContextDrawImage.argtypes = [ctypes.c_void_p, macos._rect(), ctypes.c_void_p]
            cg.CGBitmapContextGetData.restype = ctypes.c_void_p
            cg.CGBitmapContextGetData.argtypes = [ctypes.c_void_p]
            cg.CGContextRelease.argtypes = cg.CGColorSpaceRelease.argtypes = [ctypes.c_void_p]
            cg.CGImageGetWidth.restype = cg.CGImageGetHeight.restype = ctypes.c_size_t
            cg.CGImageGetWidth.argtypes = cg.CGImageGetHeight.argtypes = [ctypes.c_void_p]
            cls.cg = cg
        return cls.cg

    def frame(self, max_w, max_h, done):
        """The page as it's shown, as raw pixels: done(width, height, stride, RGBA bytes (premultiplied), None), at
        most max_w by max_h (its shape kept), or done(..., None, why). Drawn straight from WebKit's picture into
        a bitmap of that size: no image file made between."""
        send, cg = macos.objc_send, self.coregraphics()

        def got(image, error):
            if not image:
                why = self._text(send(error, b"localizedDescription")) if error else "no picture"
                GLib.idle_add(lambda: done(0, 0, 0, None, why) and False, priority=GLib.PRIORITY_HIGH)
                return
            cgi = send(image, b"CGImageForProposedRect:context:hints:", None, None, None,
                       types=(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p))
            iw, ih = cg.CGImageGetWidth(cgi), cg.CGImageGetHeight(cgi)
            scale = min(1.0, max_w / max(1, iw), max_h / max(1, ih))
            w, h = max(1, int(iw * scale)), max(1, int(ih * scale))
            space = cg.CGColorSpaceCreateDeviceRGB()
            ctx = cg.CGBitmapContextCreate(None, w, h, 8, w * 4, space, 1)  # (RGBA, premultiplied: kCGImageAlphaPremultipliedLast)
            cg.CGContextDrawImage(ctx, macos._rect()(0, 0, w, h), cgi)
            data = ctypes.string_at(cg.CGBitmapContextGetData(ctx), w * 4 * h)
            cg.CGContextRelease(ctx)
            cg.CGColorSpaceRelease(space)
            GLib.idle_add(lambda: done(w, h, w * 4, data, None) and False, priority=GLib.PRIORITY_HIGH)  # (not
            # behind whatever's idle: a frame waiting there kept the next from being taken)
        conf = send(send(b"WKSnapshotConfiguration", b"alloc"), b"init")
        ns = send(self.view, b"window")
        scale = send(ns, b"backingScaleFactor", ret=ctypes.c_double) if ns else 1.0
        view_w = (self.view_size() or [max_w])[0]
        width = max(100, min(view_w, max_w / max(1.0, scale)))  # (points: its pixels about max_w)
        send(conf, b"setSnapshotWidth:", send(b"NSNumber", b"numberWithDouble:", float(width),
                                              types=(ctypes.c_double,)), types=(ctypes.c_void_p,))
        send(self.view, b"takeSnapshotWithConfiguration:completionHandler:", conf,
             Blocks.once(got, types=(ctypes.c_void_p, ctypes.c_void_p)), types=(ctypes.c_void_p, ctypes.c_void_p))
        send(conf, b"release")

    def _event(self, kind, x, y, count=1, flags=0):
        """A mouse event at (x, y) on the page (CSS pixels from its top left), in its window's terms."""
        send, R = macos.objc_send, macos._rect()
        ns = send(self.view, b"window")
        fr = send(self.view, b"frame", ret=R)
        sup = send(self.view, b"superview")
        h = send(sup, b"bounds", ret=R).h
        top = fr.y if send(sup, b"isFlipped", ret=ctypes.c_bool) else h - fr.y - fr.h
        point = MacPoint(fr.x + x, h - (top + y))
        f = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, MacPoint, ctypes.c_ulong,
                             ctypes.c_double, ctypes.c_long, ctypes.c_void_p, ctypes.c_long, ctypes.c_long,
                             ctypes.c_float)(ctypes.cast(send.rt.objc_msgSend, ctypes.c_void_p).value)
        return f(send.rt.objc_getClass(b"NSEvent"), objc_sel(
            b"mouseEventWithType:location:modifierFlags:timestamp:windowNumber:context:eventNumber:clickCount:pressure:"),
            kind, point, flags, GLib.get_monotonic_time() / 1e6, send(ns, b"windowNumber", ret=ctypes.c_long), None, 0,
            count, 1.0 if kind not in (5, 6) else 0.0)

    def move(self, x, y):
        """The pointer to (x, y) on the page: what's under it hovered, as a person's mouse would."""
        macos.objc_send(self.view, b"mouseMoved:", self._event(5, x, y), types=(ctypes.c_void_p,))

    def click(self, x, y, button="left", count=1, mods=(), press=None):
        """A real click at (x, y): pointer moved there, pressed, released (count times, as a double click is; press:
        one press only, its click count that)."""
        flags = sum(MAC_MODS[m] for m in mods)
        down, up, dsel, usel = {"right": (3, 4, b"rightMouseDown:", b"rightMouseUp:"),
                                "middle": (25, 26, b"otherMouseDown:", b"otherMouseUp:")}.get(
            button, (1, 2, b"mouseDown:", b"mouseUp:"))
        self.move(x, y)
        for n in ([press] if press else range(1, count + 1)):
            macos.objc_send(self.view, dsel, self._event(down, x, y, n, flags), types=(ctypes.c_void_p,))
            macos.objc_send(self.view, usel, self._event(up, x, y, n, flags), types=(ctypes.c_void_p,))

    def view_size(self):
        """The page's size, in CSS pixels (at its zoom of 1: the view's points)."""
        fr = macos.objc_send(self.view, b"frame", ret=macos._rect()) if self.view else None
        return [round(fr.w), round(fr.h)] if fr else None

    def drag(self, points):
        """A real drag: pressed at the first point, moved through the rest, let go at the last."""
        x, y = points[0]
        self.move(x, y)
        macos.objc_send(self.view, b"mouseDown:", self._event(1, x, y), types=(ctypes.c_void_p,))
        for x, y in points[1:]:
            macos.objc_send(self.view, b"mouseDragged:", self._event(6, x, y), types=(ctypes.c_void_p,))
        macos.objc_send(self.view, b"mouseUp:", self._event(2, x, y), types=(ctypes.c_void_p,))

    bot_times = {}  # (the times, in ms, the Bot's key events were stamped with, oldest first: what GTK hears with one's a page's
    bot_n = [0]         # handing back a key of the Bot's it didn't use, dropped by Baude: page_has_keys)
    bot_text_until = 0.0  # (time.monotonic(): till then, text that comes to GTK with no key, as WebKit gives the text a
    # Bot's key made to whatever has the keys by the time it's done with it, is the Bot's: dropped)

    @classmethod
    def bot_stamp(cls):
        """A Bot's key event's timestamp: from a range a real key's never has (its ms near 2^32), so GTK's event,
        made from it, is known for the Bot's (its time those ms), however late WebKit hands it back."""
        cls.bot_n[0] = (cls.bot_n[0] + 1) % 900_000
        ms = 4_294_000_000 + cls.bot_n[0]
        for heard in (ms, ms - 1):  # (GDK's ms are the stamp's, cut down: one less, as often as not)
            cls.bot_times.pop(heard, None)
            cls.bot_times[heard] = True
        while len(cls.bot_times) > 4000:  # (the oldest let go of, however the count's come round)
            cls.bot_times.pop(next(iter(cls.bot_times)))
        return (ms + 0.5) / 1000.0

    def keys_here(self, fn):
        """fn() with the page its window's first responder: in Baude's window (hosted), whose own widgets have the
        keys, a Bot's keys (and the text they make) go to the page only while it is, given back straight after; what
        WebKit hands back after (a key the page didn't use) is dropped by Baude (bot_stamp, page_has_keys)."""
        send = macos.objc_send
        ns = send(self.view, b"window")
        before = send(ns, b"firstResponder") if ns and getattr(self.window, "hosted", False) else None
        if before is None or before == self.view:
            return fn()
        MacWeb.bot_text_until = time.monotonic() + 1.0
        send(ns, b"makeFirstResponder:", self.view, types=(ctypes.c_void_p,))
        try:
            return fn()
        finally:
            send(ns, b"makeFirstResponder:", before, types=(ctypes.c_void_p,))

    @staticmethod
    def page_has_keys(window, when, keyval):
        """Whether a key GTK hears (at when: its event's time, ms; keyval its key) isn't its own: a page has the keys
        (clicked into), it's a Bot's key a page handed back, or text a Bot's key made, come with no key of its own
        (VoidSymbol, at 0) while the Bot types. Your own keys come with theirs, and at their own times."""
        if when in MacWeb.bot_times or not MacWeb.gtk_has_keys(window):
            return True
        return keyval == 0xffffff and not when and time.monotonic() < MacWeb.bot_text_until

    def keyed(self):
        """Whether its window is the one keys go to (else a key's editing command is given it as well: a window
        that isn't key types characters, but doesn't run Return, Delete and the like)."""
        ns = macos.objc_send(self.view, b"window")
        return bool(ns) and macos.objc_send(ns, b"isKeyWindow", ret=ctypes.c_bool)

    def key(self, name, mods=()):
        """A key pressed and let go (name: a character, or Enter, Tab, Backspace, Delete, Escape, the arrows, Home,
        End, PageUp, PageDown, Space), as real key events."""
        return self.keys_here(lambda: self._key(name, mods))

    def _key(self, name, mods=()):
        send = macos.objc_send
        code, chars = MAC_KEYS.get(name, (None, name))
        if code is None and len(name) == 1:
            code = MAC_CHAR_CODES.get(name.lower(), 0)
        if code is None:
            raise ValueError(f"no key {name!r}")
        flags = sum(MAC_MODS[m] for m in mods)
        shown = chars.upper() if "shift" in mods and len(chars) == 1 and chars.isalpha() else chars
        ns = send(self.view, b"window")
        f = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, MacPoint, ctypes.c_ulong,
                             ctypes.c_double, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_bool, ctypes.c_ushort)(ctypes.cast(send.rt.objc_msgSend, ctypes.c_void_p).value)

        def ev(kind):
            return f(send.rt.objc_getClass(b"NSEvent"), objc_sel(
                b"keyEventWithType:location:modifierFlags:timestamp:windowNumber:context:characters:"
                b"charactersIgnoringModifiers:isARepeat:keyCode:"), kind, MacPoint(0, 0), flags,
                MacWeb.bot_stamp(), send(ns, b"windowNumber", ret=ctypes.c_long), None,
                macos.ns_string(shown), macos.ns_string(chars), False, code)
        command = MAC_COMMANDS.get((name, tuple(sorted(mods))))
        keyed = self.keyed()
        if "cmd" in mods:
            if keyed and send(self.view, b"performKeyEquivalent:", ev(10), types=(ctypes.c_void_p,),
                              ret=ctypes.c_bool):
                return
            if command:
                send(self.view, b"doCommandBySelector:", objc_sel(command), types=(ctypes.c_void_p,))
            return
        send(self.view, b"keyDown:", ev(10), types=(ctypes.c_void_p,))
        send(self.view, b"keyUp:", ev(11), types=(ctypes.c_void_p,))
        if command and not keyed:
            send(self.view, b"doCommandBySelector:", objc_sel(command), types=(ctypes.c_void_p,))

    def type_char(self, ch):
        """One character typed, as its key's press and release (True), or False if no key here makes it."""
        if ch == "\n":
            self.key("Enter")
            return True
        return self.keys_here(lambda: self._type_char(ch))

    def _type_char(self, ch):
        send = macos.objc_send
        base = MAC_SHIFTED.get(ch, ch.lower())
        code = 49 if ch == " " else MAC_CHAR_CODES.get(base, 0)
        flags = MAC_MODS["shift"] if ch.isupper() or ch in MAC_SHIFTED else 0
        ns = send(self.view, b"window")
        f = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, MacPoint, ctypes.c_ulong,
                             ctypes.c_double, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_bool, ctypes.c_ushort)(ctypes.cast(send.rt.objc_msgSend, ctypes.c_void_p).value)
        for kind, sel in ((10, b"keyDown:"), (11, b"keyUp:")):
            ev = f(send.rt.objc_getClass(b"NSEvent"), objc_sel(
                b"keyEventWithType:location:modifierFlags:timestamp:windowNumber:context:characters:"
                b"charactersIgnoringModifiers:isARepeat:keyCode:"), kind, MacPoint(0, 0), flags,
                MacWeb.bot_stamp(), send(ns, b"windowNumber", ret=ctypes.c_long), None,
                macos.ns_string(ch), macos.ns_string(ch.lower()), False, code)
            send(self.view, sel, ev, types=(ctypes.c_void_p,))
        return True

    def wheel(self, x, y, dx, dy, quick=False):
        """A real scroll wheel's turn over (x, y): dy pixels down (dx right), in a few steps, as a wheel's are (quick:
        in one, as a trackpad's many small ones are each)."""
        send = macos.objc_send
        cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cg.CGEventCreateScrollWheelEvent2.restype = ctypes.c_void_p
        cg.CGEventCreateScrollWheelEvent2.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                                      ctypes.c_int32, ctypes.c_int32, ctypes.c_int32]
        cg.CGEventSetLocation.argtypes = [ctypes.c_void_p, MacPoint]
        cg.CGEventSetIntegerValueField.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int64]
        cg.CFRelease.argtypes = [ctypes.c_void_p]
        ns = send(self.view, b"window")
        at = self._event(5, x, y)  # (where it is, in the window's terms: a mouse moved there)
        f = ctypes.CFUNCTYPE(MacPoint, ctypes.c_void_p, ctypes.c_void_p)(ctypes.cast(send.rt.objc_msgSend,
                                                                                    ctypes.c_void_p).value)
        here = f(at, objc_sel(b"locationInWindow"))
        g = ctypes.CFUNCTYPE(MacPoint, ctypes.c_void_p, ctypes.c_void_p, MacPoint)(ctypes.cast(send.rt.objc_msgSend,
                                                                                             ctypes.c_void_p).value)
        screen = g(ns, objc_sel(b"convertPointToScreen:"), here)
        top = send(send(send(b"NSScreen", b"screens"), b"objectAtIndex:", 0, types=(ctypes.c_ulong,)), b"frame",
                   ret=macos._rect()).h
        self.move(x, y)  # (the pointer there first: what's under it is what the wheel turns)
        steps = 1 if quick else max(1, min(12, round(max(abs(dx), abs(dy)) / 100)))
        for i, (tx, ty) in enumerate([(round(dx / steps), round(dy / steps))] * steps):
            if i:
                time.sleep(0.02)  # (a wheel's turns come apart, not at once)
            for where in (screen, here):  # (an event of no window has its screen place for its window's: then here)
                ev = cg.CGEventCreateScrollWheelEvent2(None, 0, 2, -ty, -tx, 0)
                cg.CGEventSetLocation(ev, MacPoint(where.x, top - where.y))
                for field in (91, 92):  # (the window under it, and the one to handle it: this one)
                    cg.CGEventSetIntegerValueField(ev, field, send(ns, b"windowNumber", ret=ctypes.c_long))
                made = send(b"NSEvent", b"eventWithCGEvent:", ev, types=(ctypes.c_void_p,))
                cg.CFRelease(ev)
                if where is here or send(made, b"window") == ns:
                    break
            send(self.view, b"scrollWheel:", made, types=(ctypes.c_void_p,))

    @staticmethod
    def user_agent():
        """Safari's own (this WebKit is Safari's): WKWebView's names no browser, which some sites take badly."""
        try:
            import plistlib
            with open("/Applications/Safari.app/Contents/Info.plist", "rb") as f:
                version = plistlib.load(f).get("CFBundleShortVersionString") or ""
        except (OSError, ValueError):
            version = ""
        return f"Version/{version.split('.')[0] + '.0' if version else '18.0'} Safari/605.1.15"

    def close(self):
        if self.view:
            self.block(False)
            if self.overlay is not None:
                self.overlay.close()
                self.overlay = None
            send = macos.objc_send
            self.views.pop(self.view, None)
            send(self.view, b"setNavigationDelegate:", None, types=(ctypes.c_void_p,))
            send(self.view, b"setUIDelegate:", None, types=(ctypes.c_void_p,))
            send(self.view, b"removeFromSuperview")
            send(self.view, b"release")
            self.view = None


# ------------------------------------------------------------- the Bot's pointer --

class BotPointer:
    """The Bot's pointer as it's drawn over its page, never in it (not in the page, nor in what the Bot's shown of
    it): what its tools do with the mouse (hover, click, drag, scroll; where, the page's CSS pixels) played in turn.
    It glides there; a click presses it (a ring spreads from it, one each for a double click's presses), a drag
    takes it pressed along its path, a scroll shows the wheel's way (a chevron going that way). It fades once the Bot's
    let it be a while. The glow (the page's edges in the accent) fades in while the Bot's at its tools (acting) or
    its pointer's moving (gliding, pressing, dragging, scrolling), and out once neither is, GLOW_HOLD_S after.
    look(now) says what's to be drawn then (now: time.monotonic())."""
    # (a glide's time: the shortest, the longest; about a second for 1500 px between)
    GLIDE_S = (tokens.seconds("pointer.glide-shortest"), tokens.seconds("pointer.glide-longest"))
    PRESS_S, RING_S, HINT_S = (tokens.seconds(f"pointer.{k}") for k in ("press", "ring", "hint"))
    # (nothing done this long: the pointer faded out, in this long; in, this)
    IDLE_S, FADE_S, APPEAR_S = (tokens.seconds(f"pointer.{k}") for k in ("idle", "fade", "appear"))
    GLOW_IN_S, GLOW_OUT_S, GLOW_HOLD_S = (tokens.seconds(f"pointer.{k}") for k in ("glow-in", "glow-out", "glow-hold"))
    BEHIND = tokens.number("pointer.behind")  # (more than this many steps waiting: glides quicker, so it isn't left
    # behind)
    MOST = 256  # (steps kept waiting while nothing draws them: play; a drag is some 45)

    def __init__(self):
        self.at, self.steps, self.step, self.step_t = None, [], None, 0.0
        self.pressed, self.marks, self.seen = False, [], -1e9  # (marks: rings and chevrons, as they fade)
        self.alpha, self.glow, self.acting, self.looked = 0.0, 0.0, False, None
        self.moved = -1e9  # (when its pointer last moved: the glow's held on till GLOW_HOLD_S after)
        self.tab = None  # (the tab it was last in: hosted, drawn over that tab's page only)
        self.log, self.seq = [], 0  # (what was done, numbered: watchers' to play, in the watch's state)

    def add(self, kind, x, y, tab=None, path=None, count=1, dx=0, dy=0, played=True):
        """What the Bot did: kind hover, click, drag or scroll, at x, y (a drag's path from there); played False:
        only kept for watchers (one elsewhere plays it, not this one)."""
        self.seq += 1
        ev = {"seq": self.seq, "kind": kind, "x": round(x, 1), "y": round(y, 1)}
        if path:
            ev["path"] = [[round(p[0], 1), round(p[1], 1)] for p in path[:40]]
        if count > 1:
            ev["count"] = count
        if dx or dy:
            ev["dx"], ev["dy"] = dx, dy
        self.log = (self.log + [ev])[-8:]
        if played:
            self.play(ev, tab)

    def play(self, ev, tab=None):
        """ev (add's) played here, after what's waiting."""
        self.tab = tab if tab is not None else self.tab
        x, y = float(ev["x"]), float(ev["y"])
        kind = ev.get("kind")
        if kind == "drag" and len(ev.get("path") or []) >= 2:
            path = [(float(p[0]), float(p[1])) for p in ev["path"]]
            self.steps += [("glide", path[0], 1.0), ("press", True, 0)]
            whole = sum(math.dist(a, b) for a, b in zip(path, path[1:])) or 1
            total = min(1.2, max(0.35, whole / 900))  # (the whole path in about its length's time)
            self.steps += [("glide", p, total * math.dist(a, p) / whole) for a, p in zip(path, path[1:])]
            self.steps.append(("press", False, 0))
        else:
            self.steps.append(("glide", (x, y), 1.0))
            if kind == "click":
                self.steps += [("tap", None, 1.0)] * max(1, min(3, int(ev.get("count") or 1)))
            elif kind == "scroll":
                self.steps.append(("hint", (ev.get("dx") or 0, ev.get("dy") or 0), 1.0))
        # (not being drawn, a hosted page out of sight: steps are only taken as it's looked at, so a Bot at work
        # behind a hidden Browser page only adds to them. The oldest are skipped then, the pointer where they'd have
        # left it; never while it's drawn, where a drag's steps are all played)
        if len(self.steps) > self.MOST and (self.looked is None or time.monotonic() - self.looked > 1.0):
            skipped, self.steps = self.steps[:-self.MOST], self.steps[-self.MOST:]
            for kind, what, _t in skipped:
                if kind == "glide":
                    self.at = what
                elif kind == "press":
                    self.pressed = bool(what)
            self.step = None
        self.seen = self.moved = time.monotonic()

    def set_acting(self, on):
        self.acting = bool(on)

    def busy(self):
        """Whether anything's moving, or about to (the glow's hold after the pointer's last move running out): else
        what's drawn stays as it is, till something's done or the Bot's turn changes."""
        return bool(self.steps or self.step or self.marks) or self.alpha not in (0.0, 1.0) or \
            self.glow not in (0.0, 1.0) or (self.glow > 0) != self.glowing() or (self.alpha > 0) != self.shown() or \
            time.monotonic() - self.moved < self.GLOW_HOLD_S

    def moving(self):
        return bool(self.steps or self.step or self.marks)

    def glowing(self, now=None):
        """Whether the glow's wanted: the Bot's at its tools, or its pointer's moving or just has."""
        return self.acting or self.moving() or (now or time.monotonic()) - self.moved < self.GLOW_HOLD_S

    def shown(self, now=None):
        """Whether the pointer's to be seen (it's somewhere, or about to be, and the Bot's been at it lately)."""
        return (self.at is not None or bool(self.steps)) and (
            self.acting or bool(self.steps or self.step) or (now or time.monotonic()) - self.seen < self.IDLE_S)

    def look(self, now=None):
        """What's drawn at now: {x, y (None: nowhere yet), alpha, scale (pressed: smaller), rings [(x, y, radius,
        alpha)], hints [(x, y, dx, dy, how far on, alpha)], glow}."""
        now = time.monotonic() if now is None else now
        dt = 0.0 if self.looked is None else max(0.0, min(0.1, now - self.looked))
        self.looked = now
        self.advance(now)
        want = 1.0 if self.shown(now) else 0.0
        self.alpha = min(want, self.alpha + dt / self.APPEAR_S) if want > self.alpha else \
            max(want, self.alpha - dt / self.FADE_S)
        if self.moving():
            self.moved = now
        g = 1.0 if self.glowing(now) else 0.0
        self.glow = min(g, self.glow + dt / self.GLOW_IN_S) if g > self.glow else \
            max(g, self.glow - dt / self.GLOW_OUT_S)
        x, y = self.at if self.at is not None else (None, None)
        scale = 0.82 if self.pressed else 1.0
        if self.step is not None and self.step[0] == "glide" and self.at is not None:
            (sx, sy), (tx, ty), dur = self.step[1], self.step[2], self.step[3]
            k = min(1.0, (now - self.step_t) / dur) if dur > 0 else 1.0
            e = 4 * k ** 3 if k < 0.5 else 1 - (-2 * k + 2) ** 3 / 2  # (eased in and out, as a hand moves one)
            x, y = sx + (tx - sx) * e, sy + (ty - sy) * e
        elif self.step is not None and self.step[0] == "tap":
            k = (now - self.step_t) / self.PRESS_S
            scale = 1.0 - 0.18 * math.sin(math.pi * min(1.0, k))
        rings, hints = [], []
        for kind, mx, my, t0, extra in self.marks:
            k = (now - t0) / (self.RING_S if kind == "ring" else self.HINT_S)
            if kind == "ring":
                rings.append((mx, my, 5 + 22 * (1 - (1 - k) ** 2), 0.9 * (1 - k)))
            else:
                hints.append((mx, my, extra[0], extra[1], k, 1 - k ** 2))
        self.marks = [m for m in self.marks if now - m[3] < (self.RING_S if m[0] == "ring" else self.HINT_S)]
        return {"x": x, "y": y, "alpha": self.alpha, "scale": scale, "rings": rings, "hints": hints,
                "glow": self.glow, "tab": self.tab}

    def advance(self, now):
        while True:
            if self.step is not None:
                kind = self.step[0]
                dur = self.step[3] if kind == "glide" else self.PRESS_S if kind == "tap" else 0
                if now - self.step_t < dur:
                    return
                if kind == "glide":
                    self.at = self.step[2]
                self.step = None
            if not self.steps:
                return
            kind, arg, share = self.steps.pop(0)
            quick = 0.35 if len(self.steps) > self.BEHIND else 1.0
            if kind == "glide":
                if self.at is None:  # (its first: shown there, not glided from nowhere)
                    self.at = arg
                    continue
                d = math.dist(self.at, arg)
                lo, hi = self.GLIDE_S
                dur = (min(hi, lo + d / 1500) if share == 1.0 else max(0.03, share)) * quick
                self.step = ("glide", self.at, arg, dur)
            elif kind == "press":
                self.pressed = arg
                continue
            elif kind == "tap":
                self.step = ("tap",)
                self.marks.append(("ring", *self.at, now, None))
            elif kind == "hint":
                if arg[0] or arg[1]:
                    self.marks.append(("hint", *self.at, now, arg))
                continue
            self.step_t = now


# (what's drawn over a page for the Bot's pointer, as shapes in the page's points: GTK draws them with its own paths,
# macOS's overlay with Core Animation's)

ARROW = tokens.points("pointer.arrow")  # (its tip at 0, 0)
ARROW_SHADOW = tokens.point("pointer.arrow-shadow")  # (its shadow: this far right and down)
RING_EDGE, ARROW_EDGE = tokens.px("pointer.ring-edge"), tokens.px("pointer.arrow-edge")  # (lines' widths)
HINT_EDGE = tokens.px("pointer.hint-edge")  # (a chevron's)
HINT_HALO = tokens.px("pointer.hint-halo")  # (the white round it)


def arrow_at(x, y, scale=1.0):
    """The pointer's outline, its tip at (x, y)."""
    return [(x + px * scale, y + py * scale) for px, py in ARROW]


def hint_at(x, y, dx, dy, k):
    """A scroll's chevrons for the pointer's tip at (x, y), the wheel turned (dx, dy), k of the way through: their
    way (a unit vector), and their middle: beside the pointer, off it, going that way."""
    n = math.hypot(dx, dy) or 1
    ux, uy = dx / n, dy / n
    d = 30 + 12 * k
    return (ux, uy), (x + 9 + ux * d, y + 13 + uy * d)


def chevrons(cx, cy, ux, uy):
    """Two chevrons, one after the other, at (cx, cy) pointing along (ux, uy): each a line of three points."""
    px, py = -uy, ux
    out = []
    for back in (-4.5, 3.5):
        bx, by = cx + ux * back, cy + uy * back
        out.append([(bx - ux * 4 + px * 7, by - uy * 4 + py * 7), (bx + ux * 3, by + uy * 3),
                    (bx - ux * 4 - px * 7, by - uy * 4 - py * 7)])
    return out


GLOW_STRENGTH = tokens.number("pointer.glow-strength")  # (the glow: how strong it is at the page's edge itself,
GLOW_REACH = tokens.px("pointer.glow-reach")             # 0 to 1, and how far in it reaches, in points)
GLOWS = {}  # ((size, rgb, scale): its picture's pixels, made once)


def glow_at(d, depth=GLOW_REACH):
    """The glow's strength d in from an edge: strong at it, nothing by depth in."""
    return GLOW_STRENGTH * (1 - d / depth) ** 2.4 if d < depth else 0.0


def glow_pixels(size, rgb, scale=2):
    """The glow for a page size by size points, as size * scale pixels each way (RGBA, premultiplied: a texture's,
    or a CGImage's), each pixel as strong as its two nearest edges make it together, so its corners are as soft as
    its sides. MacOverlay stretches its middle to the page's size. Made once for each accent and scale."""
    key = (size, tuple(round(c, 4) for c in rgb), scale)
    if key in GLOWS:
        return GLOWS[key]
    import array
    n = size * scale
    half = (n + 1) // 2
    edge = [glow_at(i / scale) for i in range(half)]
    r, g, b = (c * 255 for c in rgb)
    rows = []
    for y in range(half):
        ey = edge[y]
        left = array.array("I", [0]) * half
        for x in range(half):
            a = 1 - (1 - edge[x]) * (1 - ey)
            left[x] = round(r * a) | round(g * a) << 8 | round(b * a) << 16 | round(255 * a) << 24
        row = left + left[::-1][n % 2:]  # (the right half the left's, mirrored)
        if sys.byteorder != "little":
            row.byteswap()
        rows.append(row.tobytes())
    GLOWS[key] = b"".join(rows + rows[::-1][n % 2:])  # (the bottom half the top's)
    return GLOWS[key]


def glow_frame(w, h, rgb, scale=1):
    """The glow for a page w by h pixels, scale pixels a point, whole (as glow_pixels has it: what GTK draws over a
    streamed page as one texture, not stretched from pieces, which leave seams where they meet): each row its
    corners' pixels worked out, its middle's put in as one."""
    import array
    reach = min(GLOW_REACH * scale, w // 2)
    edge = [glow_at(i / scale) for i in range(reach)]
    r, g, b = (c * 255 for c in rgb)

    def pixel(a):
        return round(r * a) | round(g * a) << 8 | round(b * a) << 16 | round(255 * a) << 24
    rows = []
    for y in range((h + 1) // 2):
        ey = glow_at(y / scale)
        left = array.array("I", [pixel(1 - (1 - e) * (1 - ey)) for e in edge])
        row = left + array.array("I", [pixel(ey)]) * (w - 2 * reach) + left[::-1]
        if sys.byteorder != "little":
            row.byteswap()
        rows.append(row.tobytes())
    return b"".join(rows + rows[::-1][h % 2:])


class MacOverlay:
    """What's drawn over a hosted page on macOS (its MacWeb's view): a view of its own laid over the page, layers in
    it, that's never hit (clicks, wheels and keys go through to the page) and isn't the page's (the Bot's screenshots
    are WebKit's picture of the page alone). BotPointer's look drawn in it: the glow (a picture, its middle stretched),
    its rings, its chevrons and the pointer (Core Animation's shapes)."""
    klass = None
    kept = []
    RINGS, HINTS = 4, 3

    @classmethod
    def make_class(cls):
        if cls.klass is None:
            rt = macos.objc_send.rt
            rt.objc_allocateClassPair.restype = ctypes.c_void_p
            rt.objc_allocateClassPair.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
            rt.class_addMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p]
            rt.objc_registerClassPair.argtypes = [ctypes.c_void_p]
            klass = rt.objc_getClass(b"EmissionOverlay")
            if not klass:
                klass = rt.objc_allocateClassPair(rt.objc_getClass(b"NSView"), b"EmissionOverlay", 0)
                miss = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, MacPoint)(lambda *_: None)
                flipped = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(lambda *_: True)
                cls.kept += [miss, flipped]
                rt.class_addMethod(klass, rt.sel_registerName(b"hitTest:"), ctypes.cast(miss, ctypes.c_void_p).value,
                                   b"@@:{CGPoint=dd}")
                rt.class_addMethod(klass, rt.sel_registerName(b"isFlipped"),
                                   ctypes.cast(flipped, ctypes.c_void_p).value, b"c@:")
                rt.objc_registerClassPair(klass)
            cls.klass = klass
        return cls.klass

    cg = None

    @classmethod
    def graphics(cls):
        if cls.cg is None:
            cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
            cg.CGColorCreateSRGB.restype = ctypes.c_void_p
            cg.CGColorCreateSRGB.argtypes = [ctypes.c_double] * 4
            cg.CGColorRelease.argtypes = cg.CGPathRelease.argtypes = cg.CGImageRelease.argtypes = [ctypes.c_void_p]
            cg.CGPathCreateMutable.restype = ctypes.c_void_p
            cg.CGPathMoveToPoint.argtypes = cg.CGPathAddLineToPoint.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                                                                ctypes.c_double, ctypes.c_double]
            cg.CGPathCloseSubpath.argtypes = [ctypes.c_void_p]
            cg.CGColorSpaceCreateDeviceRGB.restype = ctypes.c_void_p
            cg.CGColorSpaceRelease.argtypes = [ctypes.c_void_p]
            cg.CGDataProviderCreateWithCFData.restype = ctypes.c_void_p
            cg.CGDataProviderCreateWithCFData.argtypes = [ctypes.c_void_p]
            cg.CGDataProviderRelease.argtypes = [ctypes.c_void_p]
            cg.CGImageCreate.restype = ctypes.c_void_p
            cg.CGImageCreate.argtypes = [ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t,
                                         ctypes.c_size_t, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                                         ctypes.c_void_p, ctypes.c_bool, ctypes.c_int]
            cg.CFDataCreate.restype = ctypes.c_void_p
            cg.CFDataCreate.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long]
            cg.CFRelease.argtypes = [ctypes.c_void_p]
            cls.cg = cg
        return cls.cg

    def __init__(self, view):
        send, R = macos.objc_send, macos._rect()
        self.view = send(send(self.make_class(), b"alloc"), b"initWithFrame:", send(view, b"bounds", ret=R), types=(R,))
        send(self.view, b"setWantsLayer:", True, types=(ctypes.c_bool,))
        send(self.view, b"setAutoresizingMask:", 2 | 16, types=(ctypes.c_ulong,))
        clip = objc_sel(b"setClipsToBounds:")  # (the pointer at the page's edge cut off there, not drawn on the frame)
        if send(self.view, b"respondsToSelector:", clip, types=(ctypes.c_void_p,), ret=ctypes.c_bool):
            send(self.view, b"setClipsToBounds:", True, types=(ctypes.c_bool,))
        send(view, b"addSubview:", self.view, types=(ctypes.c_void_p,))
        root = send(self.view, b"layer")

        def layer(kind=b"CALayer"):
            made = send(send(kind, b"alloc"), b"init")
            send(root, b"addSublayer:", made, types=(ctypes.c_void_p,))
            send(made, b"setOpacity:", ctypes.c_float(0.0), types=(ctypes.c_float,))
            return made
        self.glow = layer()
        self.rings = [layer() for _ in range(self.RINGS)]
        self.hints = [layer(b"CAShapeLayer") for _ in range(self.HINTS)]
        self.arrow = layer(b"CAShapeLayer")
        self.layers = [self.glow, *self.rings, *self.hints, self.arrow]
        self.rgb, self.scale, self.drawn, self.arrow_scale = None, None, None, None

    def set_colour(self, layer, sel, rgb, a=1.0):
        """layer's colour (sel: its setter) made rgb, a: the layer keeps it, so it's let go of here."""
        cg = self.graphics()
        c = cg.CGColorCreateSRGB(*rgb, a)
        macos.objc_send(layer, sel, c, types=(ctypes.c_void_p,))
        cg.CGColorRelease(c)

    def set_path(self, layer, lines, closed=False):
        """A shape layer's path: lines, each a list of points (closed: each a shape)."""
        cg = self.graphics()
        path = cg.CGPathCreateMutable()
        for line in lines:
            cg.CGPathMoveToPoint(path, None, *line[0])
            for p in line[1:]:
                cg.CGPathAddLineToPoint(path, None, *p)
            if closed:
                cg.CGPathCloseSubpath(path)
        macos.objc_send(layer, b"setPath:", path, types=(ctypes.c_void_p,))
        cg.CGPathRelease(path)

    def glow_image(self, rgb, scale):
        """The glow's picture (glow_pixels), a CGImage given the glow's layer (which keeps it)."""
        cg, send = self.graphics(), macos.objc_send
        size = GLOW_REACH * 2 + 4
        n = size * scale
        pixels = glow_pixels(size, rgb, scale)
        data = cg.CFDataCreate(None, pixels, len(pixels))
        provider = cg.CGDataProviderCreateWithCFData(data)
        space = cg.CGColorSpaceCreateDeviceRGB()
        image = cg.CGImageCreate(n, n, 8, 32, n * 4, space, 1, provider, None, False, 0)  # (1: premultiplied, last)
        send(self.glow, b"setContents:", image, types=(ctypes.c_void_p,))
        send(self.glow, b"setContentsScale:", float(scale), types=(ctypes.c_double,))  # (its pixels the screen's)
        R = macos._rect()
        mid = 4 / size / 2
        send(self.glow, b"setContentsCenter:", R(0.5 - mid, 0.5 - mid, 2 * mid, 2 * mid), types=(R,))
        cg.CGImageRelease(image)
        cg.CGColorSpaceRelease(space)
        cg.CGDataProviderRelease(provider)
        cg.CFRelease(data)

    def colours(self, rgb):
        """The accent's (rgb, 0-1 each) given to what's drawn in it, and the glow drawn at its window's scale."""
        send = macos.objc_send
        ns = send(self.view, b"window")
        scale = max(1, round(send(ns, b"backingScaleFactor", ret=ctypes.c_double))) if ns else 2
        if (rgb, scale) == (self.rgb, self.scale):
            return
        if scale != self.scale or rgb != self.rgb:
            self.glow_image(rgb, scale)
        self.rgb, self.scale = rgb, scale
        for ring in self.rings:
            send(ring, b"setBorderWidth:", RING_EDGE, types=(ctypes.c_double,))
            self.set_colour(ring, b"setBorderColor:", rgb)
            self.set_colour(ring, b"setBackgroundColor:", rgb, 0.22)
        for hint in self.hints:  # (white under the accent: its edge)
            self.set_colour(hint, b"setStrokeColor:", rgb)
            self.set_colour(hint, b"setFillColor:", rgb, 0.0)
            send(hint, b"setLineWidth:", HINT_EDGE, types=(ctypes.c_double,))
            for sel, name in ((b"setLineCap:", "round"), (b"setLineJoin:", "round")):
                send(hint, sel, macos.ns_string(name), types=(ctypes.c_void_p,))
            self.set_colour(hint, b"setShadowColor:", (1, 1, 1))
            send(hint, b"setShadowOpacity:", ctypes.c_float(1.0), types=(ctypes.c_float,))
            send(hint, b"setShadowRadius:", 1.2, types=(ctypes.c_double,))
            send(hint, b"setShadowOffset:", MacPoint(0, 0), types=(MacPoint,))
        self.set_colour(self.arrow, b"setFillColor:", rgb)
        self.set_colour(self.arrow, b"setStrokeColor:", (1, 1, 1))
        send(self.arrow, b"setLineWidth:", ARROW_EDGE, types=(ctypes.c_double,))
        send(self.arrow, b"setLineJoin:", macos.ns_string("round"), types=(ctypes.c_void_p,))
        self.set_colour(self.arrow, b"setShadowColor:", (0, 0, 0))
        send(self.arrow, b"setShadowOpacity:", ctypes.c_float(0.28), types=(ctypes.c_float,))
        send(self.arrow, b"setShadowRadius:", 0.6, types=(ctypes.c_double,))
        send(self.arrow, b"setShadowOffset:", MacPoint(*ARROW_SHADOW), types=(MacPoint,))
        self.drawn = None

    def draw(self, look, rgb, mine=True):
        """look (BotPointer's) drawn now; mine False: the pointer's in another tab (only the glow drawn here)."""
        send, R = macos.objc_send, macos._rect()
        self.colours(rgb)
        box = send(self.view, b"bounds", ret=R)
        key = (repr(look), mine, box.w, box.h)
        if key == self.drawn:
            return
        self.drawn = key
        f = ctypes.c_float
        send(b"CATransaction", b"begin")
        send(b"CATransaction", b"setDisableActions:", True, types=(ctypes.c_bool,))
        try:
            send(self.glow, b"setFrame:", R(0, 0, box.w, box.h), types=(R,))
            send(self.glow, b"setOpacity:", f(look["glow"]), types=(f,))
            a = look["alpha"] if mine else 0.0
            if look["x"] is not None:
                if look["scale"] != self.arrow_scale:
                    self.arrow_scale = look["scale"]
                    self.set_path(self.arrow, [arrow_at(0, 0, look["scale"])], closed=True)
                send(self.arrow, b"setPosition:", MacPoint(look["x"], look["y"]), types=(MacPoint,))
            send(self.arrow, b"setOpacity:", f(a), types=(f,))
            rings = look["rings"] if mine else []
            for i, ring in enumerate(self.rings):
                if i < len(rings):
                    x, y, r, ra = rings[i]
                    send(ring, b"setBounds:", R(0, 0, 2 * r, 2 * r), types=(R,))
                    send(ring, b"setCornerRadius:", r, types=(ctypes.c_double,))
                    send(ring, b"setPosition:", MacPoint(x, y), types=(MacPoint,))
                send(ring, b"setOpacity:", f(rings[i][3] * a if i < len(rings) else 0.0), types=(f,))
            hints = look["hints"] if mine else []
            for i, hint in enumerate(self.hints):
                if i < len(hints):
                    x, y, dx, dy, k, ha = hints[i]
                    (ux, uy), (cx, cy) = hint_at(x, y, dx, dy, k)
                    self.set_path(hint, chevrons(cx, cy, ux, uy))
                send(hint, b"setOpacity:", f(hints[i][5] * a if i < len(hints) else 0.0), types=(f,))
        finally:
            send(b"CATransaction", b"commit")

    def close(self):
        """Gone, and what it drew with let go of (its layers keep its colours, paths and picture)."""
        if self.view:
            send = macos.objc_send
            for made in self.layers:
                send(made, b"removeFromSuperlayer")
                send(made, b"release")
            self.layers = []
            send(self.view, b"removeFromSuperview")
            send(self.view, b"release")
            self.view = None


# ------------------------------------------------------------------- Linux --

X_KEYS = {"Enter": "Return", "Backspace": "BackSpace", "PageUp": "Prior", "PageDown": "Next", "Space": "space"}
X_MODS = {"shift": "Shift_L", "ctrl": "Control_L", "alt": "Alt_L", "cmd": "Control_L"}  # (cmd: Control, on Linux)


class XTest:
    """Real input on an X display (Emission's own, under xvfb-run, or the desktop's), through libXtst: the pointer
    moved, buttons and keys pressed, as the X server would have them from a mouse and keyboard."""
    made = None

    @classmethod
    def get(cls):
        if cls.made is None:
            cls.made = False
            from gi.repository import Gdk
            shown_on = Gdk.Display.get_default()
            if shown_on is not None and shown_on.__gtype__.name == "GdkX11Display" and \
                    os.environ.get("EMISSION_VIRTUAL_DISPLAY"):  # (X, and Emission's own: never someone's desktop)
                try:
                    cls.made = cls()
                except OSError:
                    pass
        return cls.made or None

    def __init__(self):
        self.x = ctypes.CDLL("libX11.so.6")
        self.t = ctypes.CDLL("libXtst.so.6")
        self.x.XOpenDisplay.restype = ctypes.c_void_p
        self.x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.d = self.x.XOpenDisplay(None)
        if not self.d:
            raise OSError("no X display")
        self.x.XDefaultRootWindow.restype = ctypes.c_ulong
        self.x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        self.x.XStringToKeysym.restype = ctypes.c_ulong
        self.x.XStringToKeysym.argtypes = [ctypes.c_char_p]
        self.x.XKeysymToKeycode.restype = ctypes.c_ubyte
        self.x.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.x.XTranslateCoordinates.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int,
                                                 ctypes.c_int, ctypes.POINTER(ctypes.c_int),
                                                 ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong)]
        self.x.XFlush.argtypes = [ctypes.c_void_p]
        for f, args in (("XTestFakeMotionEvent", [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]),
                        ("XTestFakeButtonEvent", [ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]),
                        ("XTestFakeKeyEvent", [ctypes.c_uint, ctypes.c_int, ctypes.c_ulong])):
            getattr(self.t, f).argtypes = [ctypes.c_void_p, *args]
        self.root = self.x.XDefaultRootWindow(self.d)

    def origin(self, xid):
        x, y, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
        self.x.XTranslateCoordinates(self.d, xid, self.root, 0, 0, ctypes.byref(x), ctypes.byref(y),
                                     ctypes.byref(child))
        return x.value, y.value

    def move(self, x, y):
        self.t.XTestFakeMotionEvent(self.d, -1, x, y, 0)
        self.x.XFlush(self.d)

    def button(self, n):
        self.t.XTestFakeButtonEvent(self.d, n, 1, 0)
        self.t.XTestFakeButtonEvent(self.d, n, 0, 0)
        self.x.XFlush(self.d)

    def code(self, name):
        sym = self.x.XStringToKeysym(name.encode()) if len(name) > 1 else 0
        if not sym and len(name) == 1:
            sym = ord(name) if ord(name) < 0x100 else 0x1000000 + ord(name)
        code = self.x.XKeysymToKeycode(self.d, sym) if sym else 0
        if not code:
            raise ValueError(f"no key {name!r} here")
        return code

    def shifted(self, ch):
        """Whether ch's key needs Shift for it (its keycode's first keysym another)."""
        code = self.code(ch)
        self.x.XKeycodeToKeysym.restype = ctypes.c_ulong
        self.x.XKeycodeToKeysym.argtypes = [ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_int]
        return self.x.XKeycodeToKeysym(self.d, code, 0) != (ord(ch) if ord(ch) < 0x100 else 0x1000000 + ord(ch))

    def key(self, name):
        code = self.code(name)
        self.t.XTestFakeKeyEvent(self.d, code, 1, 0)
        self.t.XTestFakeKeyEvent(self.d, code, 0, 0)
        self.x.XFlush(self.d)

    def held(self, mods):
        xt = self

        class Held:
            def __enter__(self):
                self.codes = [xt.code(X_MODS[m]) for m in mods]
                for c in self.codes:
                    xt.t.XTestFakeKeyEvent(xt.d, c, 1, 0)
                xt.x.XFlush(xt.d)

            def __exit__(self, *_):
                for c in reversed(self.codes):
                    xt.t.XTestFakeKeyEvent(xt.d, c, 0, 0)
                xt.x.XFlush(xt.d)
        return Held()


class GtkWeb:
    given_back = {}  # (sessions of Bots' profiles a window's let go of: this process's till their lock goes)
    """One page in WebKitGTK 6.0's WebView, in its place in the window, with its profile's own network session (its
    cookies, its logins, its cache: kept on disk under data_home()/profiles/<its id>, apart from others'). Its tab
    sets window, popup and closed, as MacWeb's. WebKitGTK asks for alerts, sign-ins and files itself."""
    sessions = {}
    views = {}

    @staticmethod
    def folder(profile):
        return os.path.join(data_home(), "profiles", profile)

    def __init__(self, place, profile, opener=None):
        """profile: its id. opener: the view that opened this page in a window of its own (its session, and its
        window.opener)."""
        from gi.repository import WebKit
        self.WebKit = WebKit
        self.window = self.popup = self.closed = None
        if opener is not None:
            self.view = WebKit.WebView(related_view=opener, hexpand=True, vexpand=True)
        else:
            self.view = WebKit.WebView(network_session=self.session(profile), hexpand=True, vexpand=True)
        self.views[self.view] = self
        self.view.connect("create", lambda v, _action: (new := self.popup and self.popup(v)) and new.view)
        self.view.connect("close", lambda _v: self.closed and GLib.idle_add(self.closed))
        self.view.connect("load-failed", self.failed)
        self.view.connect("script-dialog", self.script_dialog)
        self.view.connect("authenticate", self.authenticate)
        self.view.connect("run-file-chooser", self.file_chooser)
        self.held = []  # (what WebKit waits on an answer to, kept till it's given)
        if opener is None and for_bots(profile):
            self.view.get_user_content_manager().add_script(WebKit.UserScript.new(  # (the page's console kept)
                CONSOLE_JS, WebKit.UserContentInjectedFrames.TOP_FRAME, WebKit.UserScriptInjectionTime.START, None,
                None))
        self.view.get_user_content_manager().add_script(WebKit.UserScript.new_for_world(  # (Emission's own world
            "", WebKit.UserContentInjectedFrames.TOP_FRAME,  # registered: WebKitGTK keeps it, and what's set in it,
            WebKit.UserScriptInjectionTime.START, "emission", None, None))  # from one call to the next, then)
        settings = self.view.get_settings()
        if (agent := os.environ.get("EMISSION_USER_AGENT")):
            settings.set_user_agent(agent)
        place.append(self.view)

    # (a page's prompts: Emission's own, as on macOS, so a Bot's tools can see and answer them)

    def script_dialog(self, _view, dialog):
        if self.window is None:
            return False
        T = self.WebKit.ScriptDialogType
        kind = {T.ALERT: "alert", T.CONFIRM: "confirm", T.PROMPT: "prompt",
                T.BEFORE_UNLOAD_CONFIRM: "confirm"}.get(dialog.get_dialog_type(), "alert")
        self.held.append(dialog)

        def finish(answer):
            if kind == "confirm":
                dialog.confirm_set_confirmed(bool(answer))
            elif kind == "prompt" and answer is not None:
                dialog.prompt_set_text(answer)
            dialog.close()
            if dialog in self.held:
                self.held.remove(dialog)
        self.window.page_dialog(self, kind, dialog.get_message() or "",
                                dialog.prompt_get_default_text() if kind == "prompt" else "", once(finish))
        return True

    def authenticate(self, _view, request):
        if self.window is None or request.get_scheme() not in (
                self.WebKit.AuthenticationScheme.HTTP_BASIC, self.WebKit.AuthenticationScheme.HTTP_DIGEST,
                self.WebKit.AuthenticationScheme.DEFAULT):
            return False
        self.held.append(request)

        def answer(user, password):
            if user is None:
                request.cancel()
            else:
                request.authenticate(self.WebKit.Credential.new(user, password,
                                                                self.WebKit.CredentialPersistence.FOR_SESSION))
            if request in self.held:
                self.held.remove(request)
        waiting = self.window.sign_in(self, request.get_host() or "", request.get_realm() or "",
                                      request.is_retry(), once(answer))
        request.connect("cancelled", lambda *_: (self.window.done_waiting(waiting),
                                                 request in self.held and self.held.remove(request)))
        return True

    def file_chooser(self, _view, request):
        if self.window is None:
            return False
        self.held.append(request)

        def finish(paths):
            if paths:
                request.select_files(paths if request.get_select_multiple() else paths[:1])
            else:
                request.cancel()
            if request in self.held:
                self.held.remove(request)
        self.window.choose_files(request.get_select_multiple(), False, once(finish), self)
        return True

    @classmethod
    def session(cls, profile):
        """profile's network session (its cookies, logins and cache), made once in this process."""
        if profile not in cls.sessions and profile in cls.given_back:
            cls.sessions[profile] = cls.given_back.pop(profile)
        if profile not in cls.sessions:
            from gi.repository import WebKit
            root = cls.folder(profile)
            net = WebKit.NetworkSession.new(os.path.join(root, "data"), os.path.join(root, "cache"))
            net.get_cookie_manager().set_persistent_storage(os.path.join(root, "cookies.sqlite"),
                                                            WebKit.CookiePersistentStorage.SQLITE)
            net.connect("download-started", cls.download_started)
            cls.sessions[profile] = net
        return cls.sessions[profile]

    @classmethod
    def cookies(cls, profile, done):
        """profile's cookies: done([{name, value, domain, path, expires, secure, http_only, same_site}], None), or
        (None, why not)."""
        gi.require_version("Soup", "3.0")
        from gi.repository import Soup
        names = {Soup.SameSitePolicy.LAX: "lax", Soup.SameSitePolicy.STRICT: "strict"}

        def got(mgr, result):
            try:
                found = mgr.get_all_cookies_finish(result)
            except GLib.Error as e:
                done(None, e.message)
                return
            done([{"name": c.get_name(), "value": c.get_value(), "domain": c.get_domain(), "path": c.get_path(),
                   "expires": c.get_expires().to_unix() if c.get_expires() else None, "secure": c.get_secure(),
                   "http_only": c.get_http_only(), "same_site": names.get(c.get_same_site_policy())}
                  for c in found], None)
        cls.session(profile).get_cookie_manager().get_all_cookies(None, got)

    @classmethod
    def add_cookies(cls, profile, cookies, done):
        """cookies (as cookies gives them) put in profile's store: done(how many, None) or (None, why not)."""
        gi.require_version("Soup", "3.0")
        from gi.repository import Soup
        mgr, left = cls.session(profile).get_cookie_manager(), list(cookies)
        policies = {"lax": Soup.SameSitePolicy.LAX, "strict": Soup.SameSitePolicy.STRICT}

        def next_one(*result):
            if result:
                try:
                    result[0].add_cookie_finish(result[1])
                except GLib.Error as e:
                    done(None, e.message)
                    return
            if not left:
                done(len(cookies), None)
                return
            c = left.pop(0)
            made = Soup.Cookie.new(str(c["name"]), str(c.get("value") or ""), str(c["domain"]),
                                   str(c.get("path") or "/"), -1)
            if c.get("expires"):
                made.set_expires(GLib.DateTime.new_from_unix_utc(int(c["expires"])))
            made.set_secure(bool(c.get("secure")))
            made.set_http_only(bool(c.get("http_only")))
            made.set_same_site_policy(policies.get(c.get("same_site"), Soup.SameSitePolicy.NONE))
            mgr.add_cookie(made, None, next_one)
        next_one()

    @classmethod
    def remove_store(cls, profile, done):
        """profile's data deleted (cleared through its session if this process has one, then its folder), then
        done(why it couldn't be, or None)."""
        net = cls.sessions.pop(profile, None)

        def wipe():
            try:
                if os.path.exists(cls.folder(profile)):
                    shutil.rmtree(cls.folder(profile))
            except OSError as e:
                done(f"{e.filename}: {e.strerror}")
                return
            note_leftover(profile)  # (deleted again next time: its closed pages may still write here)
            done(None)
        if net is None:
            wipe()
            return
        from gi.repository import WebKit

        def cleared(mgr, result):
            try:
                mgr.clear_finish(result)
            except GLib.Error as e:
                done(e.message)
                return
            wipe()
        net.get_website_data_manager().clear(WebKit.WebsiteDataTypes.ALL, 0, None, cleared)

    @classmethod
    def download_started(cls, _session, download):
        state = {"path": None, "why": None}

        def destination(d, name):
            state["path"] = download_path(name)
            d.set_destination(state["path"])
            return True

        def failed(_d, error):
            state["why"] = error.message

        def finished(d):
            web = cls.views.get(d.get_web_view())
            if state["path"] and web is not None and web.window is not None:
                web.window.saved(state["path"], state["why"])
        download.connect("decide-destination", destination)
        download.connect("failed", failed)
        download.connect("finished", finished)

    def failed(self, view, _event, url, error):
        W = self.WebKit
        if error.matches(W.NetworkError.quark(), W.NetworkError.CANCELLED) or error.matches(
                W.PolicyError.quark(), W.PolicyError.FRAME_LOAD_INTERRUPTED_BY_POLICY_CHANGE):
            return False  # (a load stopped, or handed to a download: no failure)
        view.load_alternate_html(failure_page(url, error.message), url, None)
        return True

    def focus(self):
        if self.view is not None:
            self.view.grab_focus()

    def follow(self):
        pass  # (a widget in its place: GTK lays it out)

    def show(self, on):
        pass  # (its tab's page: shown as the tab is)

    def load(self, url):
        self.view.load_uri(url)

    def back(self):
        self.view.go_back()

    def forward(self):
        self.view.go_forward()

    def reload(self):
        self.view.reload()

    def stop(self):
        self.view.stop_loading()

    def state(self):
        v = self.view
        if v is None:  # (its tab closed: what's still waiting on it, a click's answer, is told nothing's there)
            return {}
        return {"title": v.get_title() or "", "url": v.get_uri() or "", "progress": v.get_estimated_load_progress(),
                "loading": v.is_loading(), "back": v.can_go_back(), "forward": v.can_go_forward()}

    # (what an agent's tools do with it: emission's Control)

    def run_js(self, body, done, page=False):
        """As MacWeb's: body run in the page's own world (page) or Emission's; done(JSON text, None) or (None, why)."""
        if self.view is None:
            return done(None, "its tab's closed")
        code = f"return JSON.stringify(await (async () => {{ {body}\n}})() ?? null);"

        def finished(view, result):
            try:
                value = view.call_async_javascript_function_finish(result)
            except GLib.Error as e:
                done(None, e.message)
                return
            done(value.to_string() if value is not None and not value.is_undefined() else "null", None)
        self.view.call_async_javascript_function(code, -1, None, None if page else "emission", None, None, finished)

    def snapshot(self, done):
        WebKit = self.WebKit
        if self.view is None:
            return done(None, "its tab's closed")

        def got(view, result):
            try:
                texture = view.get_snapshot_finish(result)
            except GLib.Error as e:
                done(None, e.message)
                return
            done(texture.save_to_png_bytes().get_data(), None)
        self.view.get_snapshot(WebKit.SnapshotRegion.VISIBLE, WebKit.SnapshotOptions.NONE, None, got)

    def frame(self, max_w, max_h, done):
        """As MacWeb's: the page's pixels, done(width, height, stride, RGBA bytes, None) or (..., None, why)."""
        WebKit = self.WebKit
        from gi.repository import Gdk

        def got(view, result):
            try:
                texture = view.get_snapshot_finish(result)
            except GLib.Error as e:
                done(0, 0, 0, None, e.message)
                return
            loader = Gdk.TextureDownloader.new(texture)
            loader.set_format(Gdk.MemoryFormat.R8G8B8A8_PREMULTIPLIED)
            data, stride = loader.download_bytes()
            w, h = texture.get_width(), texture.get_height()
            scale = min(1.0, max_w / max(1, w), max_h / max(1, h))
            if scale < 1.0:
                gi.require_version("GdkPixbuf", "2.0")
                from gi.repository import GdkPixbuf
                pic = GdkPixbuf.Pixbuf.new_from_bytes(data, GdkPixbuf.Colorspace.RGB, True, 8, w, h, stride)
                w, h = max(1, int(w * scale)), max(1, int(h * scale))
                pic = pic.scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR)
                data, stride = pic.read_pixel_bytes(), pic.get_rowstride()
            done(w, h, stride, data.get_data(), None)
        self.view.get_snapshot(WebKit.SnapshotRegion.VISIBLE, WebKit.SnapshotOptions.NONE, None, got)

    def _screen(self, x, y):
        """(x, y) on the page as a point of the X screen, or None where there's no X (input's then the page's own
        events, made by script)."""
        xt = XTest.get()
        root = self.view.get_root()
        if xt is None or root is None or root.get_surface() is None:
            return None
        from gi.repository import Graphene
        ok, p = self.view.compute_point(root, Graphene.Point().init(x, y))
        if not ok:
            return None
        tx, ty = root.get_surface_transform()
        try:
            gi.require_version("GdkX11", "4.0")
            from gi.repository import GdkX11
            xid = GdkX11.X11Surface.get_xid(root.get_surface())
        except (ValueError, ImportError, TypeError, AttributeError):
            return None
        ox, oy = xt.origin(xid)
        return int(ox + tx + p.x), int(oy + ty + p.y)

    def move(self, x, y):
        at = self._screen(x, y)
        if at is not None:
            XTest.get().move(*at)

    def click(self, x, y, button="left", count=1, mods=(), press=None):
        at = self._screen(x, y)
        if at is None:
            raise OSError("real clicks need Emission's own display: a Bot's Emission has one, a window on "
                          "someone's desktop doesn't")
        xt = XTest.get()
        xt.move(*at)
        with xt.held(mods):
            for _ in range(1 if press else count):  # (X counts a double click's presses itself)
                xt.button({"right": 3, "middle": 2}.get(button, 1))

    def keyed(self):
        return True

    def drag(self, points):
        xt = XTest.get()
        at = [self._screen(x, y) for x, y in points]
        if xt is None or None in at:
            raise OSError("a real drag needs Emission's own display")
        xt.move(*at[0])
        xt.t.XTestFakeButtonEvent(xt.d, 1, 1, 0)
        for p in at[1:]:
            xt.move(*p)
        xt.t.XTestFakeButtonEvent(xt.d, 1, 0, 0)
        xt.x.XFlush(xt.d)

    def view_size(self):
        return [self.view.get_width(), self.view.get_height()] if self.view is not None else [0, 0]

    def key(self, name, mods=()):
        xt = XTest.get()
        if xt is None or self._screen(0, 0) is None:
            raise OSError("real keys need Emission's own display: a Bot's Emission has one, a window on "
                          "someone's desktop doesn't")
        self.view.grab_focus()
        with xt.held(mods):
            xt.key(X_KEYS.get(name, name))

    def type_char(self, ch):
        xt = XTest.get()
        if xt is None or self._screen(0, 0) is None:
            return False
        if ch == "\n":
            self.key("Enter")
            return True
        try:
            shift = xt.shifted(ch)
        except ValueError:
            return False  # (no key on this keyboard makes it: typed another way)
        self.view.grab_focus()
        with xt.held(("shift",) if shift else ()):
            xt.key(ch)
        return True

    def wheel(self, x, y, dx, dy, quick=False):
        at = self._screen(x, y)
        if at is None:
            raise OSError("a real wheel needs Emission's own display")
        xt = XTest.get()
        xt.move(*at)
        for amount, back, on in ((dy, 4, 5), (dx, 6, 7)):  # (X's wheel: buttons 4 up, 5 down, 6 left, 7 right)
            for _ in range(min(40, max(1 if amount else 0, round(abs(amount) / 80)))):  # (a turn's about 80 pixels)
                xt.button(on if amount > 0 else back)

    @staticmethod
    def user_agent():
        return None  # (WebKitGTK's own: already Safari's kind)

    def close(self):
        """Its tab's closed: its view out of its place and disposed, once the main loop's idle (what closes it may be
        its own signal's handler). Its signals' handlers hold the view through this, and PyGObject frees no widget
        so held: disposing lets go of them, and WebKit ends its web process once no other page shares it (a page it
        opened, or was opened by, lives on in the same one). The page isn't asked first (try_close): its tab's gone
        whatever it answers, and one that asks before unloading would put a dialog up for it."""
        view, self.view = self.view, None
        if view is None:
            return
        self.views.pop(view, None)
        self.window = self.popup = self.closed = None

        def gone():
            if (parent := view.get_parent()) is not None:
                parent.remove(view)
            view.run_dispose()
            return False
        GLib.idle_add(gone)


def engine():
    """(the page engine here, None) or (None, why there's none)."""
    if sys.platform == "darwin":
        try:
            major = int((platform.mac_ver()[0] or "0").split(".")[0])
        except ValueError:
            major = 0
        if major < 14:
            return None, ("Emission needs macOS 14 or later: that's where WebKit keeps a profile's cookies and logins "
                          "apart from the others'.")
        return MacWeb, None
    try:
        gi.require_version("WebKit", "6.0")
        from gi.repository import WebKit  # noqa: F401
        return GtkWeb, None
    except (ValueError, ImportError):
        return None, ("Emission draws pages with WebKitGTK 6.0, which isn't installed here: its package is "
                      "gir1.2-webkit-6.0 on Debian and Ubuntu, webkitgtk-6.0 on Arch and Fedora.")


# ------------------------------------------------------------------ window --

class Tab:
    """A tab: its page, and where it's drawn. url None: a page another opened, WebKit loading it (opener: what the
    engine was given to make it with)."""
    made = 0

    def __init__(self, win, url, opener=None):
        Tab.made += 1
        self.id, self.win = f"tab{Tab.made}", win
        self.place = Gtk.Box(hexpand=True, vexpand=True)  # (where its page is drawn: MacWeb lays its view over it)
        self.web = win.Engine(self.place, win.profile_id, opener)
        if win.Engine is MacWeb:  # (its view kept over its place as soon as that's shown, hidden or moved: hosted, in a
            for signal in ("map", "unmap"):  # side panel that's resized and switched, every frame it's shown)
                self.place.connect(signal, lambda *_: self.web.follow())
            if win.hosted:  # (followed each frame while it's shown in Baude's window: not in its own, never shown)
                self.ticking = None
                self.place.connect("map", lambda p: setattr(self, "ticking", p.add_tick_callback(
                    lambda *_: self.web.follow() or True)))
                self.place.connect("unmap", lambda p: self.ticking is not None and (
                    p.remove_tick_callback(self.ticking), setattr(self, "ticking", None)))
        self.web.window, self.web.tab_id = win, self.id
        self.web.popup = lambda made_with: win.open(None, made_with).web
        self.web.closed = lambda: win.close_tab(self)
        if win.hosted:
            self.web.follow()  # (out of sight from the start: parked, at the Bot's own size)
        self.last = {}
        if url is not None:
            self.web.load(address(url))


class Chrome:
    """An Emission window's bars: back, forward, reload, the address, a new tab, its profile (who), and its tabs' strip
    (tab_bar: given its view), a page's progress along the strip's foot. Emission's window's, and a Bot's browser's
    window of Baude's (browserpane), driven its own way."""

    def __init__(self, profile):
        self.back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text=wording.say("browser.back"))
        self.fwd = Gtk.Button(icon_name="go-next-symbolic", tooltip_text=wording.say("browser.forward"))
        self.reload = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=wording.say("browser.reload"))
        self.entry = Gtk.Entry(hexpand=True, placeholder_text=wording.say("browser.address"))
        self.new = Gtk.Button(icon_name="tab-new-symbolic", tooltip_text=wording.say("browser.new_tab_shortcut"))
        self.who = Gtk.MenuButton(label=profile, tooltip_text="Profile")
        self.bar = Adw.HeaderBar()
        for w in (self.back, self.fwd, self.reload):
            self.bar.pack_start(w)
        self.bar.set_title_widget(self.entry)
        self.bar.pack_end(self.who)
        self.bar.pack_end(self.new)
        self.tab_bar = Adw.TabBar(autohide=False)
        self.strip = Gtk.Overlay(child=self.tab_bar)
        self.progress = Gtk.ProgressBar(visible=False, valign=Gtk.Align.END, can_target=False)
        self.progress.add_css_class("osd")
        self.strip.add_overlay(self.progress)  # (over the strip's foot: the page doesn't move as it shows and hides)

    def show(self, now, address=True):
        """now: the page in front's state (MacWeb.state's); address: its address put in the entry too (not while
        something's typed there)."""
        if address:
            self.entry.set_text(now.get("url") or "")
        self.back.set_sensitive(now.get("back", False))
        self.fwd.set_sensitive(now.get("forward", False))
        self.reload.set_icon_name("process-stop-symbolic" if now.get("loading") else "view-refresh-symbolic")
        self.progress.set_visible(now.get("loading", False))
        self.progress.set_fraction(now.get("progress") or 0)


class Window(Adw.ApplicationWindow):
    # (a watched page's frames: this often while it changes, MID_MS while it changes now and then, a caret blinking
    # say, SLOW_MS once it's still; Linux's agent draws on a virtual display, in software: half as often there)
    FAST_MS, MID_MS, SLOW_MS = (16 if sys.platform == "darwin" else 33), 100, 250
    SAME_FOR_MID = 6  # (this many frames the same in a row: the middle pace)

    def __init__(self, app, url, profile, agent=False, hosted=False):
        """agent: the window of an Emission started for a Bot's tools (its own profile; never seen: on macOS it's
        kept off the screen, put in unseen only while it's watched, on Linux it's on a display of its own; a page's
        prompts wait for the tools). hosted: an agent's, in Baude's process, its pages shown in Baude's Browser page
        (body, put there): never shown itself, no shortcuts of its own, not gone when idle, not quit when asked."""
        super().__init__(application=None if hosted else app, title="Emission", default_width=1280,
                         default_height=900)
        self.hosted, self.blocked = hosted, False  # (blocked: its pages not to be clicked or typed in, but watched)
        self.open_said = None  # (the pages it has open, as last written down for the next to find: open_pages)
        self.page_size = None  # (hosted: the Bot's own size for its pages, out of sight; None: 1280 by 900)
        self.fit_size = None  # (hosted, out of sight, watched: the watcher's size for its pages meanwhile)
        self.asked_at = GLib.get_monotonic_time()  # (the Bot's last request: how long it's left its browser alone)
        self.released = None  # (hosted: what lets its profile go when someone's Emission asks for it)
        self.note = None  # (what the Bot's next request is told: this browser's a new one, and what it had's gone)
        self.pointer = BotPointer()  # (what its tools do with the mouse: drawn over its pages while hosted, and told
        self.accent = None           # watchers; accent: the glow's colour there, Baude's, as rgb)
        if agent:
            pid = profile_id(profile) or ""
            had, why = open_pages(pid)
            self.note = pending_note(pid)  # (one a browser before it had for the Bot and never gave)
            if had:
                self.note = ((self.note + " ") if self.note else "") + (
                    ("This browser was closed after going unused for a while" if why == "idle" else
                     "This browser was restarted (Baude or its host restarted)") +
                    ": these pages it had open are closed now: " + ", ".join(had[:10]))
            pending_note(pid, self.note)
        self.Engine, why = engine()
        self.profile, self.profile_id, self.tabs = profile, profile_id(profile) or add_profile(profile), {}
        self.agent, self.control, self.waiting, self.downloads = agent, Control(self), {}, []
        self.used = GLib.get_monotonic_time()
        self.switching, self.asking, self.later, self.dialogs = False, {}, [], 0  # (later: (when, what), by tick)
        self.status, self.done = None, False
        self.watchers, self.watch_source, self.watch_capturing = [], None, False
        self.watch_fast, self.watch_fast_until, self.watch_same, self.watch_size = True, 0.0, 0, None  # (watch_size:
        # the window's size before a watcher laid its page out at its own: put back once no one watches)
        self.chrome = chrome = Chrome(profile)
        self.back_btn, self.fwd_btn, self.reload_btn, self.entry = chrome.back, chrome.fwd, chrome.reload, chrome.entry
        self.who, self.progress, bar, strip, new_tab = chrome.who, chrome.progress, chrome.bar, chrome.strip, chrome.new
        self.back_btn.connect("clicked", lambda *_: self.current() and self.current().web.back())
        self.fwd_btn.connect("clicked", lambda *_: self.current() and self.current().web.forward())
        self.reload_btn.connect("clicked", lambda *_: self.current() and (
            self.current().web.stop() if self.current().last.get("loading") else self.current().web.reload()))
        self.entry.connect("activate", lambda e: self.current() and (self.current().web.load(address(e.get_text())),
                                                                      self.current().web.focus()))
        new_tab.connect("clicked", lambda *_: self.open(HOME_PAGE))
        self.view = Adw.TabView(vexpand=True)
        chrome.tab_bar.set_view(self.view)
        if hosted:  # (in Baude's window: its tabs' keys are Baude's own tabs')
            self.view.set_shortcuts(Adw.TabViewShortcuts.NONE)
        if self.Engine is MacWeb and not (agent and not hosted):  # (a wheel's and a trackpad's gestures its pages')
            macos.route_events(MacWeb.take_event)
        self.view.connect("notify::selected-page", lambda *_: self.selected())
        self.view.connect("close-page", self.closing)
        self.banner = Adw.Banner(button_label=wording.say("common.show"))
        self.banner.connect("button-clicked", lambda *_: self.show_download())
        self.shown_path, self.banner_gen = None, 0
        self.body = body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.banner)
        frame = Adw.ToolbarView(content=body)  # (its bar: back, forward, reload, the address, its profile)
        if not agent and not hosted:  # (a window that's seen: its pages set into the frame, as Baude's and Record's)
            import widgets
            widgets.inside(body, under_bar=True)
            frame.add_css_class("baude-frame")
        frame.add_top_bar(bar)
        frame.add_top_bar(strip)
        self.set_content(frame)
        if self.Engine is None:
            self.who.set_sensitive(False)
            new_tab.set_sensitive(False)
        else:  # (the menu made as it opens: another Emission may have changed the profiles)
            self.who.set_create_popup_func(lambda b: b.set_menu_model(self.profile_menu()))
        if self.Engine is not None and not self.have(self.profile_id):
            why = f"The profile “{profile}” is open in another Emission that isn't answering. Close it, or choose " \
                  "another profile here."
        self.show_body(why)
        for name, keys, fn in (("new-tab", ["<Meta>t", "<Control>t"], lambda: self.open(HOME_PAGE)),
                               ("close-tab", ["<Meta>w", "<Control>w"], self.close_current),
                               ("address", ["<Meta>l", "<Control>l"], self.focus_address),
                               ("reload", ["<Meta>r", "<Control>r", "F5"], lambda: self.current()
                                and self.current().web.reload())):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda *_a, fn=fn: fn())
            self.add_action(act)
            if not hosted:
                app.set_accels_for_action(f"win.{name}", keys)
        if self.Engine is MacWeb and not hosted:
            clicks = Gtk.GestureClick(propagation_phase=Gtk.PropagationPhase.CAPTURE)
            clicks.connect("pressed", lambda *_: MacWeb.keys_to_gtk(self))
            self.add_controller(clicks)
        self.connect("close-request", self.closing_window)
        if self.Engine is not None:
            if not why:
                self.open(url or ("about:blank" if agent else HOME_PAGE))
            GLib.timeout_add(POLL_MS, self.tick)

    def free_from_agent(self, pid):
        """True if pid's profile was an agent's Emission's (a Bot's, out of sight) and now isn't: it's asked to go,
        so the profile can be opened here and seen (the Bot's tools then reach it here)."""
        try:
            if not call(pid, {"op": "info"}, timeout=3).get("agent"):
                return False
            call(pid, {"op": "quit"}, timeout=3)
        except (OSError, ValueError):
            return False
        end = GLib.get_monotonic_time() + 8_000_000
        while GLib.get_monotonic_time() < end:
            if lock_profile(pid):
                return True
            GLib.usleep(100_000)
        return False

    def give_back(self, pid):
        """A Bot's profile this window had, let go of (its socket now, its lock once WebKit's let go of its store):
        the Bot's own Emission can have it again."""
        ProfileSocket.close(pid)
        held = GtkWeb.sessions.pop(pid, None)
        if held is not None:
            GtkWeb.given_back[pid] = held  # (taken up again if this window comes back to it first)

        def let_go():
            if pid != self.profile_id and pid not in ProfileSocket.open:
                GtkWeb.given_back.pop(pid, None)
                unlock_profile(pid)
        self.later.append((GLib.get_monotonic_time() + 10_000_000, let_go))

    def have(self, pid):
        """pid's profile this process's (its lock, and its socket for others' pages), or False if another Emission
        has it."""
        if not lock_profile(pid):
            return False
        ProfileSocket.listen(pid, self.handle)
        return True

    def memory(self):
        """What this process holds, for a look at what its work leaves behind: its Python objects, the widgets, tabs
        and page engines among them (alive, shown or not), the pages WebKit has, and the processes those run in."""
        import gc
        gc.collect()
        things = gc.get_objects()
        engines = [o for o in things if isinstance(o, (MacWeb, GtkWeb))]
        pointers = [o for o in things if isinstance(o, BotPointer)]
        if sys.platform == "darwin":
            pids = {macos.objc_send(v, b"_webProcessIdentifier", ret=ctypes.c_int) for v in MacWeb.views}
        else:
            pids = set()
            for name in os.listdir("/proc"):
                try:
                    with open(f"/proc/{name}/stat") as f:
                        if name.isdigit() and int(f.read().rsplit(")", 1)[1].split()[1]) == os.getpid():
                            pids.add(int(name))
                except (OSError, ValueError, IndexError):
                    pass
        return {"pid": os.getpid(), "web_pids": sorted(p for p in pids if p and p > 0), "objects": len(things),
                "widgets": sum(isinstance(o, Gtk.Widget) for o in things),
                "tab_objects": sum(isinstance(o, Tab) for o in things), "engines": len(engines),
                "tabs": len(self.tabs), "views": len(MacWeb.views) + len(GtkWeb.views),
                "blocks": len(Blocks.live), "blocks_kept": len(Blocks.kept), "queue": len(self.control.queue),
                "pointer_steps": sum(len(getattr(p, "steps", ())) for p in pointers),
                "downloads": len(self.downloads), "threads": threading.active_count()}

    def handle(self, pid, msg, answer, conn=None):
        """A request at pid's socket: pages handed over (open), what this is (info), go (quit), or a Bot's tools'
        (Control's), for pid's profile (this window moved to it if it's elsewhere)."""
        op = msg.get("op")
        busy = bool(self.control.queue or self.waiting or any(t.last.get("loading") for t in self.tabs.values()))
        idle_s = 0.0 if busy else (GLib.get_monotonic_time() - self.asked_at) / 1e6  # (work still going: not idle)
        self.used = GLib.get_monotonic_time()
        if op not in ("info", "watch", "phone_note") and not msg.get("from_pane"):
            self.asked_at = self.used
        if op == "open":
            self.take(pid, [u for u in msg.get("urls") or [] if isinstance(u, str)])
            return answer({"ok": True})
        if op == "info":  # (hosted: not an agent's to be asked to go; its pages opened there instead: open)
            return answer({"agent": self.agent, "hosted": self.hosted, "profile": self.profile,
                           "tabs": len(self.tabs), "idle_s": idle_s})
        if op == "memory":  # (tests/bench_emission.py's: what this process holds now, counted after a collection)
            return answer(self.memory())
        if op == "phone_note":  # (its browser was on a phone till now: what the Bot's next request is told, in place
            said = str(msg.get("note") or "")  # of what a phone said before; "": that taken out)
            if pid != self.profile_id:
                return answer({"error": "that profile isn't open in this window"})
            last = getattr(self, "phone_said", "")
            now = self.note.replace(last, said) if last and self.note and last in self.note else " ".join(
                n for n in (self.note, said) if n)
            self.note, self.phone_said = " ".join(now.split()) or None, said
            pending_note(self.profile_id, self.note or "")
            return answer({"ok": True})
        if op == "quit" and self.hosted:  # (someone's Emission wants its profile: let go of, the Bot told after)
            answer({"ok": True})
            if self.released is not None:
                GLib.idle_add(lambda: self.released() and False)
            return None
        if op == "quit":
            answer({"ok": True})
            GLib.timeout_add(100, lambda: (self.closing_window(), self.get_application().quit()) and False)
            return None
        if self.Engine is None:
            return answer({"error": "Emission has no page engine on this host"})
        listed = next((p for p in profiles() if p["id"] == pid), None)
        if listed is None or not listed.get("bot"):  # (a Bot's tools reach a Bot's own profile, nothing else)
            return answer({"error": "Emission takes tools' requests for a Bot's own profile only"})
        if pid != self.profile_id:
            if not self.agent:  # (someone's own window, on another profile now: not moved for a Bot)
                return answer({"error": f"“{listed['name']}” is open in someone's Emission window, which shows "
                                        "another profile now: it's the Bot's again once that window's closed"})
            if not self.switch_profile(listed["name"]):
                return answer({"error": "that profile isn't open here"})
        if op == "watch":
            self.watchers.append(Watcher(self, conn, msg))
            if self.agent and not self.hosted and sys.platform == "darwin":  # (watched: its window in, where no one
                macos.in_unseen(self)  # sees it, so WebKit draws its pages as they change: a picture that moves)
            return self.watch_kick()
        if op == "resize" and self.agent and msg.get("restore"):  # (a watcher gone read-only: the Bot's own size
            if self.watch_size is not None and len(self.watchers) <= 1:  # back, unless another still drives it)
                self.set_default_size(*self.watch_size)
                self.watch_size = None
            if self.fit_size is not None and len(self.watchers) <= 1:
                self.fit_size = None
                for tab in self.tabs.values():
                    tab.web.follow()
            return answer({"ok": True})
        self.watch_kick()  # (something's being done: frames often for a while)
        if self.note and not msg.get("from_pane"):  # (the first of the Bot's requests told what became of the
            # browser it had: not Baude's Browser page's)
            note, said = self.note, answer
            self.note = None
            pending_note(self.profile_id, "")
            answer = lambda r: said({**r, "note": note} if isinstance(r, dict) else r)  # noqa: E731
        return self.control.run(op, msg, answer)

    # (watchers: Baude's Browser panes)

    def watch_kick(self):
        """Frames often (FAST_MS) for a while: the page may be changing."""
        self.watch_fast_until = time.monotonic() + 3
        self.watch_same = 0
        if self.watch_source is not None and self.watch_fast != self.FAST_MS:  # (timed slower: timed again, so what
            GLib.source_remove(self.watch_source)                             # was done shows at once)
            self.watch_source = None
        if self.watch_source is None and self.watchers:
            self.watch_fast = self.FAST_MS
            self.watch_source = GLib.timeout_add(self.FAST_MS, self.watch_tick)

    def unwatch(self, watcher):
        if watcher in self.watchers:
            self.watchers.remove(watcher)
            watcher.close()
        if not self.watchers and self.agent:
            if sys.platform == "darwin":
                macos.out_unseen(self)  # (no one watching: out again, its pages drawn only as the Bot's tools ask)
            if self.watch_size is not None:  # (the Bot's page at its own size again: a watcher's is gone with it)
                self.set_default_size(*self.watch_size)
                self.watch_size = None
            if self.fit_size is not None:
                self.fit_size = None
                for tab in self.tabs.values():
                    tab.web.follow()

    def watch_state(self):
        tab = self.current()
        return {"profile": self.profile, "agent": self.agent, "view": tab.web.view_size() if tab else None,
                "selected": tab.id if tab else None, "tabs": [self.control.describe(t) for t in self.tabs.values()],
                "prompts": self.prompts(None), "downloads": self.downloads[-5:],
                "now": tab.web.state() if tab is not None and tab.web.view is not None else None,
                "pointer": self.pointer.log}  # (now: the tab in front's back, forward and progress too)

    def watch_tick(self):
        """A frame of the page in front taken, and given to each watcher it's new to; its state too, if it's changed.
        Often while things happen, less after (SLOW_MS)."""
        if not self.watchers or self.done:
            self.watch_source = None
            return False
        state = self.watch_state()
        for w in list(self.watchers):
            if state != w.sent_state and w.say({"state": state}):
                w.sent_state = state
        if not self.watchers:
            self.watch_source = None
            return False
        if self.watch_capturing and time.monotonic() - self.watch_capturing > 5:
            self.watch_capturing = False  # (a frame that never came: the next tried)
        tab = self.current()
        if tab is not None and tab.web.view is not None and min(tab.web.view_size() or [0]) < 50:
            tab = None  # (not laid out yet: no frame of nothing)
        if tab is not None and tab.web.view is not None and not self.watch_capturing:
            self.watch_capturing = time.monotonic()
            want_w = max(w.max_w for w in self.watchers)
            want_h = max(w.max_h for w in self.watchers)

            def got(fw, fh, stride, data, why):
                self.watch_capturing = False
                if data is None:
                    return
                total = zlib.crc32(data)
                if total != getattr(self, "watch_last", None):  # (it's moving, an animation, a video: fast while it
                    self.watch_last = total                       # does)
                    self.watch_fast_until = max(self.watch_fast_until, time.monotonic() + 1)
                    self.watch_same = 0
                else:
                    self.watch_same += 1
                for w in list(self.watchers):
                    w.offer(fw, fh, stride, data, total)
            try:
                tab.web.frame(want_w, want_h, got)
            except Exception:  # (no frame this time: the next one tried)
                self.watch_capturing = False
                sys.excepthook(*sys.exc_info())
        pace = self.SLOW_MS if time.monotonic() >= self.watch_fast_until else \
            self.MID_MS if self.watch_same >= self.SAME_FOR_MID else self.FAST_MS
        if pace != self.watch_fast:  # (its pace changed: timed again)
            self.watch_fast = pace
            self.watch_source = GLib.timeout_add(pace, self.watch_tick)
            return False
        return True

    def take(self, pid, urls):
        """Pages handed over by another Emission, for pid's profile (this window moved to it if it's elsewhere)."""
        name = next((p["name"] for p in profiles() if p["id"] == pid), None)
        if name is None or (pid != self.profile_id and not self.switch_profile(name)):
            return
        for url in urls or [HOME_PAGE]:
            self.open(url)
        if not self.hosted:
            self.present()

    def show_body(self, why):
        """The pages, or (why: there can't be any) a status page saying why."""
        if why:
            if self.status is None:
                if self.view.get_parent() is not None:
                    self.body.remove(self.view)
                self.status = Adw.StatusPage(icon_name="web-browser-symbolic", title="Can't show pages",
                                             vexpand=True)
                self.body.append(self.status)
            self.status.set_description(why)
        elif self.status is not None or self.view.get_parent() is None:
            if self.status is not None:
                self.body.remove(self.status)
                self.status = None
            self.body.append(self.view)

    # (profiles)

    def profile_menu(self):
        menu, group = Gio.Menu(), Gio.SimpleActionGroup()
        listed = profiles()
        self.profile = next((p["name"] for p in listed if p["id"] == self.profile_id), self.profile)  # (renamed)
        self.who.set_label(self.profile)
        people, bot_section = Gio.Menu(), Gio.Menu()  # (the Bots' profiles apart, under their heading)
        for i, p in enumerate(listed):
            act = Gio.SimpleAction.new(f"p{i}", None)
            act.connect("activate", lambda *_a, n=p["name"]: self.switch_profile(n))
            group.add_action(act)
            (bot_section if p.get("bot") else people).append(p["name"] + ("  ✓" if p["name"] == self.profile else ""),
                                                             f"who.p{i}")
        menu.append_section(None, people)
        if bot_section.get_n_items():
            menu.append_section(wording.say("common.bots"), bot_section)
        more = Gio.Menu()
        bots = [p for p in listed if p.get("bot") and p["id"] != self.profile_id]
        mine = next((p for p in listed if p["id"] == self.profile_id), {})
        for name, label, fn, on in (("add", "New Profile…", self.ask_profile, True),
                                    ("copy", "Copy Sign-ins to a Bot…", self.ask_copy, bool(bots) and not mine.get("bot")),
                                    ("rename", "Rename Profile…", self.ask_rename, self.profile != DEFAULT
                                     and not mine.get("bot")),
                                    ("remove", "Remove Profile…", self.ask_remove, self.profile != DEFAULT)):
            act = Gio.SimpleAction.new(name, None)
            act.set_enabled(on)
            act.connect("activate", lambda *_a, fn=fn: fn())
            group.add_action(act)
            more.append(label, f"who.{name}")
        menu.append_section(None, more)
        self.insert_action_group("who", group)
        return menu

    def refresh_profiles(self):
        self.who.set_menu_model(self.profile_menu())

    def ask_name(self, heading, body, verb, start, then):
        entry = Gtk.Entry(placeholder_text=wording.say("lookup.name"), activates_default=True, text=start)
        dlg = Adw.AlertDialog(heading=heading, body=body, extra_child=entry, default_response="ok",
                              close_response="cancel")
        dlg.add_response("cancel", wording.say("common.cancel"))
        dlg.add_response("ok", verb)
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)

        def answered(_d, r):
            name = entry.get_text().strip()
            if r != "ok" or not name:
                return
            if profile_id(name) is not None:
                self.say("Name already in use", f"Choose a name other than “{name}”.")
                return
            then(name)
        dlg.connect("response", once(answered))
        self.present_dialog(dlg)

    def ask_profile(self):
        self.ask_name("New Profile", "A profile keeps its own cookies and logins: sign in once, and it stays signed "
                      "in.", wording.say("common.add"), "",
                      lambda name: (add_profile(name), self.switch_profile(name)))

    def ask_copy(self):
        """Sign-ins (the cookies of sites chosen) copied from this profile into a Bot's own: it's signed in there as
        this profile is."""
        bots = [p for p in profiles() if p.get("bot") and p["id"] != self.profile_id]

        def got(cookies, why):
            if why:
                self.say("Couldn't read the sign-ins", why)
                return
            sites = sites_of(cookies)
            if not sites:
                self.say("No sign-ins yet", "Sign in to some sites in this profile first.")
                return
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            to = Gtk.DropDown.new_from_strings([b["name"] for b in bots])
            box.append(to)
            checks = []
            scroller = Gtk.ScrolledWindow(min_content_height=min(240, 30 * len(sites)), propagate_natural_height=True)
            listing = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            for site in sites:
                c = Gtk.CheckButton(label=site)
                checks.append(c)
                listing.append(c)
            scroller.set_child(listing)
            box.append(scroller)
            dlg = Adw.AlertDialog(heading="Copy Sign-ins to a Bot", body=f"Copy your sign-ins for the chosen sites "
                                  f"from “{self.profile}” to the Bot.", extra_child=box,
                                  default_response="copy", close_response="cancel")
            dlg.add_response("cancel", wording.say("common.cancel"))
            dlg.add_response("copy", wording.say("common.copy"))
            dlg.set_response_appearance("copy", Adw.ResponseAppearance.SUGGESTED)

            def answered(_d, r):
                chosen = {c.get_label() for c in checks if c.get_active()}
                if r != "copy" or not chosen:
                    return
                bot = bots[to.get_selected()]
                picked = [c for c in cookies if set(sites_of([c])) & chosen]
                self.copy_cookies(bot, picked, sorted(chosen))
            dlg.connect("response", once(answered))
            self.present_dialog(dlg)
        self.Engine.cookies(self.profile_id, got)

    def copy_cookies(self, bot, cookies, sites):
        """cookies given to bot's own Emission (started for it, out of sight, if it isn't running: its store's open
        in one process only, its)."""
        def done(n, why):
            if why:
                self.say("Couldn't copy the sign-ins", why)
            else:
                self.say("Sign-ins copied", f"“{bot['name']}” is now signed in to {', '.join(sites)}.")
        if bot["id"] == self.profile_id or bot["id"] in ProfileSocket.open:  # (it's this window's, or this process's)
            self.Engine.add_cookies(bot["id"], cookies, done)
            return

        def over_there():
            try:
                got = call_bot(bot["bot"], None, {"op": "add_cookies", "cookies": cookies}, timeout=30)
                GLib.idle_add(lambda: done(got.get("added", 0), None) and False)
            except (OSError, ValueError) as e:
                why = str(e)  # (e's gone once this block's done: the idle call's after)
                GLib.idle_add(lambda: done(None, why) and False)
        threading.Thread(target=over_there, daemon=True).start()

    def ask_rename(self):
        def rename(name):
            rename_profile(self.profile, name)
            self.profile = name
            self.refresh_profiles()
        self.ask_name("Rename Profile", "Its cookies and logins stay with it.", wording.say("common.rename"),
                      self.profile, rename)

    def ask_remove(self):
        name = self.profile
        dlg = Adw.AlertDialog(heading=f"Remove “{name}”?",
                              body="Its cookies, logins and the sites' data it keeps are deleted. This can't be undone.",
                              default_response="cancel", close_response="cancel")
        dlg.add_response("cancel", wording.say("common.cancel"))
        dlg.add_response("remove", wording.say("common.remove"))
        dlg.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", once(lambda _d, r: r == "remove" and self.remove_profile(name)))
        self.present_dialog(dlg)

    def remove_profile(self, name):
        """name's profile gone, its data deleted: this window moved to Default first (its pages let go of it)."""
        pid = profile_id(name)
        if pid is None:
            return
        if self.profile == name and not self.switch_profile(DEFAULT):
            return
        if not self.have(pid) and not (self.free_from_agent(pid) and self.have(pid)):
            self.say(f"“{name}” is open elsewhere", "Close its window and try again.")
            return

        tries = [15]

        def removed(why):
            if why and self.Engine is MacWeb and tries[0] > 0:  # (WebKit lets go of a store a moment after its pages)
                tries[0] -= 1
                self.later.append((GLib.get_monotonic_time() + 1_000_000, lambda: MacWeb.remove_store(pid, removed)))
                return
            if why:
                if pid != self.profile_id:
                    ProfileSocket.close(pid)
                    unlock_profile(pid)
                self.say(f"Couldn't remove “{name}”", why)
                return
            forget_profile(name)
            try:
                os.unlink(os.path.join(data_home(), "profiles", pid + ".lock"))
            except OSError:
                pass
            ProfileSocket.close(pid)
            unlock_profile(pid)
            self.refresh_profiles()
        self.Engine.remove_store(pid, removed)

    def switch_profile(self, name):
        """Its tabs again, in another profile (its own cookies: a page signed in there is again there). False if
        that profile's open in another Emission."""
        if name == self.profile:
            return True
        pid = profile_id(name)
        if pid is None:
            self.say("Profile not found", f"“{name}” no longer exists.")
            return False
        if not self.have(pid) and not (self.free_from_agent(pid) and self.have(pid)):
            urls = [t.last.get("url") or HOME_PAGE for t in self.tabs.values()]
            if hand_over(pid, urls):
                self.say(f"Opened in “{name}”", f"“{name}” is already open, so the pages went there.")
            else:
                self.say(f"“{name}” isn't responding", "It's open in another Emission window.")
            return False
        urls = [t.last.get("url") or HOME_PAGE for t in self.tabs.values()]
        self.switching = True
        try:
            for page in list(self.tabs):
                self.view.close_page(page)
        finally:
            self.switching = False
        old = self.profile_id
        self.profile, self.profile_id = name, pid
        self.refresh_profiles()
        self.show_body(None)
        if not self.agent and any(p.get("bot") and p["id"] == old for p in profiles()):
            self.give_back(old)
        for url in urls or [HOME_PAGE]:
            self.open(url)
        return True

    # (asking the user: drawn by GTK in the window, so a macOS page, a view laid over it, is hidden meanwhile)

    def present_dialog(self, dlg, web=None):
        """dlg shown; web: the page it's for (its tab closed, the dialog's closed too: answered as cancelled)."""
        self.dialogs += 1
        self.show_pages()
        if web is not None:
            self.asking.setdefault(web, []).append(dlg)

        def closed(*_):
            self.dialogs -= 1
            if web is not None and dlg in self.asking.get(web, ()):
                self.asking[web].remove(dlg)
            self.show_pages()
        dlg.connect("closed", closed)
        dlg.present(self)

    def say(self, heading, body):
        dlg = Adw.AlertDialog(heading=heading, body=body)
        dlg.add_response("ok", wording.say("common.ok"))
        self.present_dialog(dlg)

    def host_of(self, web):
        url = web.state().get("url") or ""
        return urllib.parse.urlsplit(url).hostname or "This page"

    def waits(self, web, kind, answer, **about):
        """A page's prompt kept for the tools (prompts, answer_prompt): answer(msg) given what they say. Its id."""
        tab = next((t for t in self.tabs.values() if t.web is web), None)
        pid = f"prompt{len(self.waiting) + 1}-{GLib.get_monotonic_time() % 100000}"
        self.waiting[pid] = {"about": {"id": pid, "tab": tab.id if tab else None, "kind": kind, **about},
                             "answer": answer, "web": web}
        return pid

    def done_waiting(self, pid):
        self.waiting.pop(pid, None)

    def prompts(self, tab):
        """What's waiting on an answer (tab's only, if it's given)."""
        return [w["about"] for w in self.waiting.values() if tab is None or w["about"]["tab"] == tab.id]

    def answer_prompt(self, msg):
        w = self.waiting.pop(str(msg.get("id") or ""), None) or (
            self.waiting.pop(next(iter(self.waiting)), None) if len(self.waiting) == 1 and not msg.get("id") else None)
        if w is None:
            raise ValueError("no such prompt waiting: prompts lists them")
        w["answer"](msg)
        for dlg in self.asking.get(w["web"], [])[:]:
            if getattr(dlg, "prompt_id", None) == w["about"]["id"]:
                dlg.force_close()

    def page_dialog(self, web, kind, message, text, finish):
        """A page's alert, confirm or prompt: finish(None for an alert; True or False; the text, or None)."""
        pid = self.waits(web, kind, lambda m: finish(None if kind == "alert" else bool(m.get("accept", True))
                                                    if kind == "confirm" else
                                                    (str(m.get("text") or "") if m.get("accept", True) else None)),
                         message=message, **({"default": text or ""} if kind == "prompt" else {}))
        if self.agent:
            return
        dlg = Adw.AlertDialog(heading=f"{self.host_of(web)} says", body=message, close_response="cancel")
        entry = None
        if kind == "prompt":
            entry = Gtk.Entry(text=text or "", activates_default=True)
            dlg.set_extra_child(entry)
        if kind != "alert":
            dlg.add_response("cancel", wording.say("common.cancel"))
        dlg.add_response("ok", wording.say("common.ok"))
        dlg.set_default_response("ok")
        if kind == "alert":
            dlg.set_close_response("ok")
        dlg.prompt_id = pid
        dlg.connect("response", lambda _d, r: (self.done_waiting(pid), finish(
            None if kind == "alert" else (r == "ok") if kind == "confirm" else
            (entry.get_text() if r == "ok" else None))))
        self.present_dialog(dlg, web)

    def sign_in(self, web, host, realm, failed_before, answer):
        """A site's own sign-in (HTTP authentication): answer(user, password), or (None, None). Kept for the session
        only."""
        pid = self.waits(web, "sign_in", lambda m: answer(str(m.get("user") or ""), str(m.get("password") or ""))
                         if m.get("accept", True) else answer(None, None),
                         host=host, realm=realm, failed_before=bool(failed_before))
        if self.agent:
            return pid
        user = Gtk.Entry(placeholder_text=wording.say("browser.user_name"), activates_default=True)
        password = Gtk.PasswordEntry(placeholder_text=wording.say("browser.password"), activates_default=True,
                                     show_peek_icon=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(user)
        box.append(password)
        body = wording.say("browser.sign_in_realm", host=host, realm=realm) if realm else \
            wording.say("browser.sign_in", host=host)
        if failed_before:
            body = "That didn't work. " + body
        dlg = Adw.AlertDialog(heading="Sign In", body=body, extra_child=box, default_response="ok",
                              close_response="cancel")
        dlg.add_response("cancel", wording.say("common.cancel"))
        dlg.add_response("ok", "Sign In")
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.prompt_id = pid
        dlg.connect("response", lambda _d, r: (self.done_waiting(pid), answer(user.get_text(), password.get_text())
                                               if r == "ok" else answer(None, None)))
        self.present_dialog(dlg, web)
        return pid

    def choose_files(self, multiple, folders, finish, web=None):
        """Files for a page's file input: finish([paths]) or finish(None)."""
        if web is not None:
            pid = self.waits(web, "files", lambda m: finish([str(f) for f in m.get("files") or [] if os.path.exists(
                str(f))] or None if m.get("accept", True) else None), multiple=bool(multiple), folders=bool(folders))
            if self.agent:
                return
            finish = (lambda f, pid=pid: lambda paths: (self.done_waiting(pid), f(paths)))(finish)
        dialog = Gtk.FileDialog(modal=True)

        def done(d, result):
            try:
                if folders and multiple:
                    files = list(d.select_multiple_folders_finish(result))
                elif folders:
                    files = [d.select_folder_finish(result)]
                elif multiple:
                    files = list(d.open_multiple_finish(result))
                else:
                    files = [d.open_finish(result)]
            except GLib.Error:
                files = []
            finish([f.get_path() for f in files if f is not None and f.get_path()] or None)
        if folders:
            (dialog.select_multiple_folders if multiple else dialog.select_folder)(self, None, done)
        else:
            (dialog.open_multiple if multiple else dialog.open)(self, None, done)

    def saved(self, path, why):
        """A download finished (or why it didn't): said in the banner for a while, and kept for the tools."""
        name = os.path.basename(path)
        self.downloads.append({"name": name, "path": None if why else path, **({"error": why} if why else {})})
        self.banner.set_title(f"Couldn't download {name}: {why}" if why else f"Saved {name} to Downloads")
        self.banner.set_button_label(None if why else wording.say("common.show"))
        self.shown_path = None if why else path
        self.banner.set_revealed(True)
        self.banner_gen += 1
        gen = self.banner_gen
        GLib.timeout_add_seconds(8, lambda: gen == self.banner_gen and self.banner.set_revealed(False) and False)

    def show_download(self):
        if self.shown_path:
            Gtk.FileLauncher(file=Gio.File.new_for_path(self.shown_path)).open_containing_folder(self, None, None)
        self.banner.set_revealed(False)

    # (tabs)

    def focus_address(self):
        if self.Engine is MacWeb:
            MacWeb.keys_to_gtk(self)
        self.entry.grab_focus()

    def open(self, url, opener=None):
        tab = Tab(self, url, opener)
        page = self.view.append(tab.place)
        page.set_title(wording.say("browser.untitled_tab"))
        self.tabs[page] = tab
        self.view.set_selected_page(page)
        return tab

    def current(self):
        page = self.view.get_selected_page()
        return self.tabs.get(page) if page is not None else None

    def show_pages(self):
        """The selected tab's page shown, the others hidden (all of them while a dialog's up: on macOS a page is
        drawn over the window, dialogs included)."""
        for page, tab in self.tabs.items():
            tab.web.show(page is self.view.get_selected_page() and not self.dialogs)

    def selected(self):
        self.show_pages()
        if (tab := self.current()) is not None:
            self.show_state(tab, tab.last, force=True)

    def closing(self, view, page):
        tab = self.tabs.pop(page, None)
        if tab is not None:
            for dlg in self.asking.pop(tab.web, []):
                dlg.force_close()
            for pid in [k for k, w in self.waiting.items() if w["web"] is tab.web]:
                w = self.waiting.pop(pid, None)
                try:
                    w["answer"]({"accept": False})
                except Exception:  # (its page gone with it: nothing more to tell)
                    sys.excepthook(*sys.exc_info())
            tab.web.close()
            self.control.scales.pop(tab.id, None)  # (what the Bot's tools kept of it: closed by hand, too)
            self.control.reads.pop(tab.id, None)
        view.close_page_finish(page, True)
        if not self.tabs and not self.switching:
            if self.hosted:  # (in Baude, its last tab closed: a blank one in its place, as close_tab leaves)
                self.open("about:blank")
            else:
                self.close()
        return True

    def closing_window(self, *_):
        for w in list(self.watchers):
            self.unwatch(w)
        for tab in self.tabs.values():
            tab.web.close()
        self.tabs.clear()
        self.done = True
        return False

    def close_tab(self, tab):
        for page, t in self.tabs.items():
            if t is tab:
                self.view.close_page(page)
                break

    def close_current(self):
        if (page := self.view.get_selected_page()) is not None:
            self.view.close_page(page)

    def tick(self):
        if self.done:
            return False
        if self.watchers:
            self.used = GLib.get_monotonic_time()  # (watched: in use)
        if self.agent and not self.hosted and (GLib.get_monotonic_time() - self.used) / 1e6 > AGENT_IDLE_S and \
                not self.waiting:
            open_pages(self.profile_id, self.open_said or [], why="idle")
            self.closing_window()
            self.get_application().quit()
            return False
        if self.later:
            now = GLib.get_monotonic_time()
            due = [fn for when, fn in self.later if when <= now]
            self.later = [(when, fn) for when, fn in self.later if when > now]
            for fn in due:
                fn()
        if self.agent:
            urls = [u for t in self.tabs.values() if (u := t.last.get("url")) and u != "about:blank"]
            if urls != self.open_said:
                self.open_said = urls
                open_pages(self.profile_id, urls)
        for page, tab in list(self.tabs.items()):
            tab.web.follow()
            now = tab.web.state()
            if now != tab.last:
                page.set_title(now["title"] or now["url"] or wording.say("browser.untitled_tab"))
                page.set_loading(now["loading"])
                if tab is self.current():
                    self.show_state(tab, now)
                tab.last = now
        return True

    def typing(self):
        if not self.entry.get_delegate().has_focus():
            return False
        return self.Engine is not MacWeb or MacWeb.gtk_has_keys(self)

    def show_state(self, tab, now, force=False):
        if not now:
            return
        self.chrome.show(now, force or not self.typing())
        self.set_title(f"{now.get('title') or 'Emission'} — {self.profile}")


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

PAGE_JS = r"""
if (!globalThis.__em) globalThis.__em = (() => {
  const ids = new WeakMap(), refs = new Map();
  let n = 0;
  const ref = el => {
    let id = ids.get(el);
    if (!id) { id = 'ref_' + (++n); ids.set(el, id); refs.set(id, new WeakRef(el)); }
    return id;
  };
  const get = id => {
    const r = refs.get(id), el = r && r.deref();
    if (!el || !el.isConnected) throw new Error('no element ' + id + ' on the page now: read_page or find again');
    return el;
  };
  const visible = el => {
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') return false;
    const b = el.getBoundingClientRect();
    return b.width > 0 && b.height > 0;
  };
  const ROLES = {A: 'link', BUTTON: 'button', SELECT: 'combobox', TEXTAREA: 'textbox', IMG: 'img', NAV: 'navigation',
    MAIN: 'main', FORM: 'form', TABLE: 'table', LI: 'listitem', UL: 'list', OL: 'list', DIALOG: 'dialog',
    OPTION: 'option', SUMMARY: 'button', IFRAME: 'iframe', VIDEO: 'video', HEADER: 'banner', FOOTER: 'contentinfo',
    H1: 'heading', H2: 'heading', H3: 'heading', H4: 'heading', H5: 'heading', H6: 'heading'};
  const ACTS = ['link', 'button', 'textbox', 'searchbox', 'checkbox', 'radio', 'combobox', 'slider', 'option',
    'menuitem', 'tab', 'switch', 'spinbutton'];
  const role = el => {
    const r = el.getAttribute('role');
    if (r) return r;
    if (el.tagName === 'INPUT') {
      const t = (el.type || 'text').toLowerCase();
      if (t === 'hidden') return null;
      return {checkbox: 'checkbox', radio: 'radio', button: 'button', submit: 'button', reset: 'button',
        image: 'button', range: 'slider', search: 'searchbox', number: 'spinbutton', file: 'button'}[t] || 'textbox';
    }
    if (el.tagName === 'A' && !el.hasAttribute('href')) return null;
    return ROLES[el.tagName] || (el.isContentEditable && el.getAttribute('contenteditable') !== null ? 'textbox' : null);
  };
  const NAMED_BY_TEXT = ['link', 'button', 'heading', 'option', 'menuitem', 'tab', 'listitem', 'cell', 'label',
    'switch', 'checkbox', 'radio', 'treeitem', 'row', 'columnheader', 'rowheader', 'tooltip', 'status', 'alert'];
  const acts = el => ACTS.includes(role(el)) || el.hasAttribute('onclick') ||
    (el.hasAttribute('tabindex') && el.tabIndex >= 0);
  const clip = (t, n = 80) => {
    t = (t || '').replace(/\s+/g, ' ').trim();
    return t.length > n ? t.slice(0, n) + '…' : t;
  };
  const name = el => {
    const l = el.getAttribute('aria-label');
    if (l) return clip(l);
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const t = by.split(/\s+/).map(i => (document.getElementById(i) || {}).innerText || '').join(' ');
      if (t.trim()) return clip(t);
    }
    if (el.labels && el.labels.length) return clip([...el.labels].map(x => x.innerText).join(' '));
    const v = el.tagName === 'INPUT' && ['submit', 'button', 'reset'].includes(el.type) ? el.value : '';
    return clip(el.getAttribute('alt') || el.getAttribute('title') || el.getAttribute('placeholder') || v ||
      (NAMED_BY_TEXT.includes(role(el)) ? el.innerText : '') || '');
  };
  const line = (el, depth) => {
    const r = role(el);
    let s = '  '.repeat(depth) + '- ' + (r || el.tagName.toLowerCase());
    const nm = name(el);
    if (nm) s += ' "' + nm.replace(/"/g, '\\"') + '"';
    s += ' [' + ref(el) + ']';
    if (el.tagName === 'A' && el.getAttribute('href')) s += ' href="' + clip(el.getAttribute('href'), 120) + '"';
    if (['textbox', 'searchbox', 'combobox', 'slider', 'spinbutton'].includes(r) && 'value' in el && el.value)
      s += ' value="' + clip(String(el.value), 80) + '"';
    if (el.type === 'checkbox' || el.type === 'radio') s += el.checked ? ' checked' : ' unchecked';
    if (el.disabled) s += ' disabled';
    if (el === document.activeElement) s += ' focused';
    return s;
  };
  const keeps = (el, all) => all ? !!role(el) : (acts(el) || role(el) === 'heading');
  const LEAVES = ['link', 'button', 'option', 'img'];
  const up = el => el.assignedSlot || el.parentElement || (el.parentNode instanceof ShadowRoot ? el.parentNode.host
    : null);
  const boxes = () => {
    // (the rects of el's ancestors that clip it, a box it's scrolled in: only those its containing blocks are in, so
    // what's fixed is clipped only from an ancestor that holds fixed things (a transform's, a filter's, …) out, and
    // what's absolute only from its positioned ancestor out; shadow roots left through their hosts; looked up once a
    // read)
    const seen = new Map();
    const look = p => {
      if (!seen.has(p)) {
        const st = getComputedStyle(p), wc = st.willChange || '';
        seen.set(p, {pos: st.position, clip: st.overflowX === 'visible' && st.overflowY === 'visible' ? null
          : p.getBoundingClientRect(), holds: st.transform !== 'none' || st.filter !== 'none' ||
          st.perspective !== 'none' || /paint|layout|strict|content/.test(st.contain || '') ||
          /transform|filter|perspective/.test(wc)});
      }
      return seen.get(p);
    };
    return function* (el) {
      let pos = getComputedStyle(el).position;
      for (let p = up(el); p && p !== document.body && p !== document.documentElement; p = up(p)) {
        const box = look(p);
        if (pos === 'fixed' && !box.holds) continue;
        if (pos === 'absolute' && box.pos === 'static' && !box.holds) continue;
        if (box.clip) yield box.clip;
        pos = box.pos;
      }
    };
  };
  const read = (root, all, max, near) => {
    // (near: only what's within that many screens of the viewport, and of any box it's scrolled in; the rest counted,
    // above and below; null: all of root)
    const out = [], H = innerHeight, clips = boxes();
    let size = 0, above = 0, below = 0;
    let cut = false;
    const put = l => { if (size + l.length + 1 > max) { cut = true; return; } out.push(l); size += l.length + 1; };
    const where = el => {
      if (near === null) return 0;
      const b = el.getBoundingClientRect();
      if (b.bottom < -near * H) return -1;
      if (b.top > (1 + near) * H) return 1;
      for (const c of clips(el)) {
        if (b.bottom < c.top - near * c.height) return -1;
        if (b.top > c.bottom + near * c.height) return 1;
      }
      return 0;
    };
    const walk = (el, depth) => {
      if (cut || !(el instanceof Element)) return;
      if (el.matches('script, style, noscript, template, head') || !visible(el)) return;
      const r = role(el), keep = keeps(el, all), at = keep || all ? where(el) : 0;
      let d = depth;
      if (keep && at === 0) { put(line(el, all ? depth : 0)); d = depth + 1; }
      else if (keep) at < 0 ? above++ : below++;
      if (all && at === 0) {
        const own = [...el.childNodes].filter(c => c.nodeType === 3).map(c => c.textContent).join(' ');
        if (own.trim() && !(keep && name(el))) put('  '.repeat(d) + '- text "' + clip(own, 300) + '"');
      }
      if (keep && LEAVES.includes(r)) return;
      for (const c of el.children) walk(c, d);
      if (el.shadowRoot) for (const c of el.shadowRoot.children) walk(c, d);
    };
    walk(root, 0);
    return {text: out.join('\n'), above, below, cut};
  };
  const onscreen = () => {
    // (read_page's elements, links, buttons, fields and headings, in the viewport now, and not clipped out of it by a
    // box they're scrolled in: in the page's order)
    const out = [], W = innerWidth, H = innerHeight, clips = boxes();
    const shown = el => {
      const b = el.getBoundingClientRect();
      let l = Math.max(b.left, 0), t = Math.max(b.top, 0), r = Math.min(b.right, W), bt = Math.min(b.bottom, H);
      for (const c of clips(el)) {
        if (r <= l || bt <= t) break;
        l = Math.max(l, c.left); t = Math.max(t, c.top); r = Math.min(r, c.right); bt = Math.min(bt, c.bottom);
      }
      return r > l && bt > t;
    };
    const walk = el => {
      if (!(el instanceof Element) || el.matches('script, style, noscript, template, head') || !visible(el)) return;
      if (keeps(el, false)) {
        if (shown(el)) out.push(el);
        if (LEAVES.includes(role(el))) return;
      }
      for (const c of el.children) walk(c);
      if (el.shadowRoot) for (const c of el.shadowRoot.children) walk(c);
    };
    walk(document.body);
    return out;
  };
  const came = (before, most, max) => {
    // (what's in the viewport now that wasn't in before, a Set of elements: at most most lines, max characters)
    const now = onscreen().filter(el => !before.has(el)), out = [];
    let size = 0;
    for (const el of now) {
      const l = line(el, 0);
      if (out.length >= most || size + l.length > max) break;
      out.push(l);
      size += l.length + 1;
    }
    return {lines: out, more: now.length - out.length};
  };
  const find = (q, limit) => {
    q = q.toLowerCase();
    const out = [];
    for (const el of document.querySelectorAll('*')) {
      if (out.length >= limit) break;
      const r = role(el);
      if (!(r || acts(el)) || !visible(el)) continue;
      const hay = [r, name(el), el.getAttribute('placeholder'), el.id, el.getAttribute('name'),
        el.tagName === 'A' ? el.getAttribute('href') : ''].join(' ').toLowerCase();
      if (hay.includes(q)) out.push(line(el, 0));
    }
    return out;
  };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const centre = async el => {
    el.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});
    await sleep(60);
    const b = el.getBoundingClientRect(), x = b.left + b.width / 2, y = b.top + b.height / 2;
    const hit = document.elementFromPoint(x, y);
    return {x, y, covered: hit && hit !== el && !el.contains(hit) && !hit.contains(el) ? line(hit, 0) : null};
  };
  const editable = el => el && (el.isContentEditable ||
    (el.tagName === 'TEXTAREA') || (el.tagName === 'INPUT' && !['checkbox', 'radio', 'button', 'submit', 'reset',
      'file', 'image', 'range', 'color', 'hidden'].includes((el.type || 'text').toLowerCase())));
  const setValue = (el, v) => {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : el.tagName === 'SELECT'
      ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
  };
  return {ref, get, read, onscreen, came, find, centre, line, editable, setValue, sleep, visible};
})();
const em = globalThis.__em;
"""


def guarded_by(answer):
    return lambda fn: guarded(fn, answer)


def guarded(fn, answer):
    """fn, its errors answered (a callback's, which nothing else would catch: the request would wait for good)."""
    def run(*a):
        try:
            return fn(*a)
        except Exception as e:
            sys.excepthook(*sys.exc_info())
            answer({"error": f"{type(e).__name__}: {e}"})
            return None
    return run


REPL_THREW = "\u2063threw"  # (what a REPL's run answers when its text throws: its error)
REPL_RUN = """let __v;
try { __v = await (async () => {
BODY
})(); } catch (e) { return {"\\u2063threw": String(e) + (e && e.stack ? '\\n' + e.stack : '')}; }
try { JSON.stringify(__v); return __v === undefined ? null : __v; } catch (e) { return String(__v); }"""
REPL_SPLITS = 6  # (the last statements tried as the one whose value comes back)


def repl_bodies(text):
    """text, as a REPL reads it, as async function bodies to try in turn: all of it one expression (returned); then,
    from the last statement back, the statements before one and that one returned; then all of it, returning nothing.
    Statements are split at a ; or a line's end outside brackets, strings, templates and comments (one that a line
    goes on past, an operator before or after it, isn't a split)."""
    text = text.strip().rstrip(";").rstrip()
    splits, depth, i, n = [], 0, 0, len(text)
    while i < n:
        c = text[i]
        if c in "'\"`":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            continue
        if text.startswith("//", i):
            i = text.find("\n", i)
            i = n if i < 0 else i
            continue
        if text.startswith("/*", i):
            i = text.find("*/", i + 2)
            i = n if i < 0 else i + 2
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth = max(0, depth - 1)
            if c == "}" and depth == 0:
                splits.append(i + 1)
        elif depth == 0 and c == ";":
            splits.append(i + 1)
        elif depth == 0 and c == "\n":
            before, after = text[:i].rstrip()[-1:], text[i:].lstrip()[:1]
            if before and after and before not in "+-*/%&|^.,?:=<>([{!~" and after not in "+-*/%&|^.,?:=<>)]}([`":
                splits.append(i + 1)
        i += 1
    bodies, lasts = [f"return ({text}\n);"], set()
    for at in sorted(set(splits), reverse=True):
        head, last = text[:at], text[at:].strip()
        if last and last not in lasts and len(lasts) < REPL_SPLITS:
            lasts.add(last)
            bodies.append(f"{head}\nreturn ({last}\n);")
    bodies.append(text)
    return bodies


class Control:
    """What a Bot's browser tools ask of a window (emissiontools, through the host process and the socket): a request,
    answered with a dict. Input's real (the pointer moved to a place, then pressed; keys one at a time), as computer
    use's is; a page's text comes from the page itself, and what's on it is named by refs read_page and find give."""
    KEY_GAP_MS = 30  # (between keys, as a person's: kept steady)
    TYPE_GAP_MS = 12  # (between characters typed: xdotool type's own pace)
    LOAD_S = 30  # (a page's load waited for this long at most)
    JS_S = 120  # (a script's end waited for this long at most: past what a Bot's tools wait for an answer)
    SHOT_MAX = wire.CONSTANTS["SHOT_MAX"]  # (a screenshot's longest side: about XGA's, as computer use has it; zoom for
    # more)
    ZOOM_MAX = wire.CONSTANTS["ZOOM_MAX"]  # (a zoom's longest side: what a model sees whole)
    NEAR = 1  # (read_page: what's shown, and this many screens above and below it)
    CAME_LINES, CAME_CHARS = 30, 1500  # (a scroll's answer: at most this much of what came into view)

    def __init__(self, win):
        self.win = win
        self.scales = {}  # (each tab's last screenshot's pixels to the page's: by tab id)
        self.reads = {}  # (each tab's last read_page: what was asked and a hash of what it gave, by tab id)
        self.queue = []  # (input waiting its turn: (op, msg, answer), the first being done)
        self.doing = None  # (the one of them begun: next_input)

    # (tabs)

    def tab(self, msg):
        want = msg.get("tab")
        for page, t in self.win.tabs.items():
            if want in (None, "", t.id) and (want or page is self.win.view.get_selected_page()):
                self.win.view.set_selected_page(page)
                return t
        if want:
            raise ValueError(f"no tab {want}: tabs_context lists them")
        return self.win.open("about:blank")

    def describe(self, t):
        st = t.web.state()
        return {"tab": t.id, "title": st.get("title") or "", "url": st.get("url") or "",
                "loading": bool(st.get("loading")), "selected": t is self.win.current(),
                **({"prompts": got} if (got := self.win.prompts(t)) else {})}

    def settle(self, t, answer, extra=None, started=None):
        """answer(describe(t) + extra) once t's page has loaded (or LOAD_S has gone), after a moment to start."""
        started = started or GLib.get_monotonic_time()

        @guarded_by(answer)
        def look():
            st = t.web.state() if t.web.view is not None else {}
            waited = (GLib.get_monotonic_time() - started) / 1e6
            if waited < 0.35 or (st.get("loading") and waited < self.LOAD_S and not self.win.prompts(t)):
                return True
            answer({**self.describe(t), **(extra or {})})
            return False
        GLib.timeout_add(100, look)

    def js(self, t, body, answer, then=None, page=False, failed=None):
        """body run in t's page (Emission's world, PAGE_JS's em there; page: the page's own world, for javascript):
        then(result) given what it returned, or the error answered (or given to failed, if there's one)."""
        if t.web.view is None or t not in self.win.tabs.values():
            answer({"error": f"tab {t.id} has closed: tabs_context lists the open ones"})
            return
        if (waiting := [p for p in self.win.prompts(t) if p["kind"] in ("alert", "confirm", "prompt")]):
            answer({"error": "the page is waiting on its prompt, and runs nothing till it's answered (answer_prompt)",
                    "prompts": waiting})
            return

        def done(text, why):
            if why is not None:
                (failed or (lambda w: answer({"error": w})))(why)
                return
            try:
                value = json.loads(text)
            except ValueError:
                value = text
            (then or (lambda v: answer({"result": v})))(value)
        finish = once(guarded(done, answer))
        # (a script that never settles, a promise nothing resolves: said so once whoever asked has stopped waiting,
        # so its request and connection go, and input queued behind it gets its turn)
        timer = [GLib.timeout_add_seconds(self.JS_S, lambda: (
            timer.clear(), finish(None, f"the script hadn't finished after {self.JS_S} s"))[1] and False)]

        def ended(text, why):
            if timer:
                GLib.source_remove(timer.pop())
            finish(text, why)
        t.web.run_js(body if page else PAGE_JS + body, ended, page=page)

    INPUT = {"click", "drag", "hover", "type", "key", "scroll", "form_input", "navigate", "new_tab", "close_tab",
             "select_tab", "answer_prompt", "resize"}  # (what changes the page: done in the order it came)

    def run(self, op, msg, answer):
        fn = getattr(self, "op_" + str(op), None)
        if fn is None:
            raise ValueError(f"no such request: {op}")
        if op not in self.INPUT:
            return fn(msg, answer)
        self.queue.append((fn, msg, answer))
        if len(self.queue) == 1:
            self.next_input()

    def next_input(self):
        """The first of what's waiting done (the next once it's answered, whatever the answer), once (doing): this
        is asked again while it's being done, when the request after the last one answered comes before the idle
        moment that answer left (run begins it, then that moment's here)."""
        while self.queue and self.queue[0] is not self.doing:
            self.doing = self.queue[0]
            fn, msg, answer = self.queue[0]

            def done(reply, answer=answer):
                answer(reply)
                if self.queue and self.queue[0][2] is answer:
                    self.queue.pop(0)
                    GLib.idle_add(lambda: self.next_input() and False)
            try:
                fn(msg, once(done))
                return
            except Exception as e:  # (said, and the next done)
                answer({"error": str(e) if isinstance(e, (ValueError, OSError)) else f"{type(e).__name__}: {e}"})
                self.queue.pop(0)

    def op_tabs(self, msg, answer):
        answer({"profile": self.win.profile, "tabs": [self.describe(t) for t in self.win.tabs.values()],
                "downloads": self.win.downloads[-10:]})

    def op_select_tab(self, msg, answer):
        answer(self.describe(self.tab(msg)))

    def op_new_tab(self, msg, answer):
        t = self.win.open(address(msg.get("url") or "about:blank"))
        self.settle(t, answer)

    def op_close_tab(self, msg, answer):
        t = self.tab(msg)
        if len(self.win.tabs) == 1:
            self.win.open("about:blank")
        self.win.close_tab(t)
        self.reads.pop(t.id, None)
        self.scales.pop(t.id, None)
        answer({"closed": t.id, "tabs": [self.describe(x) for x in self.win.tabs.values() if x is not t]})

    def op_navigate(self, msg, answer):
        t, url = self.tab(msg), msg.get("url") or ""
        {"back": t.web.back, "forward": t.web.forward, "reload": t.web.reload}.get(
            url, lambda: t.web.load(address(url)))()
        self.settle(t, answer)

    # (looking)

    def picture(self, t, answer, then):
        """then(pixbuf, view) for t's page as it's shown (view: its size, CSS pixels; the pixbuf may have more)."""
        def sized(view):
            def shot(png, why):
                if png is None:
                    answer({"error": f"no screenshot: {why}"})
                    return
                gi.require_version("GdkPixbuf", "2.0")
                from gi.repository import GdkPixbuf
                loader = GdkPixbuf.PixbufLoader.new_with_type("png")
                loader.write(png)
                loader.close()
                then(loader.get_pixbuf(), {"w": max(1, int(view["w"])), "h": max(1, int(view["h"]))})
            GLib.timeout_add(120, guarded(lambda: t.web.snapshot(guarded(shot, answer)) and False, answer))
        self.js(t, "return {w: innerWidth, h: innerHeight}", answer, sized)

    @staticmethod
    def jpeg_of(pic, x, y, w, h, pw, ph, quality):
        """pic's part (x, y, w, h: its pixels) as a pw by ph JPEG, on white (JPEG has no alpha)."""
        from gi.repository import GdkPixbuf
        flat = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, pw, ph)
        flat.fill(0xffffffff)
        pic.composite(flat, 0, 0, pw, ph, -x * pw / w, -y * ph / h, pw / w, ph / h, GdkPixbuf.InterpType.BILINEAR, 255)
        return base64.b64encode(flat.save_to_bufferv("jpeg", ["quality"], [str(quality)])[1]).decode()

    def op_screenshot(self, msg, answer):
        t = self.tab(msg)

        def shot(pic, view):
            w, h = view["w"], view["h"]
            scale = min(1.0, self.SHOT_MAX / max(w, h))
            pw, ph = max(1, round(w * scale)), max(1, round(h * scale))
            image = self.jpeg_of(pic, 0, 0, pic.get_width(), pic.get_height(), pw, ph,
                                 wire.CONSTANTS["SHOT_QUALITY"])
            self.scales[t.id] = scale
            answer({**self.describe(t), "image": image, "mime": "image/jpeg", "width": pw, "height": ph,
                    "note": "x and y for click, scroll and zoom are this picture's pixels"})
        self.picture(t, answer, shot)

    def op_zoom(self, msg, answer):
        """A region of the last screenshot ([x0, y0, x1, y1], its pixels), at the page's own detail: what x and y
        are in stays the screenshot's."""
        t = self.tab(msg)
        region = msg.get("region")
        if not (isinstance(region, list) and len(region) == 4 and all(isinstance(v, (int, float)) for v in region)
                and region[2] > region[0] and region[3] > region[1]):
            raise ValueError("zoom needs a region: [x0, y0, x1, y1], the screenshot's pixels, x1 past x0, y1 past y0")

        if t.id not in self.scales:
            raise ValueError("zoom is of a screenshot's region: take one first")

        def shot(pic, view):
            w, h = view["w"], view["h"]
            scale = self.scales[t.id]
            k = pic.get_width() / w  # (the picture's pixels to the page's CSS ones: 2 on a Retina display)
            x0, y0 = max(0.0, region[0] / scale), max(0.0, region[1] / scale)
            x1, y1 = min(float(w), region[2] / scale), min(float(h), region[3] / scale)
            if x1 - x0 < 1 or y1 - y0 < 1:
                return answer({"error": f"that region is off the page (the screenshot is {round(w * scale)} by "
                                        f"{round(h * scale)})"})
            px, py, pw, ph = x0 * k, y0 * k, (x1 - x0) * k, (y1 - y0) * k
            fit = min(1.0, self.ZOOM_MAX / max(pw, ph))
            ow, oh = max(1, round(pw * fit)), max(1, round(ph * fit))
            image = self.jpeg_of(pic, px, py, pw, ph, ow, oh, wire.CONSTANTS["ZOOM_QUALITY"])
            answer({**self.describe(t), "image": image, "mime": "image/jpeg",
                    "width": ow, "height": oh, "region": [round(v) for v in region],
                    "note": "x and y for click and scroll are still the screenshot's pixels"})
        self.picture(t, answer, shot)

    def op_read_page(self, msg, answer):
        """The page's elements near what's shown (all of ref's part, if a ref's given); or, if nothing's changed
        since this tab's last read (its address, and what it gave), a line saying so: once, the next read giving
        them again."""
        t = self.tab(msg)
        root = f"em.get({json.dumps(msg['ref'])})" if msg.get("ref") else "document.body"
        near = None if msg.get("ref") else self.NEAR

        def read(got):
            text = got["text"]
            if got.get("cut"):
                text += "\n(… cut short: read a part with ref_id, or look with find)"
            off = [f"{n} {side}" for n, side in ((got.get("above"), "above"), (got.get("below"), "below")) if n]
            if off:
                text += (f"\n(Not listed: {' and '.join(off).replace(' ', ' elements ', 1)}, more than a screen "
                         "from what's shown. Scroll, or find, reaches them.)")
            asked = (t.web.state().get("url") or "", msg.get("filter") or "", msg.get("ref") or "",
                     msg.get("max_chars") or 0)
            seen = (asked, hashlib.sha1(text.encode()).hexdigest())
            if self.reads.get(t.id) == seen:
                del self.reads[t.id]
                return answer({**self.describe(t), "page": "Unchanged since your last read_page of this tab. (Read "
                                                           "again if you no longer have it.)"})
            self.reads[t.id] = seen
            answer({**self.describe(t), "page": text})
        self.js(t, f"return em.read({root}, {json.dumps(msg.get('filter') == 'all')}, "
                   f"{int(msg.get('max_chars') or 30000)}, {json.dumps(near)})", answer, read)

    def op_find(self, msg, answer):
        t = self.tab(msg)
        if not str(msg.get("query") or "").strip():
            raise ValueError("find needs a query")
        self.js(t, f"return em.find({json.dumps(str(msg['query']))}, {int(msg.get('limit') or 20)})",
                answer, lambda found: answer({**self.describe(t), "found": found}))

    def op_get_text(self, msg, answer):
        t = self.tab(msg)
        cap = int(msg.get("max_chars") or 50000)
        self.js(t, f"const el = document.querySelector('main, article, [role=main]') || document.body;"
                   f"const t = (el.innerText || ''); return t.length > {cap} ? t.slice(0, {cap}) + "
                   f"'\\n(… ' + (t.length - {cap}) + ' more characters)' : t", answer,
                lambda text: answer({**self.describe(t), "text": text}))

    def op_javascript(self, msg, answer):
        """text run in the page's own world as a REPL runs it (its last expression's value back, await anywhere): the
        first of repl_bodies' readings of it that compiles, run (compiled in Emission's world first, where the page's
        rules on scripts don't reach, without running: a syntax error's left out of the page's console); what it
        throws said as the error. code: an async function's body, run as it is."""
        t = self.tab(msg)
        if "text" not in msg:
            return self.js(t, str(msg.get("code") or ""), answer,
                           lambda value: answer({**self.describe(t), "result": value}), page=True)
        bodies = repl_bodies(str(msg.get("text") or ""))

        def ran(value):
            if isinstance(value, dict) and set(value) == {REPL_THREW}:
                return answer({"error": value[REPL_THREW]})
            answer({**self.describe(t), "result": value})

        def run(i):
            self.js(t, REPL_RUN.replace("BODY", bodies[i]), answer, ran, page=True)

        def tried(i):  # (where it couldn't be compiled there, each reading run in turn until one isn't a SyntaxError)
            self.js(t, REPL_RUN.replace("BODY", bodies[i]), answer, ran, page=True, failed=lambda why: (
                tried(i + 1) if "SyntaxError" in str(why) and i + 1 < len(bodies) else answer({"error": why})))

        def chose(got):
            if got.get("at") is not None:
                return run(got["at"])
            if got.get("unchecked"):
                return tried(0)
            answer({"error": got.get("why") or "SyntaxError"})
        self.js(t, f"const bodies = {json.dumps(bodies)}, AF = (async () => {{}}).constructor; let why = null;"
                   "for (let i = 0; i < bodies.length; i++) {"
                   "  try { new AF(bodies[i]); return {at: i}; } catch (e) {"
                   "    if (!(e instanceof SyntaxError)) return {unchecked: true}; why = String(e); } }"
                   "return {why}", answer, chose)

    # (doing)

    def point(self, t, msg, answer, then):
        """then(x, y, covered) for a ref's centre (scrolled to first), or for x and y as the last screenshot had them."""
        if msg.get("ref"):
            self.js(t, f"return await em.centre(em.get({json.dumps(msg['ref'])}))", answer,
                    lambda c: then(c["x"], c["y"], c.get("covered")))
        elif msg.get("x") is not None and msg.get("y") is not None:
            scale = 1.0 if msg.get("css") else self.scales.get(t.id, 1.0)  # (css: the page's own pixels)
            then(float(msg["x"]) / scale, float(msg["y"]) / scale, None)
        else:
            raise ValueError("a ref, or x and y, says where")

    def pointed(self, msg, t, kind, x, y, **more):
        """What the Bot did with the mouse (not the Browser page's own), for its pointer: drawn here if it's hosted,
        else told its watchers (BotPointer)."""
        if not msg.get("from_pane"):
            self.win.pointer.add(kind, x, y, tab=t.id, played=self.win.hosted, **more)

    def op_click(self, msg, answer):
        t = self.tab(msg)
        mods = [m for m in (msg.get("modifiers") or []) if m in ("shift", "ctrl", "alt", "cmd")]

        def at(x, y, covered):
            started = GLib.get_monotonic_time()
            self.pointed(msg, t, "click", x, y, count=1 if msg.get("press") else max(1, int(msg.get("count") or 1)))
            t.web.click(x, y, msg.get("button") if msg.get("button") in ("right", "middle") else "left",
                        max(1, int(msg.get("count") or 1)), mods, press=int(msg["press"]) if msg.get("press") else None)
            if not t.web.keyed():  # (a window that isn't key: what's clicked given focus, as a click would give it)
                t.web.run_js(PAGE_JS + f"const el = document.elementFromPoint({x}, {y});"
                             "const f = el && el.closest('input, textarea, select, [contenteditable], button, a[href],"
                             " [tabindex]'); if (f && f.focus) f.focus(); return true", lambda *_: None)
            if msg.get("quick"):  # (the Browser page's own click: answered done, its page seen in the frames)
                return answer({"clicked": [round(x), round(y)]})
            k = 1.0 if msg.get("css") else self.scales.get(t.id, 1.0)  # (said in the screenshot's pixels, as asked)
            self.settle(t, answer, {"clicked": [round(x * k), round(y * k)],
                                    **({"covered_by": covered} if covered else {})}, started)
        self.point(t, msg, answer, at)

    def op_drag(self, msg, answer):
        """A drag along path ([[x, y], ...], the page's CSS pixels if css, else the last screenshot's)."""
        t = self.tab(msg)
        scale = 1.0 if msg.get("css") else self.scales.get(t.id, 1.0)
        path = [(float(p[0]) / scale, float(p[1]) / scale) for p in msg.get("path") or [] if len(p) == 2]
        if len(path) < 2:
            raise ValueError("a drag needs a path of two points at least")
        started = GLib.get_monotonic_time()
        self.pointed(msg, t, "drag", *path[0], path=path)
        t.web.drag(path)
        if msg.get("quick"):
            return answer({"dragged": len(path)})
        self.settle(t, answer, {"dragged": len(path)}, started)

    def op_hover(self, msg, answer):
        t = self.tab(msg)
        if msg.get("quick"):
            return self.point(t, msg, answer, lambda x, y, _c: (t.web.move(x, y), answer({"hovered": True})))
        self.point(t, msg, answer, lambda x, y, _c: (self.pointed(msg, t, "hover", x, y), t.web.move(x, y),
                                                     GLib.timeout_add(80, lambda: answer(self.describe(t)) and False)))

    def op_type(self, msg, answer):
        """text typed into the focused field (or ref's, focused first), a key press a character at a time, as a
        keyboard would type it (a character no key here makes: put in as the page's own editing would)."""
        t, text = self.tab(msg), str(msg.get("text") or "")
        focus = f"em.get({json.dumps(msg['ref'])}).focus();" if msg.get("ref") else ""
        left = list(text)

        def read_back():
            self.js(t, "const el = em.typed;"
                       "if (!el || !el.isConnected) return null;"
                       "const v = 'value' in el ? String(el.value) : el.textContent;"
                       "return v.length > 2000 ? v.slice(0, 2000) + '…' : v", answer,
                    lambda value: answer({**self.describe(t), "value": value} if value is not None else
                                         {**self.describe(t), "note": "the field's gone (the page changed)"}))

        @guarded_by(answer)
        def next_char():
            if not left:
                read_back()
                return False
            ch = left.pop(0)
            if not t.web.type_char(ch):
                self.js(t, f"const ch = {json.dumps(ch)}, el = em.typed;"
                           "if (!document.execCommand('insertText', false, ch) && el) {"
                           "  if ('value' in el) em.setValue(el, el.value + ch); else el.textContent += ch; }"
                           "return true", answer,
                        lambda _v: GLib.timeout_add(self.TYPE_GAP_MS, next_char))
                return False
            return True

        def ready(_v):
            if next_char():
                GLib.timeout_add(self.TYPE_GAP_MS, next_char)
        anywhere = json.dumps(bool(msg.get("anywhere")))
        self.js(t, focus + "const el = document.activeElement;"
                f"if (!em.editable(el) && !{anywhere}) throw new Error('nothing to type into is focused: click the "
                "field, or give its ref'); em.typed = em.editable(el) ? el : null; return true", answer, ready)

    def op_key(self, msg, answer):
        t = self.tab(msg)
        combos = str(msg.get("keys") or "").split()
        if not combos:
            raise ValueError("key needs keys: Enter, Tab, cmd+a, Escape, …")
        names = {"return": "Enter", "enter": "Enter", "esc": "Escape", "escape": "Escape", "tab": "Tab",
                 "backspace": "Backspace", "delete": "Delete", "del": "Delete", "space": "Space", "up": "Up",
                 "down": "Down", "left": "Left", "right": "Right", "home": "Home", "end": "End",
                 "pageup": "PageUp", "pagedown": "PageDown"}
        mod_names = {"shift": "shift", "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "option": "alt",
                     "cmd": "cmd", "command": "cmd", "meta": "cmd", "super": "cmd"}
        presses = []
        for combo in combos:
            parts = combo.split("+")
            mods = tuple(sorted({mod_names[p.lower()] for p in parts[:-1] if p.lower() in mod_names}))
            k = parts[-1]
            k = names.get(k.lower(), k)
            if len(k) == 1 and k.isupper():
                k, mods = k.lower(), tuple(sorted(set(mods) | {"shift"}))
            presses.append((k, mods))
        started = GLib.get_monotonic_time()

        def press():
            if not presses:
                self.settle(t, answer, None, started)
                return False
            k, mods = presses.pop(0)
            try:
                t.web.key(k, mods)
            except Exception as e:
                answer({"error": f"{type(e).__name__}: {e}" if not isinstance(e, (ValueError, OSError)) else str(e)})
                return False
            return True
        if press():
            GLib.timeout_add(self.KEY_GAP_MS, press)

    def op_scroll(self, msg, answer):
        """A real mouse wheel turned over a ref, a point of the last screenshot, or the page's middle: dy pixels
        down (600 if none's given; up negative), dx right; what's under the pointer scrolls."""
        t = self.tab(msg)
        dx, dy = int(msg.get("dx") or 0), int(msg.get("dy") if msg.get("dy") is not None else 600)
        under = ("let el = document.elementFromPoint({x}, {y});"
                 "while (el && el !== document.body && el !== document.documentElement && !("
                 "el.scrollHeight > el.clientHeight + 2 && /(auto|scroll)/.test(getComputedStyle(el).overflowY)) && !("
                 "el.scrollWidth > el.clientWidth + 2 && /(auto|scroll)/.test(getComputedStyle(el).overflowX)))"
                 " el = el.parentElement;"
                 "const inner = !!el && el !== document.body && el !== document.documentElement;"
                 "const s = inner ? el : document.scrollingElement;")
        if msg.get("quick") and msg.get("x") is not None:  # (the Browser page's own wheel: turned, nothing waited for;
            def turned(x, y, _c):                               # with no display of its own, by script, as below)
                try:
                    t.web.wheel(x, y, dx, dy, quick=True)
                except OSError:
                    t.web.run_js(under.format(x=x, y=y) + f"s.scrollBy({{left: {dx}, top: {dy}, behavior: 'instant'}});"
                                 "return true", lambda *_: None, page=True)
                answer({"scrolled": [dx, dy]})
            return self.point(t, msg, answer, turned)

        def at(x, y, _c):
            self.pointed(msg, t, "scroll", x, y, dx=dx, dy=dy)

            def chosen(inner):
                how = "wheel"
                try:
                    t.web.wheel(x, y, dx, dy)
                    extra = ""
                except OSError:
                    how = "script"
                    extra = f"s.scrollBy({{left: {dx}, top: {dy}, behavior: 'instant'}});"
                GLib.timeout_add(250, guarded(lambda: self.js(
                    t, under.format(x=x, y=y) + extra + "await em.sleep(120);"
                    f"const came = em.came(em.seen || new Set(), {self.CAME_LINES}, {self.CAME_CHARS});"
                    "em.seen = null;"
                    "return {where: {x: s.scrollLeft, y: s.scrollTop, height: s.scrollHeight, inner}, came}", answer,
                    lambda got: answer({**self.describe(t), "scrolled": got["where"], "by": how,
                                        "in_view": in_view(got["came"])})) and False, answer))
            self.js(t, under.format(x=x, y=y) + "return inner", answer, chosen)

        def in_view(came):
            if not came["lines"]:
                return "Nothing new came into view."
            more = f"\n(… and {came['more']} more: read_page lists them)" if came["more"] else ""
            return "Came into view:\n" + "\n".join(came["lines"]) + more
        # (what's in view first: before a ref's element is scrolled to, which may bring things into view itself)
        if msg.get("ref") or msg.get("x") is not None:
            self.js(t, "em.seen = new Set(em.onscreen()); return true", answer,
                    lambda _v: self.point(t, msg, answer, at))
        else:
            self.js(t, "em.seen = new Set(em.onscreen()); return {x: innerWidth / 2, y: innerHeight / 2}", answer,
                    lambda c: at(c["x"], c["y"], None))

    def op_console(self, msg, answer):
        """What the page has said in its console (and its errors), newest last: pattern, a regular expression, only
        those matching it; onlyErrors, only errors; clear, forgotten after."""
        t = self.tab(msg)
        self.js(t, "const log = window[Symbol.for('emission.console')] || [];"
                   f"const p = {json.dumps(str(msg.get('pattern') or ''))};"
                   "let re = null;"
                   "try { re = p ? new RegExp(p) : null; } catch (e) { re = {test: x => x.includes(p)}; }"
                   f"const errors = {json.dumps(bool(msg.get('onlyErrors')))};"
                   "const got = log.filter(e => (!re || re.test(e.text)) && (!errors || e.level === 'error'))"
                   f".slice(-{max(1, int(msg.get('limit') or 100))});"
                   f"if ({json.dumps(bool(msg.get('clear')))}) log.length = 0; return got", answer,
                lambda got: answer({**self.describe(t), "console": got}), page=True)

    def op_network(self, msg, answer):
        """What the page has loaded (itself, and its resources), newest last: urlPattern, only URLs with it in them;
        clear, those forgotten after."""
        t = self.tab(msg)
        self.js(t, f"const p = {json.dumps(str(msg.get('urlPattern') or ''))}, K = Symbol.for('emission.network');"
                   "const since = window[K] ?? -1;"
                   "const got = [...performance.getEntriesByType('navigation'),"
                   " ...performance.getEntriesByType('resource')]"
                   ".filter(e => e.startTime > since && (!p || e.name.includes(p)))"
                   f".slice(-{max(1, int(msg.get('limit') or 100))}).map(e => ({{url: e.name, type: e.initiatorType || "
                   "e.entryType, status: e.responseStatus || null, ms: Math.round(e.duration), "
                   "bytes: e.transferSize || null}));"
                   f"if ({json.dumps(bool(msg.get('clear')))}) window[K] = performance.now(); return got", answer,
                lambda got: answer({**self.describe(t), "requests": got}))

    def op_resize(self, msg, answer):
        if self.win.hosted:  # (in Baude: its page laid out at this when it's not shown, its pane's size when it is)
            return self.resize_hosted(msg, answer)
        return self.resize_window(msg, answer)

    def resize_hosted(self, msg, answer):
        t = self.tab(msg)
        w, h = int(msg.get("width") or 0), int(msg.get("height") or 0)
        if not (200 <= w <= SCREEN[0] - 200 and 200 <= h <= SCREEN[1] - 300):
            raise ValueError(f"width between 200 and {SCREEN[0] - 200}, height between 200 and {SCREEN[1] - 300}")
        if msg.get("fit"):  # (a pane watching it streamed, its page out of sight: laid out at the pane's shape while
            if self.win.watchers:  # it's watched, the Bot's own size again once it isn't: unwatch; one come after
                self.win.fit_size = (w, h)  # its watch ended, not)
                for tab in self.win.tabs.values():
                    tab.web.follow()
            return answer({**self.describe(t), "size": None})
        self.win.page_size, self.win.fit_size = (w, h), None  # (the Bot's own: kept, a watcher's given up)
        for tab in self.win.tabs.values():
            tab.web.follow()
        shown = t.place.get_mapped()
        GLib.timeout_add(300, guarded(lambda: self.js(t, "return {width: innerWidth, height: innerHeight}", answer,
                                                      lambda v: answer({**self.describe(t), "size": v, **(
                                                          {"note": "its page is shown in Baude's Browser page now, at "
                                                                   "the page's size: it's laid out at this size again "
                                                                   "once it isn't"} if shown else {})}))
                                      and False, answer))

    def resize_window(self, msg, answer):
        """The page's size (its viewport) made width by height, CSS pixels. fit: a watcher's (its pane's size), the
        size the Bot had kept to put back once no one watches; else the Bot's own, kept."""
        t = self.tab(msg)
        if self.win.agent:  # (as it's done, in its turn: not as it was asked)
            if not msg.get("fit"):
                self.win.watch_size = None
            elif self.win.watch_size is None:
                self.win.watch_size = self.win.get_default_size()
        w, h = int(msg.get("width") or 0), int(msg.get("height") or 0)
        if not (200 <= w <= SCREEN[0] - 200 and 200 <= h <= SCREEN[1] - 300):
            raise ValueError(f"width between 200 and {SCREEN[0] - 200}, height between 200 and {SCREEN[1] - 300}")

        def sized(now):
            ww, wh = self.win.get_default_size()
            self.win.set_default_size(max(300, ww + w - int(now["w"])), max(300, wh + h - int(now["h"])))
            GLib.timeout_add(500, guarded(lambda: self.js(t, "return {width: innerWidth, height: innerHeight}",
                                                          answer, lambda v: answer({**self.describe(t), "size": v}))
                                          and False, answer))
        self.js(t, "return {w: innerWidth, h: innerHeight}", answer, sized)

    def op_form_input(self, msg, answer):
        t = self.tab(msg)
        if not msg.get("ref"):
            raise ValueError("form_input needs the field's ref")
        self.js(t, f"const el = em.get({json.dumps(msg['ref'])}), v = {json.dumps(msg.get('value'))};"
                "if (el.tagName === 'SELECT') {"
                "  const o = [...el.options].find(o => o.value === String(v) || o.text.trim() === String(v));"
                "  if (!o) throw new Error('no option ' + v + ': ' + [...el.options].map(o => o.text.trim()).join(', '));"
                "  em.setValue(el, o.value); return o.text.trim(); }"
                "if (el.type === 'checkbox' || el.type === 'radio') {"
                "  const want = v === true || v === 'true' || v === 'on' || v === 1;"
                "  if (el.checked !== want) el.click(); return el.checked; }"
                "if (!['INPUT', 'TEXTAREA'].includes(el.tagName)) throw new Error(" + json.dumps(str(msg["ref"])) +
                " + ' is a ' + el.tagName.toLowerCase() + ', not a field');"
                "em.setValue(el, String(v ?? '')); return el.value", answer,
                lambda value: answer({**self.describe(t), "value": value}))

    # (a page's own prompts: alert, confirm, prompt, a site's sign-in, files to upload)

    def op_prompts(self, msg, answer):
        answer({"prompts": self.win.prompts(None)})

    def op_answer_prompt(self, msg, answer):
        self.win.answer_prompt(msg)
        GLib.timeout_add(300, lambda: answer({"ok": True, "tabs": [self.describe(t) for t in self.win.tabs.values()]})
                         and False)

    # (sign-ins)

    def op_sites(self, msg, answer):
        self.win.Engine.cookies(self.win.profile_id, lambda cookies, why: answer(
            {"error": why} if why else {"profile": self.win.profile, "sites": sites_of(cookies)}))

    def op_add_cookies(self, msg, answer):
        self.win.Engine.add_cookies(self.win.profile_id, msg.get("cookies") or [],
                                    lambda n, why: answer({"error": why} if why else {"added": n}))


def sites_of(cookies):
    """The sites cookies are for (signed in to, as like as not), from their domains."""
    out = set()
    for c in cookies or []:
        d = str(c.get("domain") or "").lstrip(".").lower()
        if d:
            parts = d.split(".")
            out.add(".".join(parts[-3:]) if len(parts) > 2 and len(parts[-2]) <= 3 else ".".join(parts[-2:]))
    return sorted(out)


class Watcher:
    """Someone watching a profile's browser (Baude's Browser pane): its frames when the page changes, and its state
    (tabs, prompts) when that does, each frame sent only once the last's been taken (acked): a slow one gets fewer,
    never a backlog. shm (one on this machine): the frame's pixels in shared memory, only a line saying so sent;
    else (one elsewhere, through its host) the frame as a JPEG in the line itself."""
    ACK_S = 5  # (a frame not acked in this long: taken as lost)

    def __init__(self, win, conn, msg):
        self.win, self.conn = win, conn
        self.shm_wanted = bool(msg.get("shm"))
        self.max_w = max(100, min(3840, int(msg.get("max_w") or 1280)))
        self.max_h = max(100, min(2400, int(msg.get("max_h") or 900)))
        self.seq, self.waiting_since, self.sent_sum, self.sent_state, self.pending = 0, None, None, None, None
        self.memory, self.buffer, self.out, self.writing = None, b"", b"", None
        conn.setblocking(False)
        self.source = GLib.io_add_watch(conn.fileno(), GLib.PRIORITY_DEFAULT,
                                        GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR, self.heard)

    def heard(self, _fd, cond):
        """What the watcher says: {ack: seq} for a frame taken, {max_w, max_h} for its new size; or it's gone."""
        try:
            part = self.conn.recv(1 << 16)
        except BlockingIOError:
            return True
        except OSError:
            part = b""
        if not part:
            self.win.unwatch(self)
            return False
        self.buffer += part
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("ack") == self.seq:
                self.waiting_since = None
                if self.pending is not None:  # (the newest frame, taken while it waited: now)
                    self.offer(*self.pending)
            if msg.get("max_w"):
                self.max_w = max(100, min(3840, int(msg["max_w"])))
                self.max_h = max(100, min(2400, int(msg.get("max_h") or self.max_h)))
                self.sent_sum = None  # (sent again at its new size)
                self.win.watch_kick()  # (an ack isn't the page changing: only a new size is)
        return True

    def say(self, msg):
        """msg sent without waiting on the watcher: what the socket won't take now is kept, and written as it will
        (a frame isn't made till it's all gone). False if it's gone."""
        self.out += (json.dumps(msg) + "\n").encode()
        return self.flush()

    def flush(self, *_):
        try:
            while self.out:
                n = self.conn.send(self.out)
                self.out = self.out[n:]
        except BlockingIOError:
            if self.writing is None:
                self.writing = GLib.io_add_watch(self.conn.fileno(), GLib.PRIORITY_DEFAULT, GLib.IOCondition.OUT,
                                                 self.flushed)
            return True
        except (OSError, ValueError):
            self.win.unwatch(self)
            return False
        return True

    def flushed(self, *_):
        self.writing = None
        self.flush()
        return False

    def ready(self):
        if self.out:
            return False
        if self.waiting_since is not None and time.monotonic() - self.waiting_since > self.ACK_S:
            self.waiting_since = None
        return self.waiting_since is None

    def offer(self, w, h, stride, data, total):
        """A frame (the page's, w by h): sent if it's new to this watcher and it's taken the last; else kept, the
        newest, to be sent as soon as it has."""
        if total == self.sent_sum:
            self.pending = None  # (back as it was sent: nothing newer to send)
            return
        if not self.ready():
            self.pending = (w, h, stride, data, total)
            return
        self.pending = None
        if w > self.max_w or h > self.max_h:
            w, h, stride, data = scaled(w, h, stride, data, self.max_w, self.max_h)
        self.seq += 1
        if self.shm_wanted and self.to_memory(stride * h):
            self.memory.buf[:stride * h] = data
            line = {"frame": self.seq, "w": w, "h": h, "stride": stride, "shm": self.memory.name}
        else:
            line = {"frame": self.seq, "w": w, "h": h, "jpeg": base64.b64encode(jpeg(w, h, stride, data)).decode()}
        if self.say(line):
            self.sent_sum, self.waiting_since = total, time.monotonic()

    def to_memory(self, size):
        """Shared memory at least size big (made anew if it's smaller): False if there's none to be had."""
        if self.memory is not None and self.memory.size >= size:
            return True
        from multiprocessing import shared_memory
        self.drop_memory()
        try:
            self.memory = shared_memory.SharedMemory(create=True, size=size + (size >> 2), track=False)
        except TypeError:  # (a Python before 3.13: no track)
            self.memory = shared_memory.SharedMemory(create=True, size=size + (size >> 2))
        except OSError:
            self.memory, self.shm_wanted = None, False
            return False
        return True

    def drop_memory(self):
        if self.memory is not None:
            try:
                self.memory.close()
                self.memory.unlink()
            except (OSError, BufferError):
                pass
            self.memory = None

    def close(self):
        GLib.source_remove(self.source)
        if self.writing is not None:
            GLib.source_remove(self.writing)
            self.writing = None
        self.drop_memory()
        try:
            self.conn.close()
        except OSError:
            pass


def scaled(w, h, stride, data, max_w, max_h):
    """Pixels made to fit max_w by max_h, their shape kept: (w, h, stride, data)."""
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    pic = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(data), GdkPixbuf.Colorspace.RGB, True, 8, w, h, stride)
    scale = min(max_w / w, max_h / h)
    pic = pic.scale_simple(max(1, int(w * scale)), max(1, int(h * scale)), GdkPixbuf.InterpType.BILINEAR)
    return pic.get_width(), pic.get_height(), pic.get_rowstride(), pic.read_pixel_bytes().get_data()


def jpeg(w, h, stride, data, quality=wire.CONSTANTS["FRAME_QUALITY"]):
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    pic = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(data), GdkPixbuf.Colorspace.RGB, True, 8, w, h, stride)
    flat = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, w, h)
    flat.fill(0xffffffff)
    pic.composite(flat, 0, 0, w, h, 0, 0, 1, 1, GdkPixbuf.InterpType.NEAREST, 255)
    return flat.save_to_bufferv("jpeg", ["quality"], [str(quality)])[1]


# ---- its logo: Baude's and Record's tile, in a muted yellow (their terracotta's and teal's lightness and saturation),
# with a cream page on it (their bubble's, without its tail) and a globe cut out of the page through a mask, so every
# cut is the tile itself: a meridian and two parallels. At 16 px, the same a pixel wide, on whole pixels (ICON_SVG_16).

ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <mask id="cut">
    <rect x="22" y="38" width="84" height="52" rx="8" fill="#fff"/>
    <g fill="none" stroke="#000" stroke-width="4.5">
      <ellipse cx="64" cy="64" rx="22" ry="34"/>
      <path d="M22 50h84M22 78h84"/>
    </g>
  </mask>
  <rect x="8" y="8" width="112" height="112" rx="26" fill="#d9b44a"/>
  <rect x="22" y="38" width="84" height="52" rx="8" fill="#fff8f0" mask="url(#cut)"/>
</svg>
"""
ICON_SVG_16 = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <mask id="cut">
    <rect x="22" y="38" width="84" height="52" rx="8" fill="#fff"/>
    <g fill="none" stroke="#000" stroke-width="8">
      <ellipse cx="64" cy="64" rx="20" ry="36"/>
      <path d="M22 52h84M22 76h84"/>
    </g>
  </mask>
  <rect x="8" y="8" width="112" height="112" rx="26" fill="#d9b44a"/>
  <rect x="22" y="38" width="84" height="52" rx="8" fill="#fff8f0" mask="url(#cut)"/>
</svg>
"""


def icon_png(size=1024, mac=True):
    """The logo as a PNG; mac: with the margin macOS's own icons have (the tile 824 of 1024)."""
    return applist.icon_png(ICON_SVG, size, mac)


def dock_icon():
    """macOS: the Dock shows the logo, not Python's rocket (GTK's icon names don't reach a Python process's tile)."""
    png = icon_png()
    send = macos.objc_send
    data = send(b"NSData", b"dataWithBytes:length:", png, len(png), types=(ctypes.c_char_p, ctypes.c_ulong))
    image = send(send(b"NSImage", b"alloc"), b"initWithData:", data, types=(ctypes.c_void_p,))
    send(send(b"NSApplication", b"sharedApplication"), b"setApplicationIconImage:", image, types=(ctypes.c_void_p,))


def themed_icon():
    """Elsewhere, windows take the logo from the icon theme by name (APP_ID): it's put in an icon folder of
    Emission's own (16 px's its own drawing), found there whether or not Emission's in the application menu. Once a
    display (each window asks). Its path (None: not written)."""
    display = Gdk.Display.get_default()
    if display is not None and getattr(display, "emission_icon", None):
        return display.emission_icon
    root, path = os.path.join(data_home(), "icons"), None
    try:
        for size, svg in (("scalable", ICON_SVG), ("16x16", ICON_SVG_16)):
            there = os.path.join(root, "hicolor", size, "apps", f"{APP_ID}.svg")
            if not os.path.exists(there) or open(there).read() != svg:
                os.makedirs(os.path.dirname(there), exist_ok=True)
                with open(there, "w") as f:
                    f.write(svg)
            path = path or there
    except OSError:
        return None
    if display is not None:
        Gtk.IconTheme.get_for_display(display).add_search_path(root)
        display.emission_icon = path
    return path


def install():
    """Emission in the platform's list of apps, for this user (applist): macOS's ~/Applications/Emission.app, Linux's
    .desktop entry and logo. What it wrote."""
    return applist.install(APP_ID, "Emission", ICON_SVG, [sys.executable, os.path.realpath(__file__), "%u"],
                           "public.app-category.productivity",
                           ["GenericName=Web Browser", "Comment=Baude's browser", "Categories=Network;WebBrowser;GTK;"],
                           small=ICON_SVG_16)


def uninstall():
    """Emission's launcher removed: what was there."""
    return applist.uninstall(APP_ID, "Emission")


class App(Adw.Application):
    def __init__(self, url, profile, agent=False):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.url, self.profile, self.agent = url, profile, agent

    def do_startup(self):
        Adw.Application.do_startup(self)
        if self.agent:  # (never seen: no logo, no look)
            return
        GLib.set_application_name("Emission")
        import widgets
        widgets.load_css()  # (Baude's look: the frame, the inside set into it)
        widgets.theme_css()  # (whose stylesheet that's drawn over: libadwaita's, or a GTK_THEME's)
        if sys.platform == "darwin":
            try:
                dock_icon()
            except Exception as e:  # (Python's Dock tile isn't worth not starting over)
                print("emission: dock_icon:", e, file=sys.stderr)
        elif themed_icon() is not None:
            Gtk.Window.set_default_icon_name(APP_ID)

    def do_activate(self):
        Window(self, self.url, self.profile, self.agent).present()


def with_display(own=False):
    """On Linux with no display (or, own, whatever there is: an agent's Emission isn't put on anyone's screen), this
    run again under a virtual one of its own (xvfb-run), or why it can't be."""
    if sys.platform == "darwin" or os.environ.get("EMISSION_VIRTUAL_DISPLAY") or not own and (
            os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return None
    if shutil.which("xvfb-run") is None:
        return "There's no display here, and xvfb-run (package xvfb) isn't installed to make one."
    env = {**os.environ, "EMISSION_VIRTUAL_DISPLAY": "1", "GDK_BACKEND": "x11"}
    os.execvpe("xvfb-run", ["xvfb-run", "--auto-servernum", f"--server-args=-screen 0 {SCREEN[0]}x{SCREEN[1]}x24", sys.executable,
                            os.path.abspath(__file__), *sys.argv[1:]], env)


def main():
    p = argparse.ArgumentParser(description="Emission, Baude's browser")
    p.add_argument("url", nargs="?")
    p.add_argument("--profile", default=DEFAULT, help="whose cookies and logins (a profile of its own each)")
    p.add_argument("--bot", help="a Bot's chat id: its own profile (made if it has none)")
    p.add_argument("--name", help="the Bot's name, with --bot")
    p.add_argument("--agent", action="store_true", help="for the Bot's tools: out of sight, gone when idle")
    inst = p.add_mutually_exclusive_group()
    where = wording.say("install.applications") if sys.platform == "darwin" else \
        wording.say("install.application_menu")
    inst.add_argument("--install", action="store_true",
                      help=wording.say("install.install_help", app="Emission", where=where))
    inst.add_argument("--uninstall", action="store_true",
                      help=wording.say("install.uninstall_help", app="Emission", where=where))
    a = p.parse_args()
    if a.install:
        print("\n".join(wording.say("install.installed", path=path) for path in install()))
        return 0
    if a.uninstall:
        print("\n".join(wording.say("install.removed", path=path) for path in uninstall())
              or wording.say("install.not_installed", app="Emission"))
        return 0
    if a.agent and not a.bot:
        p.error("--agent is for a Bot's Emission: --bot too")
    if (why := with_display(own=a.agent)) is not None:
        print(why, file=sys.stderr)
        return 1
    if a.bot:
        pid = bot_profile(a.bot, a.name)
        a.profile = next(p["name"] for p in profiles() if p["id"] == pid)
        if a.agent:
            if engine()[0] is None:
                print(engine()[1], file=sys.stderr)
                return 2
            if not lock_profile(pid):
                return 0  # (another Emission has it already: the tools reach that one)
            AGENT["on"] = True
            if sys.platform == "darwin":
                macos.stay_offscreen()
                MacWeb.stay_awake()
            return App(None, a.profile, agent=True).run([sys.argv[0]])
    if not a.profile.strip():
        p.error("a profile needs a name")
    pid = add_profile(a.profile.strip())
    clear_leftovers()
    if pid not in HELD and not lock_profile(pid) and hand_over(pid, [a.url or HOME_PAGE]):
        print(f"Opened in the Emission that has the profile “{a.profile.strip()}”.")
        return 0
    return App(a.url, a.profile.strip()).run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
