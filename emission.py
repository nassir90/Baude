#!/usr/bin/env python3
"""Emission: Baude's browser, a GTK window of its own (and, later, one of Baude's side panel's pages): tabs, back and
forward, reload, an address that's a search too, and profiles, each its own cookies and logins kept (a login made once,
stored, as a Bot's identity will use it). Its pages are WebKit's: on macOS Apple's WKWebView (MacWeb), put in the
window through the Objective-C runtime (macos.objc_send: there's no PyObjC); on Linux WebKitGTK 6.0 (GtkWeb), a GTK
widget of its own. On a host with no display, xvfb-run gives it one.

    python3 emission.py [URL] [--profile NAME]
"""
import argparse
import ctypes
import html
import json
import os
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
PROFILE_SPACE = uuid.UUID("6b1f0d3e-3c4e-4f51-9b0e-7a1e5e6d2c11")  # (a profile's store: its name's uuid in this)
POLL_MS = 200  # (a page's title, address and progress looked at this often: no delegate to be told)


def data_home():
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "baude", "emission")


def profiles():
    """The profiles there are (profiles.json's), "Default" first: each a name, its cookies and logins its own."""
    try:
        with open(os.path.join(data_home(), "profiles.json"), encoding="utf-8") as f:
            names = [n for n in json.load(f).get("profiles") or [] if isinstance(n, str) and n.strip()]
    except (OSError, ValueError):
        names = []
    return ["Default", *[n for n in names if n != "Default"]]


def add_profile(name):
    names = [n for n in profiles() if n != "Default"]
    if name not in names and name != "Default":
        os.makedirs(data_home(), exist_ok=True)
        with open(os.path.join(data_home(), "profiles.json"), "w", encoding="utf-8") as f:
            json.dump({"profiles": [*names, name]}, f)


def address(text):
    """What's typed in the address bar as a URL: one as it is, a host given https://, anything else searched for."""
    text = text.strip()
    if not text:
        return HOME_PAGE
    if "://" in text or text.startswith(("about:", "file:")):
        return text
    if " " not in text and ("." in text or text.startswith("localhost")):
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


class MacWeb:
    """One page in a WKWebView, laid over its place in a GTK window (place: the widget it's drawn over), with the
    store of its profile (WKWebsiteDataStore dataStoreForIdentifier:, macOS 14: kept on disk, apart from others').
    Its tab sets popup (a page this one opens in a window of its own: a new MacWeb, given the configuration WebKit
    made for it) and closed (the page closed itself)."""
    loaded = False
    delegate = None
    views = {}  # (each view's MacWeb, by its pointer: what the delegate's told about)
    kept = []  # (the delegate's methods: kept while it's there)

    @classmethod
    def load_kit(cls):
        if not cls.loaded:
            ctypes.cdll.LoadLibrary("/System/Library/Frameworks/WebKit.framework/WebKit")
            ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Foundation.framework/Foundation")
            cls.loaded = True

    @classmethod
    def make_delegate(cls):
        """One object, every view's navigation and UI delegate: a load that failed, a page opening another in a
        window of its own, a page that closed itself."""
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
                imp = ctypes.CFUNCTYPE(ret, *[P] * (nargs + 2))(fn)
                cls.kept.append(imp)
                rt.class_addMethod(klass, rt.sel_registerName(name), ctypes.cast(imp, P).value,
                                   (b"@" if ret else b"v") + b"@:" + b"@" * nargs)

            add(b"webView:didFailProvisionalNavigation:withError:", lambda _s, _c, v, _n, e: cls.failed(v, e), 3)
            add(b"webView:didFailNavigation:withError:", lambda _s, _c, v, _n, e: cls.failed(v, e), 3)
            add(b"webView:createWebViewWithConfiguration:forNavigationAction:windowFeatures:",
                lambda _s, _c, v, config, _a, _f: cls.opened(v, config), 4, ret=P)
            add(b"webViewDidClose:", lambda _s, _c, v: cls.views.get(v) and GLib.idle_add(cls.views[v].closed), 1)
            rt.objc_registerClassPair(klass)
        cls.delegate = macos.objc_send(macos.objc_send(b"EmissionDelegate", b"alloc"), b"init")
        return cls.delegate

    @classmethod
    def failed(cls, view, error):
        web = cls.views.get(view)
        send = macos.objc_send
        domain = cls._text(send(send(error, b"domain"), b"description"))
        code = send(error, b"code", ret=ctypes.c_long)
        if web is None or (domain == "NSURLErrorDomain" and code == -999) or domain == "WebKitErrorDomain":
            return  # (a load stopped, or handed to another: no failure)
        info = send(error, b"userInfo")
        url = cls._text(send(info, b"objectForKey:", macos.ns_string("NSErrorFailingURLStringKey"),
                             types=(ctypes.c_void_p,))) or web.asked
        web.failure = url
        send(web.view, b"loadHTMLString:baseURL:", macos.ns_string(
            failure_page(url, cls._text(send(error, b"localizedDescription")))), None,
            types=(ctypes.c_void_p, ctypes.c_void_p))

    @classmethod
    def opened(cls, view, config):
        web = cls.views.get(view)
        new = web.popup(config) if web is not None and web.popup else None
        return new.view if new is not None else None

    def __init__(self, place, profile, opener=None):
        """opener: the configuration WebKit made for a page another opened (its profile's store already in it)."""
        self.load_kit()
        send, R = macos.objc_send, macos._rect()
        self.place, self.view, self.shown = place, None, True
        self.popup = self.closed = None
        self.asked, self.failure = "", None
        if opener:
            config = opener
        else:
            config = send(send(b"WKWebViewConfiguration", b"alloc"), b"init")
            ident = str(uuid.uuid5(PROFILE_SPACE, profile))
            nsuuid = send(send(b"NSUUID", b"alloc"), b"initWithUUIDString:", macos.ns_string(ident),
                          types=(ctypes.c_void_p,))
            store = send(b"WKWebsiteDataStore", b"dataStoreForIdentifier:", nsuuid, types=(ctypes.c_void_p,))
            if store:
                send(config, b"setWebsiteDataStore:", store, types=(ctypes.c_void_p,))
        self.view = send(send(b"WKWebView", b"alloc"), b"initWithFrame:configuration:", R(0, 0, 10, 10), config,
                         types=(R, ctypes.c_void_p))
        send(self.view, b"setAllowsBackForwardNavigationGestures:", True, types=(ctypes.c_bool,))
        delegate = self.make_delegate()
        send(self.view, b"setNavigationDelegate:", delegate, types=(ctypes.c_void_p,))
        send(self.view, b"setUIDelegate:", delegate, types=(ctypes.c_void_p,))
        self.views[self.view] = self
        self.attached = None

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
        """Its frame kept over its place (the window resized, the bar's height changed)."""
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
        send(self.view, b"setFrame:", frame, types=(R,))
        send(self.view, b"setHidden:", not self.shown, types=(ctypes.c_bool,))

    def show(self, on):
        self.shown = on
        if self.attached is not None:
            macos.objc_send(self.view, b"setHidden:", not on, types=(ctypes.c_bool,))

    def load(self, url):
        send = macos.objc_send
        self.asked, self.failure = url, None
        nsurl = send(b"NSURL", b"URLWithString:", macos.ns_string(url), types=(ctypes.c_void_p,))
        if nsurl and url.startswith("file:"):
            folder = send(nsurl, b"URLByDeletingLastPathComponent")  # (what it may read: its own folder)
            send(self.view, b"loadFileURL:allowingReadAccessToURL:", nsurl, folder,
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
        """{title, url, progress, loading, back, forward}: as the page is now (a failed one: the address asked for)."""
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


class GtkWeb:
    """One page in WebKitGTK 6.0's WebView, in its place in the window, with its profile's own network session (its
    cookies, its logins, its cache: kept on disk under data_home(), apart from others'). Its tab sets popup and
    closed, as MacWeb's."""
    sessions = {}

    def __init__(self, place, profile, opener=None):
        """opener: the view that opened this page in a window of its own (its session, and its window.opener)."""
        from gi.repository import WebKit
        self.WebKit = WebKit
        self.popup = self.closed = None
        if opener is not None:
            self.view = WebKit.WebView(related_view=opener, hexpand=True, vexpand=True)
        else:
            if profile not in self.sessions:
                root = os.path.join(data_home(), "profiles", str(uuid.uuid5(PROFILE_SPACE, profile)))
                net = WebKit.NetworkSession.new(os.path.join(root, "data"), os.path.join(root, "cache"))
                net.get_cookie_manager().set_persistent_storage(os.path.join(root, "cookies.sqlite"),
                                                                WebKit.CookiePersistentStorage.SQLITE)
                self.sessions[profile] = net
            self.view = WebKit.WebView(network_session=self.sessions[profile], hexpand=True, vexpand=True)
        self.view.connect("create", lambda v, _action: (new := self.popup and self.popup(v)) and new.view)
        self.view.connect("close", lambda _v: self.closed and GLib.idle_add(self.closed))
        self.view.connect("load-failed", self.failed)
        place.append(self.view)

    def failed(self, view, _event, url, error):
        W = self.WebKit
        if error.matches(W.NetworkError.quark(), W.NetworkError.CANCELLED) or error.matches(
                W.PolicyError.quark(), W.PolicyError.FRAME_LOAD_INTERRUPTED_BY_POLICY_CHANGE):
            return False  # (a load stopped, or handed to another: no failure)
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
        self.view.try_close()


def engine():
    """The page engine here: MacWeb on macOS, GtkWeb where WebKitGTK 6.0 is; None if neither (said by the window)."""
    if sys.platform == "darwin":
        return MacWeb
    try:
        gi.require_version("WebKit", "6.0")
        return GtkWeb
    except ValueError:
        return None


class Tab:
    """A tab: its page, and where it's drawn. url None: a page another opened, WebKit loading it (opener: what the
    engine was given to make it with)."""
    def __init__(self, win, url, opener=None):
        self.win = win
        self.place = Gtk.Box(hexpand=True, vexpand=True)  # (where its page is drawn: MacWeb lays its view over it)
        self.web = win.Engine(self.place, win.profile, opener)
        self.web.popup = lambda made_with: win.open(None, made_with).web
        self.web.closed = lambda: win.close_tab(self)
        self.last = {}
        if url is not None:
            self.web.load(address(url))


class Window(Adw.ApplicationWindow):
    def __init__(self, app, url, profile):
        super().__init__(application=app, title="Emission", default_width=1200, default_height=820)
        self.profile, self.Engine, self.tabs = profile, engine(), {}
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
        strip = Adw.TabBar(view=self.view, autohide=False)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.progress = Gtk.ProgressBar(visible=False)
        self.progress.add_css_class("osd")
        frame = Adw.ToolbarView(content=body)  # (its bar: back, forward, reload, the address, its profile)
        frame.add_top_bar(bar)
        frame.add_top_bar(strip)
        body.append(self.progress)
        if self.Engine is None:
            body.append(Adw.StatusPage(icon_name="web-browser-symbolic", title="No page engine here",
                                       description="Emission draws pages with WebKit: Apple's WKWebView on macOS, "
                                                   "WebKitGTK 6.0 on Linux (its package: webkitgtk-6.0 on Arch, "
                                                   "gir1.2-webkit-6.0 on Debian and Ubuntu)."))
        else:
            body.append(self.view)
        self.set_content(frame)
        for name, keys, fn in (("new-tab", ["<Meta>t", "<Control>t"], lambda: self.open(HOME_PAGE)),
                               ("close-tab", ["<Meta>w", "<Control>w"], self.close_current),
                               ("address", ["<Meta>l", "<Control>l"], lambda: self.entry.grab_focus()),
                               ("reload", ["<Meta>r", "<Control>r", "F5"], lambda: self.current()
                                and self.current().web.reload())):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda *_a, fn=fn: fn())
            self.add_action(act)
            app.set_accels_for_action(f"win.{name}", keys)
        if self.Engine is not None:
            self.open(url or HOME_PAGE)
            GLib.timeout_add(POLL_MS, self.tick)

    def profile_menu(self):
        menu, group = Gio.Menu(), Gio.SimpleActionGroup()
        for i, name in enumerate(profiles()):
            act = Gio.SimpleAction.new(f"p{i}", None)
            act.connect("activate", lambda *_a, n=name: self.switch_profile(n))
            group.add_action(act)
            menu.append(name, f"who.p{i}")
        add = Gio.SimpleAction.new("add", None)
        add.connect("activate", lambda *_: self.ask_profile())
        group.add_action(add)
        more = Gio.Menu()
        more.append("New Profile…", "who.add")
        menu.append_section(None, more)
        self.insert_action_group("who", group)
        return menu

    def ask_profile(self):
        entry = Gtk.Entry(placeholder_text="Name", activates_default=True)
        dlg = Adw.AlertDialog(heading="New Profile", body="A profile keeps its own cookies and logins: sign in once, "
                              "and it stays signed in.", extra_child=entry, default_response="add",
                              close_response="cancel")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("add", "Add")
        dlg.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", lambda _d, r: r == "add" and entry.get_text().strip() and (
            add_profile(entry.get_text().strip()), self.switch_profile(entry.get_text().strip())))
        dlg.present(self)

    def switch_profile(self, name):
        """Its tabs again, in another profile (its own cookies: a page signed in there is again there)."""
        if name == self.profile:
            return
        urls = [t.last.get("url") or HOME_PAGE for t in self.tabs.values()]
        for page in list(self.tabs):
            self.view.close_page(page)
        self.profile = name
        self.who.set_label(name)
        self.who.set_menu_model(self.profile_menu())
        for url in urls or [HOME_PAGE]:
            self.open(url)

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
            tab.web.close()
        view.close_page_finish(page, True)
        if not self.tabs:
            self.close()
        return True

    def close_tab(self, tab):
        for page, t in self.tabs.items():
            if t is tab:
                self.view.close_page(page)
                break

    def close_current(self):
        if (page := self.view.get_selected_page()) is not None:
            self.view.close_page(page)

    def tick(self):
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
        if force or not self.entry.has_focus():
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


def main():
    p = argparse.ArgumentParser(description="Emission, Baude's browser")
    p.add_argument("url", nargs="?")
    p.add_argument("--profile", default="Default", help="whose cookies and logins (a profile of its own each)")
    a = p.parse_args()
    if a.profile != "Default":
        add_profile(a.profile)
    return App(a.url, a.profile).run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
