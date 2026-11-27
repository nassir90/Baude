"""emissionmac — Emission's pages on macOS: Apple's WKWebView, put in a GTK window through the Objective-C runtime
(MacWeb), the blocks WebKit's delegates are handed (Blocks), keys as macOS has them (MAC_KEYS…), and what's drawn over a
hosted page (MacOverlay).
"""
import ctypes
import os
import sys
import time

import macos  # noqa: E402  (no GTK: before it's loaded)
from gi.repository import GLib  # noqa: E402
from emissioncore import AGENT, CONSOLE_JS, download_path, failure_page, for_bots, objc_sel, once  # noqa: E402
from emissionpointer import (  # noqa: E402
    ARROW_EDGE, ARROW_SHADOW, GLOW_REACH, HINT_EDGE, RING_EDGE, arrow_at, chevrons, glow_pixels, hint_at,
)


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
            cls.allow_http()
            cls.loaded = True

    @staticmethod
    def allow_http():
        """Plain http opens as in any browser (a dev server on app.localhost, a box on the LAN, any address typed):
        App Transport Security, which WebKit's network process takes from this process's bundle (Python's, which
        says nothing of it), is told in memory, before WebKit's first asked, that a web view's loads are anyone's
        (NSAllowsArbitraryLoadsInWebContent: what's meant for a browser; local networking alone leaves *.localhost
        out)."""
        send = macos.objc_send
        ats = send(b"NSDictionary", b"dictionaryWithObject:forKey:",
                   send(b"NSNumber", b"numberWithBool:", True, types=(ctypes.c_bool,)),
                   macos.ns_string("NSAllowsArbitraryLoadsInWebContent"), types=(ctypes.c_void_p, ctypes.c_void_p))
        if info := send(send(b"NSBundle", b"mainBundle"), b"infoDictionary"):
            send(info, b"setObject:forKey:", ats, macos.ns_string("NSAppTransportSecurity"),
                 types=(ctypes.c_void_p, ctypes.c_void_p))

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
        self.on_top()
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

    ON_TOP = 1000.0

    def on_top(self):
        """Its page drawn over GTK's own picture of the window. GTK draws the window as tiles, layers beside the page's
        in the content view's layer, and adds new ones at the top whenever it lays them out again (the window resized,
        maximized, or dragged out of maximized by its bar), which would hide the page under them though it's there and
        running. Raised above all of them, whatever order they're in (zPosition)."""
        layer = macos.objc_send(self.view, b"layer")
        if layer and macos.objc_send(layer, b"zPosition", ret=ctypes.c_double) != self.ON_TOP:
            macos.objc_send(layer, b"setZPosition:", self.ON_TOP, types=(ctypes.c_double,))

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
