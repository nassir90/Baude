"""emissiongtk — Emission's pages on Linux: WebKitGTK 6.0's (GtkWeb), real input on Emission's own X display
(XTest); and what a window has on either engine: a tab (Tab) and the bars (Chrome).
"""
import ctypes
import os
import shutil
import time

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402
from baudelib import errorbook, wording  # noqa: E402  (its words, Baude's browser's: schema/copy)
from baudelib.emissionlink import blocks_tracking, data_home, note_leftover  # noqa
from emissioncore import CONSOLE_JS, address, download_path, failure_page, for_bots, once  # noqa: E402
from emissionmac import MacWeb  # noqa: E402


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
            cls.track(net, blocks_tracking(profile))
            net.connect("download-started", cls.download_started)
            cls.sessions[profile] = net
        return cls.sessions[profile]

    @staticmethod
    def track(net, on):
        """net's cookies under another site's page (an app's preview on another host signing in) kept, as a browser
        without tracking prevention keeps them, or (on) refused, and WebKit's tracking prevention on."""
        from gi.repository import WebKit
        net.set_itp_enabled(on)
        net.get_cookie_manager().set_accept_policy(
            WebKit.CookieAcceptPolicy.NO_THIRD_PARTY if on else WebKit.CookieAcceptPolicy.ALWAYS)

    @classmethod
    def block_tracking(cls, profile, on):
        """As MacWeb's: profile's pages from now on blocking cross-site cookies (on), or not."""
        if profile in cls.sessions:
            cls.track(cls.sessions[profile], on)
        elif profile in cls.given_back:
            cls.track(cls.given_back[profile], on)

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
        errorbook.note("page_load", f"{error.domain}", error.message, key=f"{error.domain}:{error.code}",
                       code=error.code)
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
        self.painted, self.ticking = None, None  # (hosted on macOS: frames)
        self.fading = None  # (a timeout, at the Bot's pointer's idle time's end: frames then, to fade it out)
        if win.Engine is MacWeb:  # (its view kept over its place as soon as that's shown, hidden or moved: hosted, in a
            for signal in ("map", "unmap"):  # side panel that's resized and switched, every frame it's shown)
                self.place.connect(signal, lambda *_: self.web.follow())
            if win.hosted:  # (followed while it's shown in Baude's window, not in its own, never shown: after each
                # frame Baude's window draws, a resize, a panel dragged, a toast coming, and each frame while the
                # Bot's pointer moves (kick); none's drawn for it otherwise, so a page left as it is costs nothing)
                self.place.connect("map", lambda p: self.frames(True))
                self.place.connect("unmap", lambda p: self.frames(False))
                self.place.connect("destroy", lambda p: self.unfade())
        self.web.window, self.web.tab_id = win, self.id
        self.web.popup = lambda made_with: win.open(None, made_with).web
        self.web.closed = lambda: win.close_tab(self)
        if win.hosted:
            self.web.follow()  # (out of sight from the start: parked, at the Bot's own size)
        self.last = {}
        if url is not None:
            self.web.load(address(url))

    def frames(self, on):
        """(hosted) Its page followed after each frame of the window its place is in (on), or not (off: unmapped)."""
        if self.painted is not None:
            clock, handler = self.painted
            clock.disconnect(handler)
            self.painted = None
        if self.ticking is not None:
            self.place.remove_tick_callback(self.ticking)
            self.ticking = None
        if not on:
            self.unfade()
        clock = self.place.get_frame_clock() if on else None
        if clock is not None:
            self.painted = (clock, clock.connect("after-paint", lambda *_: self.web.follow()))
            self.kick()

    def kick(self):
        """(hosted) Frames drawn while the Bot's pointer or the glow moves, each following it, till they're still."""
        if self.painted is None:
            return
        if self.ticking is None and self.win.pointer.busy():
            self.ticking = self.place.add_tick_callback(self.tick)
        pointer = self.win.pointer  # (its idle time ending isn't a move: frames are asked for then, to fade it out)
        self.unfade()
        left = pointer.seen + pointer.IDLE_S - time.monotonic()
        if left > 0:
            self.fading = GLib.timeout_add(int(left * 1000) + 20, self.faded)

    def unfade(self):
        if self.fading is not None:
            GLib.source_remove(self.fading)
            self.fading = None

    def faded(self):
        self.fading = None
        self.kick()
        return False

    def tick(self, *_):
        if self.win.pointer.busy():  # (a frame, followed once it's drawn: frames' after-paint)
            return True
        self.ticking = None
        self.web.follow()  # (as it's left)
        return False


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
        self.tab_bar = Adw.TabBar(autohide=True)  # (one tab: no strip, as Multiplex's window)
        self.strip = Gtk.Overlay(child=self.tab_bar)
        self.head = Gtk.Overlay(child=self.bar)  # (the bar, as it's put in the window: chrome.head, not chrome.bar)
        self.progress = Gtk.ProgressBar(visible=False, valign=Gtk.Align.END, can_target=False)
        self.progress.add_css_class("osd")
        self.strip.add_overlay(self.progress)  # (over the strip's foot, or the bar's while there's no strip: the page
        self.tab_bar.connect("notify::tabs-revealed", lambda *_: self.place_progress())  # doesn't move as it shows)
        self.place_progress()

    def place_progress(self):
        want = self.strip if self.tab_bar.get_tabs_revealed() else self.head
        if self.progress.get_parent() is not want:
            if (was := self.progress.get_parent()) is not None:
                was.remove_overlay(self.progress)
            want.add_overlay(self.progress)

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
