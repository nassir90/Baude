#!/usr/bin/env python3
"""Emission: Baude's browser, a GTK window of its own (and, later, one of Baude's side panel's pages): tabs, back and
forward, reload, an address that's a search too, and profiles, each its own cookies and logins kept (a login made once,
stored, as a Bot's identity will use it). Its pages are WebKit's: on macOS Apple's WKWebView (MacWeb), put in the
window through the Objective-C runtime (macos.objc_send: there's no PyObjC); on Linux WebKitGTK 6.0 (GtkWeb), a GTK
widget of its own. On a Linux host with no display it starts one of its own (xvfb-run).

    python3 emission.py [URL] [--profile NAME]
"""
import argparse
import ctypes
import fcntl
import html
import ipaddress
import json
import os
import platform
import shutil
import sys
import urllib.parse
import uuid

import macos  # noqa: E402  (no GTK: before it's loaded)
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

APP_ID = "dev.baude.Emission"
HOME_PAGE = "https://duckduckgo.com/"
SEARCH = "https://duckduckgo.com/?q={}"
PROFILE_SPACE = uuid.UUID("6b1f0d3e-3c4e-4f51-9b0e-7a1e5e6d2c11")  # (a profile named before ids: its name's uuid)
POLL_MS = 200  # (each page's title, address, progress, back and forward read this often, the same on both engines)
DEFAULT = "Default"


def data_home():
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "baude", "emission")


# ------------------------------------------------------------------ profiles --

def _read_profiles():
    try:
        with open(os.path.join(data_home(), "profiles.json"), encoding="utf-8") as f:
            raw = json.load(f).get("profiles") or []
    except (OSError, ValueError, AttributeError):
        raw = []
    out = []
    for p in raw:
        if isinstance(p, str):
            p = {"name": p, "id": str(uuid.uuid5(PROFILE_SPACE, p))}
        if isinstance(p, dict) and isinstance(p.get("name"), str) and p["name"].strip() and p["name"] != DEFAULT \
                and isinstance(p.get("id"), str):
            out.append({"name": p["name"].strip(), "id": p["id"]})
    return out


def _write_profiles(items):
    os.makedirs(data_home(), exist_ok=True)
    path = os.path.join(data_home(), "profiles.json")
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"profiles": items}, f, indent=1)
    os.replace(path + ".tmp", path)


def profiles():
    """The profiles there are, "Default" first: [{name, id}], each one's cookies and logins its own (kept by its id,
    so a profile renamed keeps them)."""
    return [{"name": DEFAULT, "id": str(uuid.uuid5(PROFILE_SPACE, DEFAULT))}, *_read_profiles()]


def profile_id(name):
    return next((p["id"] for p in profiles() if p["name"] == name), None)


def add_profile(name):
    if profile_id(name) is None:
        _write_profiles([*_read_profiles(), {"name": name, "id": str(uuid.uuid4())}])
    return profile_id(name)


def rename_profile(old, new):
    _write_profiles([{**p, "name": new} if p["name"] == old else p for p in _read_profiles()])


def forget_profile(name):
    _write_profiles([p for p in _read_profiles() if p["name"] != name])


def lock_profile(pid):
    """The profile's lock (a file descriptor, held while a window uses it), or None if another Emission has it: two
    processes on one store would share its cookie database and caches."""
    folder = os.path.join(data_home(), "profiles")
    os.makedirs(folder, exist_ok=True)
    fd = os.open(os.path.join(folder, pid + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def unlock_profile(fd):
    if fd is not None:
        os.close(fd)


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

    @classmethod
    def make(cls, fn, types=()):
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
        cls.kept.extend((imp, desc, lit))
        return ctypes.addressof(lit)


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

    @classmethod
    def decide_action(cls, _view, action, decide):
        download = macos.objc_send(action, b"shouldPerformDownload", ret=ctypes.c_bool)
        Blocks.call(decide, 2 if download else 1, types=(ctypes.c_long,))  # (WKNavigationActionPolicy)

    @classmethod
    def decide_response(cls, _view, response, decide):
        send = macos.objc_send
        show = send(response, b"canShowMIMEType", ret=ctypes.c_bool)
        r = send(response, b"response")
        if show and send(r, b"respondsToSelector:", objc_sel(b"valueForHTTPHeaderField:"),
                         types=(ctypes.c_void_p,), ret=ctypes.c_bool):
            how = cls._text(send(r, b"valueForHTTPHeaderField:", macos.ns_string("Content-Disposition"),
                                 types=(ctypes.c_void_p,)))
            show = not how.strip().lower().startswith("attachment")
        Blocks.call(decide, 1 if show else 2, types=(ctypes.c_long,))  # (WKNavigationResponsePolicy)

    @classmethod
    def downloading(cls, download):
        macos.objc_send(download, b"setDelegate:", cls.make_delegate(), types=(ctypes.c_void_p,))

    @classmethod
    def destination(cls, download, _response, name, done):
        path = download_path(cls._text(name))
        cls.downloads[download] = path
        url = macos.objc_send(b"NSURL", b"fileURLWithPath:", macos.ns_string(path), types=(ctypes.c_void_p,))
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
        space = send(challenge, b"protectionSpace")
        method = cls._text(send(space, b"authenticationMethod"))
        web = cls.views.get(view)
        if web is None or web.window is None or method not in (
                "NSURLAuthenticationMethodHTTPBasic", "NSURLAuthenticationMethodHTTPDigest",
                "NSURLAuthenticationMethodDefault"):
            Blocks.call(done, 1, None, types=(ctypes.c_long, ctypes.c_void_p))  # (default handling: TLS and the like)
            return
        done = Blocks.copy(done)

        def answer(user, password):
            if user is None:
                Blocks.call(done, 2, None, types=(ctypes.c_long, ctypes.c_void_p), release=True)  # (cancelled)
                return
            cred = send(b"NSURLCredential", b"credentialWithUser:password:persistence:", macos.ns_string(user),
                        macos.ns_string(password), 1, types=(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong))
            Blocks.call(done, 0, cred, types=(ctypes.c_long, ctypes.c_void_p), release=True)  # (use it)
        web.window.sign_in(web, cls._text(send(space, b"host")), cls._text(send(space, b"realm")),
                           send(challenge, b"previousFailureCount", ret=ctypes.c_long) > 0, once(answer))

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
            finish = lambda _answer: Blocks.call(done, release=True)  # noqa: E731
        elif kind == "confirm":
            finish = lambda answer: Blocks.call(done, bool(answer), types=(ctypes.c_bool,), release=True)  # noqa
        else:
            finish = lambda answer: Blocks.call(done, macos.ns_string(answer) if answer is not None else None,  # noqa
                                                types=(ctypes.c_void_p,), release=True)
        if web is None or web.window is None:
            finish(None)
            return
        web.window.page_dialog(web, kind, cls._text(message), cls._text(text), once(finish))

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
        if web is None or web.window is None:
            finish(None)
            return
        web.window.choose_files(send(params, b"allowsMultipleSelection", ret=ctypes.c_bool),
                                send(params, b"allowsDirectories", ret=ctypes.c_bool), once(finish))

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
        self.view = send(send(b"WKWebView", b"alloc"), b"initWithFrame:configuration:", R(0, 0, 10, 10), config,
                         types=(R, ctypes.c_void_p))
        if not opener:
            send(config, b"release")  # (the view keeps its own copy)
        send(self.view, b"setAllowsBackForwardNavigationGestures:", True, types=(ctypes.c_bool,))
        send(self.view, b"setAutoresizingMask:", 2 | 16, types=(ctypes.c_ulong,))  # (sized with the window between
        delegate = self.make_delegate()                                             # polls: its margins kept)
        send(self.view, b"setNavigationDelegate:", delegate, types=(ctypes.c_void_p,))
        send(self.view, b"setUIDelegate:", delegate, types=(ctypes.c_void_p,))
        self.views[self.view] = self
        self.attached = None

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

    @staticmethod
    def keys_to_gtk(window):
        """Keys back to GTK's widgets (a page that had them, given up when something of GTK's is clicked)."""
        ns = macos.native_window(window)
        if ns:
            macos.objc_send(ns, b"makeFirstResponder:", macos.objc_send(ns, b"contentView"), types=(ctypes.c_void_p,))

    def attach(self):
        """Put in its window's content view, once the window's shown."""
        if self.attached is not None:
            return True
        ns = macos.native_window(self.place.get_root())
        if not ns:
            return False
        content = macos.objc_send(ns, b"contentView")
        macos.objc_send(content, b"addSubview:", self.view, types=(ctypes.c_void_p,))
        self.attached = content
        return True

    def follow(self):
        """Its frame kept over its place (the window resized, a bar's height changed)."""
        if not self.attach():
            return
        send, R = macos.objc_send, macos._rect()
        root = self.place.get_root()
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
        send(self.view, b"setHidden:", not self.shown, types=(ctypes.c_bool,))

    def show(self, on):
        self.shown = on
        if self.attached is not None:
            macos.objc_send(self.view, b"setHidden:", not on, types=(ctypes.c_bool,))

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

    def close(self):
        if self.view:
            send = macos.objc_send
            self.views.pop(self.view, None)
            send(self.view, b"setNavigationDelegate:", None, types=(ctypes.c_void_p,))
            send(self.view, b"setUIDelegate:", None, types=(ctypes.c_void_p,))
            send(self.view, b"removeFromSuperview")
            send(self.view, b"release")
            self.view = None


# ------------------------------------------------------------------- Linux --

class GtkWeb:
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
            if profile not in self.sessions:
                root = self.folder(profile)
                net = WebKit.NetworkSession.new(os.path.join(root, "data"), os.path.join(root, "cache"))
                net.get_cookie_manager().set_persistent_storage(os.path.join(root, "cookies.sqlite"),
                                                                WebKit.CookiePersistentStorage.SQLITE)
                net.connect("download-started", self.download_started)
                self.sessions[profile] = net
            self.view = WebKit.WebView(network_session=self.sessions[profile], hexpand=True, vexpand=True)
        self.views[self.view] = self
        self.view.connect("create", lambda v, _action: (new := self.popup and self.popup(v)) and new.view)
        self.view.connect("close", lambda _v: self.closed and GLib.idle_add(self.closed))
        self.view.connect("load-failed", self.failed)
        place.append(self.view)

    @classmethod
    def forget_session(cls, profile):
        cls.sessions.pop(profile, None)

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
        return {"title": v.get_title() or "", "url": v.get_uri() or "", "progress": v.get_estimated_load_progress(),
                "loading": v.is_loading(), "back": v.can_go_back(), "forward": v.can_go_forward()}

    def close(self):
        self.views.pop(self.view, None)
        self.view.try_close()


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
    def __init__(self, win, url, opener=None):
        self.win = win
        self.place = Gtk.Box(hexpand=True, vexpand=True)  # (where its page is drawn: MacWeb lays its view over it)
        self.web = win.Engine(self.place, win.profile_id, opener)
        self.web.window = win
        self.web.popup = lambda made_with: win.open(None, made_with).web
        self.web.closed = lambda: win.close_tab(self)
        self.last = {}
        if url is not None:
            self.web.load(address(url))


class Window(Adw.ApplicationWindow):
    def __init__(self, app, url, profile):
        super().__init__(application=app, title="Emission", default_width=1200, default_height=820)
        self.Engine, why = engine()
        self.profile, self.profile_id, self.tabs = profile, profile_id(profile) or add_profile(profile), {}
        self.lock, self.switching, self.asking, self.later = None, False, {}, []  # (later: (when, what), run by tick)
        self.back_btn = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="Back")
        self.fwd_btn = Gtk.Button(icon_name="go-next-symbolic", tooltip_text="Forward")
        self.reload_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Reload")
        self.back_btn.connect("clicked", lambda *_: self.current() and self.current().web.back())
        self.fwd_btn.connect("clicked", lambda *_: self.current() and self.current().web.forward())
        self.reload_btn.connect("clicked", lambda *_: self.current() and (
            self.current().web.stop() if self.current().last.get("loading") else self.current().web.reload()))
        self.entry = Gtk.Entry(hexpand=True, placeholder_text="Search or type an address")
        self.entry.connect("activate", lambda e: self.current() and self.current().web.load(address(e.get_text())))
        new_tab = Gtk.Button(icon_name="tab-new-symbolic", tooltip_text="New Tab (⌘T)")
        new_tab.connect("clicked", lambda *_: self.open(HOME_PAGE))
        self.who = Gtk.MenuButton(label=profile, tooltip_text="Profile: its cookies and logins its own")
        self.who.set_menu_model(self.profile_menu())
        bar = Adw.HeaderBar()
        for w in (self.back_btn, self.fwd_btn, self.reload_btn):
            bar.pack_start(w)
        bar.set_title_widget(self.entry)
        bar.pack_end(self.who)
        bar.pack_end(new_tab)
        self.view = Adw.TabView(vexpand=True)
        self.view.connect("notify::selected-page", lambda *_: self.selected())
        self.view.connect("close-page", self.closing)
        strip = Gtk.Overlay(child=Adw.TabBar(view=self.view, autohide=False))
        self.progress = Gtk.ProgressBar(visible=False, valign=Gtk.Align.END, can_target=False)
        self.progress.add_css_class("osd")
        strip.add_overlay(self.progress)  # (over the strip's foot: the page doesn't move as it shows and hides)
        self.banner = Adw.Banner(button_label="Show")
        self.banner.connect("button-clicked", lambda *_: self.show_download())
        self.shown_path, self.banner_gen = None, 0
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.banner)
        frame = Adw.ToolbarView(content=body)  # (its bar: back, forward, reload, the address, its profile)
        frame.add_top_bar(bar)
        frame.add_top_bar(strip)
        self.set_content(frame)
        if self.Engine is not None:
            self.lock = lock_profile(self.profile_id)
            if self.lock is None:
                why = f"The profile “{profile}” is open in another Emission window. Close that one, or open " \
                      "another profile here."
        if why:
            body.append(Adw.StatusPage(icon_name="web-browser-symbolic", title="Can't show pages here",
                                       description=why, vexpand=True))
        else:
            body.append(self.view)
        for name, keys, fn in (("new-tab", ["<Meta>t", "<Control>t"], lambda: self.open(HOME_PAGE)),
                               ("close-tab", ["<Meta>w", "<Control>w"], self.close_current),
                               ("address", ["<Meta>l", "<Control>l"], self.focus_address),
                               ("reload", ["<Meta>r", "<Control>r", "F5"], lambda: self.current()
                                and self.current().web.reload())):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda *_a, fn=fn: fn())
            self.add_action(act)
            app.set_accels_for_action(f"win.{name}", keys)
        if self.Engine is MacWeb:
            clicks = Gtk.GestureClick(propagation_phase=Gtk.PropagationPhase.CAPTURE)
            clicks.connect("pressed", lambda *_: MacWeb.keys_to_gtk(self))
            self.add_controller(clicks)
        self.connect("close-request", self.closing_window)
        if not why:
            self.open(url or HOME_PAGE)
            GLib.timeout_add(POLL_MS, self.tick)

    # (profiles)

    def profile_menu(self):
        menu, group = Gio.Menu(), Gio.SimpleActionGroup()
        for i, p in enumerate(profiles()):
            act = Gio.SimpleAction.new(f"p{i}", None)
            act.connect("activate", lambda *_a, n=p["name"]: self.switch_profile(n))
            group.add_action(act)
            menu.append(p["name"] + ("  ✓" if p["name"] == self.profile else ""), f"who.p{i}")
        more = Gio.Menu()
        for name, label, fn, on in (("add", "New Profile…", self.ask_profile, True),
                                    ("rename", "Rename Profile…", self.ask_rename, self.profile != DEFAULT),
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
        self.who.set_label(self.profile)
        self.who.set_menu_model(self.profile_menu())

    def ask_name(self, heading, body, verb, start, then):
        entry = Gtk.Entry(placeholder_text="Name", activates_default=True, text=start)
        dlg = Adw.AlertDialog(heading=heading, body=body, extra_child=entry, default_response="ok",
                              close_response="cancel")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", verb)
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)

        def answered(_d, r):
            name = entry.get_text().strip()
            if r != "ok" or not name:
                return
            if profile_id(name) is not None:
                self.say("That name's taken", f"There's a profile called “{name}” already.")
                return
            then(name)
        dlg.connect("response", once(answered))
        self.present_dialog(dlg)

    def ask_profile(self):
        self.ask_name("New Profile", "A profile keeps its own cookies and logins: sign in once, and it stays signed "
                      "in.", "Add", "", lambda name: (add_profile(name), self.switch_profile(name)))

    def ask_rename(self):
        def rename(name):
            rename_profile(self.profile, name)
            self.profile = name
            self.refresh_profiles()
        self.ask_name("Rename Profile", "Its cookies and logins stay with it.", "Rename", self.profile, rename)

    def ask_remove(self):
        name = self.profile
        dlg = Adw.AlertDialog(heading=f"Remove “{name}”?",
                              body="Its cookies, logins and the sites' data it keeps are deleted. This can't be undone.",
                              default_response="cancel", close_response="cancel")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("remove", "Remove")
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
        lock = lock_profile(pid)
        if lock is None:
            self.say("Still open", f"“{name}” is open in another Emission window, so it's kept. Close that window "
                     "and try again.")
            return

        tries = [15]

        def removed(why):
            if why and self.Engine is MacWeb and tries[0] > 0:  # (WebKit lets go of a store a moment after its pages)
                tries[0] -= 1
                self.later.append((GLib.get_monotonic_time() + 1_000_000, lambda: MacWeb.remove_store(pid, removed)))
                return
            unlock_profile(lock)
            if why:
                self.say(f"“{name}” wasn't removed", why)
                return
            forget_profile(name)
            try:
                os.unlink(os.path.join(data_home(), "profiles", pid + ".lock"))
            except OSError:
                pass
            self.refresh_profiles()
        if self.Engine is MacWeb:
            MacWeb.remove_store(pid, removed)
        else:
            GtkWeb.forget_session(pid)
            shutil.rmtree(GtkWeb.folder(pid), ignore_errors=True)
            removed(None)

    def switch_profile(self, name):
        """Its tabs again, in another profile (its own cookies: a page signed in there is again there). False if
        that profile's open in another Emission."""
        if name == self.profile:
            return True
        pid = profile_id(name)
        lock = lock_profile(pid) if pid else None
        if lock is None:
            self.say("Open elsewhere", f"The profile “{name}” is open in another Emission window.")
            return False
        urls = [t.last.get("url") or HOME_PAGE for t in self.tabs.values()]
        self.switching = True
        try:
            for page in list(self.tabs):
                self.view.close_page(page)
        finally:
            self.switching = False
        unlock_profile(self.lock)
        self.lock, self.profile, self.profile_id = lock, name, pid
        self.refresh_profiles()
        for url in urls or [HOME_PAGE]:
            self.open(url)
        return True

    # (asking the user: drawn by GTK in the window, so a macOS page, a view laid over it, is hidden meanwhile)

    def present_dialog(self, dlg, web=None):
        """dlg shown; web: the page it's for (its tab closed, the dialog's closed too: answered as cancelled)."""
        if (tab := self.current()) is not None:
            tab.web.show(False)
        if web is not None:
            self.asking.setdefault(web, []).append(dlg)
        dlg.connect("closed", lambda *_: (web is not None and dlg in self.asking.get(web, ()) and
                                          self.asking[web].remove(dlg), self.selected()))
        dlg.present(self)

    def say(self, heading, body):
        dlg = Adw.AlertDialog(heading=heading, body=body)
        dlg.add_response("ok", "OK")
        self.present_dialog(dlg)

    def host_of(self, web):
        url = web.state().get("url") or ""
        return urllib.parse.urlsplit(url).hostname or "This page"

    def page_dialog(self, web, kind, message, text, finish):
        """A page's alert, confirm or prompt: finish(None for an alert; True or False; the text, or None)."""
        dlg = Adw.AlertDialog(heading=f"{self.host_of(web)} says", body=message, close_response="cancel")
        entry = None
        if kind == "prompt":
            entry = Gtk.Entry(text=text or "", activates_default=True)
            dlg.set_extra_child(entry)
        if kind != "alert":
            dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", "OK")
        dlg.set_default_response("ok")
        if kind == "alert":
            dlg.set_close_response("ok")
        dlg.connect("response", lambda _d, r: finish(None if kind == "alert" else (r == "ok") if kind == "confirm"
                                                     else (entry.get_text() if r == "ok" else None)))
        self.present_dialog(dlg, web)

    def sign_in(self, web, host, realm, failed_before, answer):
        """A site's own sign-in (HTTP authentication): answer(user, password), or (None, None). Kept for the session
        only."""
        user = Gtk.Entry(placeholder_text="User name", activates_default=True)
        password = Gtk.PasswordEntry(placeholder_text="Password", activates_default=True, show_peek_icon=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(user)
        box.append(password)
        body = (f"“{realm}” on " if realm else "") + host + " asks you to sign in."
        if failed_before:
            body = "That didn't work. " + body
        dlg = Adw.AlertDialog(heading="Sign In", body=body, extra_child=box, default_response="ok",
                              close_response="cancel")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", "Sign In")
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", lambda _d, r: answer(user.get_text(), password.get_text()) if r == "ok"
                    else answer(None, None))
        self.present_dialog(dlg, web)

    def choose_files(self, multiple, folders, finish):
        """Files for a page's file input: finish([paths]) or finish(None)."""
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
        """A download finished (or why it didn't): said in the banner for a while."""
        name = os.path.basename(path)
        self.banner.set_title(f"Couldn't download {name}: {why}" if why else f"Saved {name} to Downloads")
        self.banner.set_button_label(None if why else "Show")
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
        page.set_title("New Tab")
        self.tabs[page] = tab
        self.view.set_selected_page(page)
        return tab

    def current(self):
        page = self.view.get_selected_page()
        return self.tabs.get(page) if page is not None else None

    def selected(self):
        for page, tab in self.tabs.items():
            tab.web.show(page is self.view.get_selected_page())
        if (tab := self.current()) is not None:
            self.show_state(tab, tab.last, force=True)

    def closing(self, view, page):
        tab = self.tabs.pop(page, None)
        if tab is not None:
            for dlg in self.asking.pop(tab.web, []):
                dlg.force_close()
            tab.web.close()
        view.close_page_finish(page, True)
        if not self.tabs and not self.switching:
            self.close()
        return True

    def closing_window(self, *_):
        for tab in self.tabs.values():
            tab.web.close()
        self.tabs.clear()
        unlock_profile(self.lock)
        self.lock = None
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
        if not self.tabs and self.lock is None:
            return False
        if self.later:
            now = GLib.get_monotonic_time()
            due = [fn for when, fn in self.later if when <= now]
            self.later = [(when, fn) for when, fn in self.later if when > now]
            for fn in due:
                fn()
        for page, tab in list(self.tabs.items()):
            tab.web.follow()
            now = tab.web.state()
            if now != tab.last:
                page.set_title(now["title"] or now["url"] or "New Tab")
                page.set_loading(now["loading"])
                if tab is self.current():
                    self.show_state(tab, now)
                tab.last = now
        return True

    def show_state(self, tab, now, force=False):
        if not now:
            return
        if force or not self.entry.get_delegate().has_focus():  # (what's being typed there left alone)
            self.entry.set_text(now.get("url") or "")
        self.back_btn.set_sensitive(now.get("back", False))
        self.fwd_btn.set_sensitive(now.get("forward", False))
        self.reload_btn.set_icon_name("process-stop-symbolic" if now.get("loading") else "view-refresh-symbolic")
        self.progress.set_visible(now.get("loading", False))
        self.progress.set_fraction(now.get("progress") or 0)
        self.set_title(f"{now.get('title') or 'Emission'} — {self.profile}")


class App(Adw.Application):
    def __init__(self, url, profile):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.url, self.profile = url, profile

    def do_activate(self):
        Window(self, self.url, self.profile).present()


def with_display():
    """On Linux with no display, this run again under a virtual one of its own (xvfb-run), or why it can't be."""
    if sys.platform == "darwin" or os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") \
            or os.environ.get("EMISSION_VIRTUAL_DISPLAY"):
        return None
    if shutil.which("xvfb-run") is None:
        return "There's no display here, and xvfb-run (package xvfb) isn't installed to make one."
    env = {**os.environ, "EMISSION_VIRTUAL_DISPLAY": "1", "GDK_BACKEND": "x11"}
    os.execvpe("xvfb-run", ["xvfb-run", "--auto-servernum", "--server-args=-screen 0 1280x900x24", sys.executable,
                            os.path.abspath(__file__), *sys.argv[1:]], env)


def main():
    p = argparse.ArgumentParser(description="Emission, Baude's browser")
    p.add_argument("url", nargs="?")
    p.add_argument("--profile", default=DEFAULT, help="whose cookies and logins (a profile of its own each)")
    a = p.parse_args()
    if (why := with_display()) is not None:
        print(why, file=sys.stderr)
        return 1
    if not a.profile.strip():
        p.error("a profile needs a name")
    add_profile(a.profile.strip())
    return App(a.url, a.profile.strip()).run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
