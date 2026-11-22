"""No focus ring (#100), checked the same in Baude's tests and Record's, with each app's stylesheets on the display:
every kind of control in a window of its own, light and dark, drawn with the focus and without; then real keys and a
real click; then a real window of the app's."""
import io

from gi.repository import Adw, Gtk
from PIL import Image, ImageChops

import mouse
import widgets


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from walk(child)
        child = child.get_next_sibling()


def named(widget, css_name):
    return next(w for w in walk(widget) if w.get_css_name() == css_name)


def drawn(widget, pump, wait=0.3):
    """What it draws, after what the theme fades has had its time (less is enough to see a ring begin)."""
    pump(wait)
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(widget).snapshot(snap, widget.get_width(), widget.get_height())
    tex = widget.get_native().get_renderer().render_texture(snap.to_node(), None)
    return Image.open(io.BytesIO(tex.save_to_png_bytes().get_data())).convert("RGB")


def changed(a, b):
    return ImageChops.difference(a, b).getbbox()


def focus(win, widget, name=""):
    assert widget.grab_focus(), f"{name or widget} takes the focus"
    win.set_focus_visible(True)


def controls():
    """(its name, what goes in the window, what takes the focus): the kinds the apps have, and those where the theme's
    ring is on a part of what has the focus (a tab's box, an expander's title, a scale's slider, a notebook's tab)."""
    def of(kind, name, **kw):
        w = kind(**kw)
        return name, w, w

    def in_list(row, *classes):
        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=list(classes))
        rows.append(row)
        return rows

    flat_toggle = Gtk.ToggleButton(icon_name="view-refresh-symbolic", active=True, css_classes=["flat"])
    sidebar_row = Gtk.ListBoxRow(child=Gtk.Label(label="A row", xalign=0))
    switch_row = Adw.SwitchRow(title="A switch")
    combo_row = Adw.ComboRow(title="A choice", model=Gtk.StringList.new(["One"]))
    tabs = Adw.TabView()
    tabs.append(Gtk.Label(label="In a tab")).set_title("A tab")
    tabs.append(Gtk.Label(label="In a tab")).set_title("Another")
    tab_bar = Adw.TabBar(view=tabs, autohide=False)
    drop = Gtk.DropDown.new_from_strings(["A dropdown", "Another"])
    pages = Adw.ViewStack()
    pages.add_titled(Gtk.Label(label="In a page"), "one", "One")
    pages.add_titled(Gtk.Label(label="In a page"), "two", "Two")
    switcher = Adw.ViewSwitcher(stack=pages, policy=Adw.ViewSwitcherPolicy.WIDE)
    flows = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE)
    flow_child = Gtk.FlowBoxChild(child=Gtk.Label(label="A flow child"))
    flows.append(flow_child)
    book = Gtk.Notebook()
    book.append_page(Gtk.Label(label="In a page"), Gtk.Label(label="A page"))
    cell = Gtk.SignalListItemFactory()
    cell.connect("setup", lambda _f, item: item.set_child(Gtk.Label(xalign=0)))
    cell.connect("bind", lambda _f, item: item.get_child().set_label(item.get_item().get_string()))
    table = Gtk.ColumnView(model=Gtk.NoSelection(model=Gtk.StringList.new(["A table's row"])))
    table.append_column(Gtk.ColumnViewColumn(title="A column", factory=cell, expand=True))
    scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 10, 1)
    return [
        of(Gtk.Button, "a button", label="A button"),
        of(widgets.RefreshButton, "a refresh button"),
        of(Gtk.ToggleButton, "a toggle", label="A toggle"),
        ("a flat toggle, on", flat_toggle, flat_toggle),
        of(Gtk.Switch, "a switch"),
        of(Gtk.CheckButton, "a check button", label="A check"),
        of(Gtk.LinkButton, "a link", uri="https://example.invalid", label="A link"),
        of(Gtk.Expander, "an expander", label="An expander"),
        ("a sidebar's row", in_list(sidebar_row, "navigation-sidebar"), sidebar_row),
        ("a switch row", in_list(switch_row, "boxed-list"), switch_row),
        ("a combo row", in_list(combo_row, "boxed-list"), combo_row),
        ("a tab", tab_bar, lambda: next(w for w in walk(tab_bar) if w.get_css_name() == "tab"
                                        and w.get_state_flags() & Gtk.StateFlags.SELECTED)),
        ("a dropdown", drop, lambda: named(drop, "button")),
        ("a view switcher", switcher, lambda: named(switcher, "button")),
        ("a flow box's child", flows, flow_child),
        ("a notebook", book, book),
        ("a table's row", table, lambda: named(named(table, "listview"), "row")),
        ("a scale", scale, scale),
    ]


def check(application, pump):
    manager = Adw.StyleManager.get_default()
    was = manager.get_color_scheme()
    win = Adw.ApplicationWindow(application=application)
    columns = Gtk.Box(spacing=16, margin_top=12, margin_bottom=12, margin_start=16, margin_end=16)
    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True)
    columns.append(column)
    kinds = controls()
    for i, (_name, shown, _takes) in enumerate(kinds):
        if i == len(kinds) // 2 + 1:
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True)
            columns.append(column)
        column.append(shown)
    entries = [Gtk.Entry(text="An entry"), Gtk.SearchEntry()]
    for entry in entries:
        column.append(entry)
    for row in (Adw.SpinRow.new_with_range(0, 10, 1), Adw.EntryRow(title="An entry row")):  # (Settings' sizes: their own ring)
        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        rows.append(row)
        column.append(rows)
        entries.append(row)
    menu_button = Gtk.MenuButton(label="A popover", halign=Gtk.Align.START)
    column.append(menu_button)
    win.set_content(columns)
    # (a click goes to the top window at that place, and a window already up, as Record's is, stays over this one)
    others = [w for w in application.get_windows() if w.get_visible()]
    for other in others:
        other.set_visible(False)
    win.present()
    pump(0.6)
    try:
        for scheme in (Adw.ColorScheme.FORCE_LIGHT, Adw.ColorScheme.FORCE_DARK):
            manager.set_color_scheme(scheme)
            pump(0.6)
            win.set_focus(None)
            win.set_focus_visible(True)
            plain = drawn(win, pump)
            for name, _shown, takes in kinds:
                focus(win, takes() if callable(takes) else takes, name)
                assert not (at := changed(drawn(win, pump, 0.15), plain)), f"{name}: a ring at {at} ({scheme})"
            for entry in entries:  # (its caret and selection change what's inside it: a ring is along its edge)
                focus(win, named(entry, "text") if isinstance(entry, Adw.PreferencesRow) else entry)
                now, (left, top, right, bottom) = drawn(win, pump, 0.15), around(win, entry)
                edge = (left + 14, top + 8, right - 14, top + 11)
                assert not changed(now.crop(edge), plain.crop(edge)), f"{entry}: a ring along its top ({scheme})"
            win.set_focus(None)
        in_a_popover(win, menu_button, pump)
        in_a_dialog(win, pump)
        if mouse.available():
            real_keys_and_a_click(win, kinds, pump)
    finally:
        manager.set_color_scheme(was)
        win.destroy()
        for other in others:
            other.set_visible(True)
        pump(0.1)


def in_a_popover(win, menu_button, pump):
    """Two buttons with the focus in turn, drawn alike: a ring would be round one, then the other."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    one, other = Gtk.Button(label="One"), Gtk.Button(label="Other")
    box.append(one)
    box.append(other)
    pop = Gtk.Popover(child=box)
    menu_button.set_popover(pop)
    pop.popup()
    pump(0.4)
    focus(win, one)
    first = drawn(pop, pump)
    focus(win, other)
    assert not (at := changed(drawn(pop, pump), first)), f"a ring in a popover, at {at}"
    pop.popdown()
    pump(0.3)


def in_a_dialog(win, pump):
    dialog = Adw.AlertDialog(heading="Sure?")
    dialog.add_response("no", "No")
    dialog.add_response("yes", "Yes")
    dialog.present(win)
    pump(0.6)
    one, other = [w for w in walk(dialog) if isinstance(w, Gtk.Button) and w.get_mapped()][:2]
    focus(win, one)
    first = drawn(win, pump)
    focus(win, other)
    assert not (at := changed(drawn(win, pump), first)), f"a ring in a dialog, at {at}"
    dialog.force_close()
    pump(0.4)


def around(win, widget):
    """Where it's drawn, with a ring round it if it has one."""
    ok, at = widget.compute_bounds(win)
    assert ok, widget
    return (int(at.get_x()) - 8, int(at.get_y()) - 8,
            int(at.get_x() + at.get_width()) + 8, int(at.get_y() + at.get_height()) + 8)


def real_keys_and_a_click(win, kinds, pump):
    """The keys turn focus-visible on (Tab, an arrow), and a click after them leaves it on: that's when a ring showed on
    what was clicked."""
    hand = mouse.Mouse(win)

    def same(widget, plain, what):
        place = around(win, widget)
        assert not (at := changed(drawn(win, pump).crop(place), plain.crop(place))), f"a ring {what}, at {at}"

    for x in (30, 2):  # (the pointer on nothing, so nothing's hovered: the first move can leave a tab as it was)
        hand.post(mouse.MOVED, x, x)
        hand.settle()
    button, refresh, toggle = kinds[0][1], kinds[1][1], kinds[2][1]
    focus(win, button)
    win.set_focus_visible(False)
    plain = drawn(win, pump)
    hand.key(*mouse.TAB)
    assert win.get_focus() is refresh and win.get_focus_visible(), (win.get_focus(), win.get_focus_visible())
    same(refresh, plain, "after a real Tab")
    ok, at = toggle.compute_bounds(win)
    x, y = at.get_x() + at.get_width() / 2, at.get_y() + at.get_height() / 2
    for _ in range(2):  # (on, then off again: its own colour when on isn't what's looked for)
        hand.down(x, y)
        hand.up(x, y)
        pump(0.1)
    hand.post(mouse.MOVED, 2, 2)
    assert win.get_focus() is toggle and win.get_focus_visible(), (win.get_focus(), win.get_focus_visible())
    assert not toggle.get_active()
    same(toggle, plain, "on what's clicked")
    rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["navigation-sidebar"])
    first, second = Gtk.ListBoxRow(child=Gtk.Label(label="A row")), Gtk.ListBoxRow(child=Gtk.Label(label="Another"))
    rows.append(first)
    rows.append(second)
    button.get_parent().append(rows)
    pump(0.2)
    focus(win, first)
    win.set_focus_visible(False)
    plain = drawn(win, pump)
    hand.key(*mouse.DOWN_ARROW)
    assert win.get_focus() is second and win.get_focus_visible(), (win.get_focus(), win.get_focus_visible())
    same(second, plain, "after a real arrow key")


def check_window(win, pump):
    """A real window: a real Tab from its first button, to what's next, has no ring round that."""
    if not mouse.available():
        return
    was = win.get_focus()
    win.present()
    pump(0.3)
    start = next(w for w in walk(win) if isinstance(w, Gtk.Button) and w.get_mapped() and w.get_focusable())
    try:
        start.grab_focus()
        win.set_focus_visible(False)
        plain = drawn(win, pump)
        mouse.Mouse(win).key(*mouse.TAB)
        now = win.get_focus()
        assert now is not start and isinstance(now, (Gtk.Button, Gtk.ListBoxRow)), now
        assert win.get_focus_visible()
        place = around(win, now)
        assert not (at := changed(drawn(win, pump).crop(place), plain.crop(place))), f"a ring round {now}, at {at}"
    finally:
        win.set_focus_visible(False)
        if was is not None:
            was.grab_focus()
