"""Terminals on a host, in Baude's and Record's windows (☰ → Terminal): TerminalView, over the window as the
Kubernetes and Database views are, with tabs of Multiplex terminals (TerminalPane: multiplexterm.HostedTerminal, each
run on a host by its host process and drawn here), each new one on the host picked in the window's host dropdown;
or out in a window of their own (TerminalWindow: Multiplex's look), as a Bot's browser goes out
(browserpane.BrowserWindow), put back over the window when it's closed.
"""
import sys

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk, Pango  # noqa: E402
from baudelib import wire, wording  # noqa: E402
import widgets  # noqa: E402

OUT_TIP = wording.say("common.move_out")  # (as a Bot's browser's button says it)


class TerminalPane(Gtk.Box):
    """The tabs (a strip only with more than one, as Emission's and Multiplex's): a new one on host() → (where, its
    name). changed(): a tab's come, gone or been picked; empty(): the last one's closed."""

    def __init__(self, host, changed=None, empty=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.host, self.changed, self.empty = host, changed, empty
        self.tabs = Adw.TabView(hexpand=True, vexpand=True)
        self.tabs.connect("close-page", self._closing)
        self.tabs.connect("notify::selected-page", lambda *_: (self._focus(), self.changed and self.changed()))
        self.tabs.set_shortcuts(Adw.TabViewShortcuts.NONE)  # (the window's own keys: its tabs', its chats')
        self.append(Adw.TabBar(view=self.tabs, autohide=True))
        self.append(self.tabs)
        import multiplexterm
        multiplexterm.shortcuts(self, {"t": self.open, "w": self.close_current})

    def close_current(self):
        if page := self.tabs.get_selected_page():
            self.tabs.close_page(page)

    def open(self, where=None, name=None, attach=None):
        """A new terminal on where (the host picked, if none): its login shell there; attach: the one there by that
        id, attached to (its tab, if one's open here already, picked)."""
        import multiplexterm
        if where is None:
            where, name = self.host()
        if attach is not None:
            for i in range(self.tabs.get_n_pages()):
                page = self.tabs.get_nth_page(i)
                if getattr(page.get_child(), "host_id", None) == attach:
                    self.tabs.set_selected_page(page)
                    return page.get_child()
        term = multiplexterm.HostedTerminal(where, attach=attach, host_name=name, on_exit=self._ended,
                                            on_title=self._titled, on_close=self._finish)
        multiplexterm.Terminals.get(wire.HEAD_CLIENT).add(term)
        page = self.tabs.append(term)
        page.set_title(term.title())
        page.set_tooltip(name or "")
        self.tabs.set_selected_page(page)
        term.vte.grab_focus()
        return term

    def current(self):
        page = self.tabs.get_selected_page()
        return page.get_child() if page is not None else None

    def page_of(self, term):
        for i in range(self.tabs.get_n_pages()):
            if self.tabs.get_nth_page(i).get_child() is term:
                return self.tabs.get_nth_page(i)
        return None

    def _titled(self, term):
        if (page := self.page_of(term)) is not None:
            page.set_title(term.title() + (f" ({wording.say('multiplex.ended')})" if term.status is not None else ""))

    def _ended(self, term, _status):
        if term.closing or term.bot is None:  # (as Multiplex's window: one a Bot's at stays, to be read)
            self._finish(term)
        else:
            self._titled(term)

    def _finish(self, term):
        if (page := self.page_of(term)) is not None:
            self.tabs.close_page(page)

    def _focus(self):
        if (term := self.current()) is not None:
            term.vte.grab_focus()

    def _closing(self, tabs, page):
        import multiplexterm
        term = page.get_child()
        term.on_exit = term.on_title = term.on_close = None  # (gone from here: its end isn't this pane's)
        term.stop()
        multiplexterm.Terminals.get().remove(term)
        tabs.close_page_finish(page, True)
        if self.changed:
            self.changed()
        if tabs.get_n_pages() == 0 and self.empty is not None:
            self.empty()
        return True

    def close_all(self):
        for i in reversed(range(self.tabs.get_n_pages())):
            self.tabs.close_page(self.tabs.get_nth_page(i))


class TerminalView(Gtk.Box):
    """☰ → Terminal: over the window, as the Kubernetes view is. Its bar: its name, the host the terminal shown is
    on, a new tab (on the host picked: host()), out to a window of its own, close. Closed, its terminals keep running
    (on their hosts) and are there again when it opens; the last one's closing closes it."""

    def __init__(self, win, host):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, visible=False, focusable=True)
        self.add_css_class("baude-frame")  # (the bar: theming.CSS; the terminals the inside, set into it)
        self.win, self.window = win, None
        bar = widgets.view_bar()
        title = Gtk.Label(label=wording.say("multiplex.terminal_menu"))
        title.add_css_class("heading")
        bar.append(title)
        self.host_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.host_label.add_css_class("dim-label")
        bar.append(self.host_label)
        bar.append(Gtk.Box(hexpand=True))
        new = Gtk.Button(icon_name="tab-new-symbolic", tooltip_text=wording.say("browser.new_tab"))
        new.connect("clicked", lambda *_: self.pane.open())
        out = Gtk.Button(icon_name="window-new-symbolic", tooltip_text=OUT_TIP)
        out.connect("clicked", lambda *_: self.put_out())
        self.buttons, self.missing = (new, out), None  # (missing: what's shown without VTE: show_missing)
        for b in self.buttons:
            b.add_css_class("flat")
            bar.append(b)
        close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text=wording.say("images.close"))
        close.add_css_class("circular")
        close.connect("clicked", lambda *_: self.close())
        bar.append(close)
        self.append(Gtk.WindowHandle(child=bar))
        self.pane = TerminalPane(host, changed=self.show_host, empty=self.emptied)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.body.append(self.pane)
        widgets.inside(self.body)
        self.append(self.body)
        keys = Gtk.EventControllerKey()  # (Esc that the terminal didn't take: none does, so only with it unfocused)
        keys.connect("key-pressed", lambda _c, kv, _k, _s: (self.close(), True)[1] if kv == Gdk.KEY_Escape else False)
        self.add_controller(keys)
        win.connect("close-request", lambda *_: self.window_closing() or False)

    def window_closing(self):
        """Its window's closing: its terminals ended with it, or, out in a window of their own, they carry on there
        and end as that's closed."""
        pane = self.pane
        pane.changed = pane.empty = None  # (this view's going)
        if self.window is not None:
            out, self.window = self.window, None
            out.back = lambda p: (setattr(p, "empty", None), p.close_all())
            pane.empty = out.close  # (its last tab closed: its window too)
        else:
            pane.close_all()

    def show_host(self):
        term = self.pane.current()
        self.host_label.set_label((term.host_name or "") if term is not None else "")

    def open(self, where=None, name=None):
        """Shown (or, out, its window brought up) on a terminal on where (the host picked, if none): the one shown if
        it's there, else another of that host's, else a new one there."""
        import multiplexterm
        if (dep := multiplexterm.missing()) is not None:  # (no terminals: what's missing shown in their place)
            self.show_missing(dep)
            return
        if self.window is not None:
            self.window.present()
        else:
            self.set_visible(True)
        self.on_host(*((where, name) if where is not None else self.pane.host()))

    def on_host(self, where, name):
        """A terminal of yours on where shown: the one shown if it's that, else another there, else a new one. Yours:
        opened here (not one attached to, a Bot's), no Bot at its tools, its program still running."""
        def yours(term):
            return getattr(term, "host_where", None) == where and getattr(term, "attach_to", None) is None and \
                term.bot is None and term.status is None and not getattr(term, "stopped", False)
        tabs = self.pane.tabs
        current = self.pane.current()
        if current is not None and yours(current):
            return self.pane._focus()
        for i in range(tabs.get_n_pages()):
            if yours(tabs.get_nth_page(i).get_child()):
                return tabs.set_selected_page(tabs.get_nth_page(i))
        self.pane.open(where, name)

    def show(self, where, name, tid):
        """Shown on the terminal tid on where (named name): its tab, or one attached to it."""
        import multiplexterm
        if (dep := multiplexterm.missing()) is not None:
            self.show_missing(dep)
            return
        if self.window is not None:
            self.window.present()
        else:
            self.set_visible(True)
        self.pane.open(where, name, attach=tid)

    def show_missing(self, dep):
        """VTE isn't here: the view shown with what to install in place of its tabs, and nothing to open."""
        import depsview
        if self.missing is None:
            self.missing = depsview.Missing()
            self.body.remove(self.pane)
            self.body.append(self.missing)
            for b in self.buttons:
                b.set_sensitive(False)
        self.missing.show(dep, "multiplex.terminal_menu")
        self.set_visible(True)

    def close(self):
        self.set_visible(False)
        self.win.input.grab_focus()

    def find(self):
        return False

    def emptied(self):
        if self.window is not None:
            window, self.window = self.window, None
            window.back = None
            window.close()
            self.body.append(self.pane)
        self.close()

    def put_out(self):
        """Out to a window of its own (Multiplex's look); closed, it's put back here."""
        self.body.remove(self.pane)
        self.window = TerminalWindow(self.win.get_application(), self.pane, back=self.put_back)
        self.close()
        self.window.present()

    def put_back(self, pane):
        self.window = None
        self.body.append(pane)
        self.set_visible(True)
        pane._focus()


class TerminalWindow(Adw.ApplicationWindow):
    """The terminals out in a window of their own, Multiplex's to look at (its frame, its logo). back(pane): it's
    closed, the pane put back where it came from."""

    def __init__(self, app, pane, back=None):
        super().__init__(application=app, title="Multiplex", default_width=900, default_height=580)
        if sys.platform != "darwin":
            import multiplex
            if multiplex.themed_icon():
                self.set_icon_name(multiplex.APP_ID)
        self.pane, self.back = pane, back
        self.holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.holder.append(pane)
        widgets.inside(self.holder, under_bar=True)
        view = Adw.ToolbarView(content=self.holder)
        view.add_css_class("baude-frame")
        new = Gtk.Button(icon_name="tab-new-symbolic", tooltip_text=wording.say("browser.new_tab"))
        new.connect("clicked", lambda *_: self.pane.open())
        head = Adw.HeaderBar()
        head.pack_start(new)
        view.add_top_bar(head)
        self.set_content(view)
        self.connect("close-request", self._close)

    def _close(self, *_):
        if self.pane.get_parent() is self.holder:
            self.holder.remove(self.pane)
        if self.back is not None:
            self.back(self.pane)
        return False
