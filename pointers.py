"""The pointer's shape, all of it here (#92), for Baude and Record: what it is over a widget, and that you see it.

Over what a click follows, the hand (hand). Over an image a click opens large, the magnifying glass (magnifier). Anything
else (the I-beam, the crosshair, a rule's arrows) by its name (set_pointer). A Gtk.Label's links need nothing: GTK gives a
link the hand itself, by where the pointer is.

macOS: GDK tells macOS a new cursor only for the window it was last told the pointer came into, and any leave, a popover's
as well, clears that. From then on a widget's cursor changes and macOS still shows the old one, till the pointer leaves the
window and comes back. So every window, popover and dialog (start) has macOS shown, on each move of the pointer, what GTK
has worked out for what's under it, whichever widget it is and whoever set it."""
import sys
import weakref

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gtk  # noqa: E402

import macos  # noqa: E402  (no GTK: beside this file)

HAND, MAGNIFIER, TEXT = "pointer", "zoom-in", "text"
TOOLTIP_UP_MS = 150  # (after a tooltip's window is shown, it's macOS's arrow till this has passed: _tooltip_up)


def set_pointer(widget, name):
    """The pointer over widget, by GTK's name for it (None: as over its parent). Shown at once if it's there now."""
    now = widget.get_cursor()
    if (now.get_name() if now is not None else None) != name:
        widget.set_cursor_from_name(name)
        show(widget.get_native())
    return widget


def hand(widget):
    """A click on widget follows something, as a link does."""
    return set_pointer(widget, HAND)


def magnifier(widget):
    """A click on widget, an image, opens it large."""
    return set_pointer(widget, MAGNIFIER)


def show(native):
    """(macOS) What GTK says the pointer is on native (a window or a popover), if it's there, shown now."""
    watch = getattr(native, "pointer_watch", None)
    if not watch or not watch.contains_pointer() or (surface := native.get_surface()) is None:
        return
    cursor = surface.get_device_cursor(surface.get_display().get_default_seat().get_pointer())
    macos.show_cursor(cursor.get_name() if cursor is not None else "default")


def start():
    """As an app starts, once. macOS: the magnifying glass made, and each window, popover and dialog watched as it's
    realized (so none can be missed). Elsewhere, nothing: GTK's cursors are shown."""
    if sys.platform != "darwin" or getattr(start, "started", False):
        return
    start.started = True
    try:
        macos.make_zoom_cursor()
    except Exception as e:  # noqa: BLE001 (the arrow over an image isn't worth not starting over)
        print("no magnifying glass over images:", e, file=sys.stderr)
    GObject.add_emission_hook(Gtk.Widget, "realize", _realized)


def _realized(widget):
    # (a dialog as well as its window: a move under one, as under a grab, goes no further up than it)
    if isinstance(widget, (Gtk.Native, Adw.Dialog)) and getattr(widget, "pointer_watch", None) is None:
        if GObject.type_name(widget) == "GtkTooltipWindow":
            widget.connect("map", _tooltip_up)
            widget.pointer_watch = False  # (a tooltip takes no pointer: one connect is what's wanted here)
        else:
            # (the window's, so after the widget under the pointer's, which may change it, have seen the move)
            widget.pointer_watch = Gtk.EventControllerMotion()
            widget.pointer_watch.connect("motion", lambda c, _x, _y: show(c.get_widget().get_native()))
            widget.add_controller(widget.pointer_watch)
    return True  # (a hook stays only while it says so)


def _tooltip_up(tooltip):
    """A tooltip's window coming up makes macOS show the arrow, with no move of the pointer to put it right: it's set
    again, if the pointer's still where it was. (The tooltip is GTK's to show.)"""
    me = weakref.ref(tooltip)

    def again():
        if (t := me()) is not None and (over := t.get_parent()) is not None:
            show(over.get_native())
        return False
    GLib.timeout_add(TOOLTIP_UP_MS, again)
