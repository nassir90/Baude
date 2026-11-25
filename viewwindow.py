"""The Kubernetes and database views out in a window of their own and back, as ☰ → Terminal's terminals go
(terminalpane.TerminalView) and a Bot's browser (Emission's): Out is a view's Move to Its Own Window button and its
close (×), at its bar's end; ViewWindow the window it's out in. The same view moves, nothing read again: its cluster or
database, its watch, its logs and shells go on. That window closed (its ×, the view's ×), the view's back over the
window it came from, shown; the window it came from closed, it goes too."""
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Pango  # noqa: E402

from baudelib import wording  # noqa: E402


def host_label():
    """The bar's word for the host the view reads (as ☰ → Terminal's says the shown terminal's)."""
    label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=24)
    label.add_css_class("dim-label")
    return label


class Out:
    """A view's Move to Its Own Window button and × at the end of its bar. The view (view.win: its window) has close()
    and its find entry (search). out() moves it to a window of its own titled title; window: that window, or None."""

    def __init__(self, view, bar, title, search, zoom=None):
        self.view, self.title, self.search, self.zoom = view, title, search, zoom
        self.window = self.home = None
        self.gap = getattr(bar, "gap", None)
        self.button = Gtk.Button(icon_name="window-new-symbolic", tooltip_text=wording.say("common.move_out"))
        self.button.add_css_class("flat")
        self.button.connect("clicked", lambda *_: self.out())
        bar.append(self.button)
        close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text=wording.say("images.close"))
        close.add_css_class("circular")
        close.connect("clicked", lambda *_: self.window.close() if self.window is not None else self.view.close())
        bar.append(close)

    def here(self):
        """Whether it's over its window (not out), and open."""
        return self.window is None and self.view.get_visible()

    def out(self):
        view = self.view
        self.home = view.get_parent()
        self.home.remove_overlay(view)
        self.window = ViewWindow(view.win, view, self.title, self.search, self.back, self.zoom)
        self.button.set_visible(False)
        if self.gap is not None:
            self.gap.set_visible(False)
        view.set_visible(True)
        view.win.input.grab_focus()
        self.window.present()

    def back(self, closing_home=False):
        """Its window's closed: the view over the window it came from, shown (that window closing: closed too)."""
        view, self.window = self.view, None
        self.button.set_visible(True)
        if self.gap is not None:
            self.gap.set_visible(True)
        if closing_home:
            view.close()
        self.home.add_overlay(view)
        view.set_visible(not closing_home)
        if not closing_home:
            view.grab_focus()

    def present(self):
        """Out: its window brought up. False if it isn't out."""
        if self.window is None:
            return False
        self.window.present()
        return True


class ViewWindow(Adw.ApplicationWindow):
    """A view out in a window of its own: the view is all of it (its bar is the window's to drag, as it is over the
    window it came from). Ctrl+F (⌘F) finds in it; zoom(steps), if it has one, is Ctrl +/−/0's (⌘). back(closing_home):
    it's closed, the view to go back."""

    def __init__(self, main, view, title, search, back, zoom=None):
        super().__init__(application=main.get_application(), title=title, default_width=1200, default_height=780)
        self.view, self.back, self.main = view, back, main
        frame = Adw.ToolbarView(content=view)  # (a header bar of its own, as Multiplex's window has: the window's controls)
        frame.add_css_class("baude-frame")
        frame.add_top_bar(Adw.HeaderBar(show_title=False))
        self.set_content(frame)
        keys = Gtk.ShortcutController()
        keys.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<Meta>f|<Control>f"),
                                           Gtk.CallbackAction.new(lambda *_: (search.grab_focus(),
                                                                              search.select_region(0, -1), True)[2])))
        for accel, steps in (("<Meta>plus|<Control>plus|<Meta>equal|<Control>equal", 1), ("<Meta>minus|<Control>minus", -1),
                             ("<Meta>0|<Control>0", 0)):
            if zoom is not None:
                keys.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(accel), Gtk.CallbackAction.new(
                    lambda *_, n=steps: (zoom(n), True)[1])))
        self.add_controller(keys)
        self.connect("close-request", lambda *_: (self._closed(False), False)[1])  # (False: the window goes)
        self.gone_id = main.get_application().connect("window-removed",
                                                      lambda _a, w: w is main and self._closed(True) and self.destroy())

    def _closed(self, closing_home):
        if self.back is None:
            return True
        back, self.back = self.back, None
        self.get_application().disconnect(self.gone_id)
        self.get_content().set_content(None)
        self.set_content(None)
        back(closing_home)
        return True
