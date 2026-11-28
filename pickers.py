"""pickers — what's picked from: a code view's line numbers (LineGutter), dropdowns (ChoiceDropDown,
press_opens_dropdowns), the model, host and kubeconfig context pickers, tree lists (TreeList, TreeListView), a sidebar's action, a
label that fades where it's cut (FadeLabel), and ⌘K's palette (RefPalette).
"""
import math
import os
import re
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Graphene, Gsk, Gtk, Pango  # noqa: E402,F401

from baudelib import icons  # noqa: E402  (schema/icons: every app's icons, the Android app's too)
from baudelib import models  # noqa: E402
from baudelib import providers  # noqa: E402  (every app's model names)
from baudelib import wording  # noqa: E402
from widgets import RefIcon, WorkingDots, find_label, insert_actions, time_tip, walk  # noqa: E402


class LineGutter(Gtk.Widget):
    """Line numbers beside a TextView (its left gutter), the marked line's in bold."""

    def __init__(self, view):
        super().__init__()
        self.view, self.marked = view, None
        self.drawn = []  # (line, y) of the numbers drawn last (tests hold them to their lines)
        self.add_css_class("code-gutter")

    def do_measure(self, orientation, _for_size):
        if orientation == Gtk.Orientation.VERTICAL:
            return 0, 0, -1, -1
        digits = len(str(max(1, self.view.get_buffer().get_line_count())))
        w = self.create_pango_layout("0" * (digits + 2)).get_pixel_size()[0]
        return w, w, -1, -1

    def do_snapshot(self, snap):
        view, width = self.view, self.get_width()
        rect = view.get_visible_rect()
        it, _top = view.get_line_at_y(rect.y)
        color = self.get_color()
        dim = Gdk.RGBA()
        dim.red, dim.green, dim.blue, dim.alpha = color.red, color.green, color.blue, color.alpha * 0.45
        layout = self.create_pango_layout("")
        self.drawn = []
        while True:
            y, h = view.get_line_yrange(it)
            if y > rect.y + rect.height:
                break
            n = it.get_line() + 1
            _x, wy = view.buffer_to_window_coords(Gtk.TextWindowType.LEFT, 0, y)
            layout.set_markup(f"<b>{n}</b>" if n == self.marked else str(n), -1)
            w, lh = layout.get_pixel_size()
            first = view.get_iter_location(it).height or h  # (a wrapped line: its first display line's height)
            self.drawn.append((n, wy))
            snap.save()
            snap.translate(Graphene.Point().init(width - w - 8, wy + max(0, (min(h, first) - lh) / 2)))
            snap.append_layout(layout, color if n == self.marked else dim)
            snap.restore()
            if not it.forward_line():
                break


class CheckList(Gtk.Box):
    """A check for each of a list of things, and one over them all, as the Kubernetes page's Namespaces and the
    sidebar's ⋮ → Groups have them: those checked are shown, in the order checked. Every one shown is every one checked
    (None), the one over them checked too; checking that one checks every one, unchecking it unchecks every one ([]).
    changed(the keys shown, None for every one) as they're picked; menu(key, check, x, y), x and y in the check's, on a
    right-click on one. heading: the one over them all reads as a section heading (the popover's "Group by"), its
    check kept."""

    def __init__(self, all_label, changed, menu=None, max_height=420, heading=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.changed, self.menu, self.items, self.shown, self.checks, self.syncing = changed, menu, None, None, {}, False
        self.all = Gtk.CheckButton(label=all_label)
        self.all.connect("toggled", lambda b: self.syncing or self.pick(None if b.get_active() else []))
        if heading and isinstance(label := self.all.get_last_child(), Gtk.Label):
            label.add_css_class("heading")
            label.add_css_class("dim-label")
        self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.append(self.all)
        self.append(Gtk.Separator())
        self.append(Gtk.ScrolledWindow(child=self.list, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                       propagate_natural_height=True, propagate_natural_width=True,
                                       max_content_height=max_height))

    def fill(self, items, shown, dimmed=()):
        """items: [(key, label)], in order; shown: the keys checked (None: every one); dimmed: the keys whose labels are
        greyed (a column filter's values no row has), still there to check. Built again only when the items change."""
        if items != self.items:
            child = self.list.get_first_child()
            while child is not None:
                self.list.remove(child)
                child = self.list.get_first_child()
            self.checks, self.items = {}, list(items)
            for key, label in items:
                check = self.checks[key] = Gtk.CheckButton(label=label)
                check.key = key
                check.connect("toggled", lambda b: self.syncing or self.pick(
                    [k for k in self.keys() if k != b.key] + [b.key] * b.get_active()))
                if self.menu is not None:
                    right = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
                    right.connect("pressed", lambda _g, _n, x, y, c=check: self.menu(c.key, c, x, y))
                    check.add_controller(right)
                self.list.append(check)
        for key, check in self.checks.items():
            if isinstance(label := check.get_last_child(), Gtk.Label):
                (label.add_css_class if key in dimmed else label.remove_css_class)("dim-label")
        self.sync(shown)

    def keys(self):
        """The keys checked: every one's, in order, while every one is."""
        return [key for key, _label in self.items or ()] if self.shown is None else self.shown

    def sync(self, shown):
        """The checks as shown says (the keys checked, None for every one), changed not told."""
        was, self.syncing = self.syncing, True
        self.shown = None if shown is None else list(shown)
        self.all.set_active(self.shown is None)
        for key, check in self.checks.items():
            check.set_active(self.shown is None or key in self.shown)
        self.syncing = was

    def pick(self, keys):
        """Checked or unchecked by hand: these keys shown (None: every one), and changed told."""
        if keys is not None:
            keys = list(dict.fromkeys(keys))
            if keys and set(keys) >= {key for key, _label in self.items or ()}:
                keys = None
        self.sync(keys)
        self.changed(keys)


class ChoiceDropDown(Gtk.DropDown):
    """A DropDown over (value, label) pairs. on_pick(value) fires for user picks only,
    not for select()/set_choices() calls that mirror what the CLI reports."""

    def __init__(self, choices, tooltip, on_pick):
        super().__init__(tooltip_text=tooltip, css_classes=["choice"])
        # (its list centred under it: GTK's lines up their left sides, and a longer one runs on to the right)
        if (popup := next((w for w in walk(self) if isinstance(w, Gtk.Popover)), None)) is not None:
            popup.set_halign(Gtk.Align.CENTER)
        self.strings = Gtk.StringList()
        self.set_model(self.strings)
        self.on_pick, self.values, self.quiet = on_pick, [], False
        self.set_choices(choices)
        self.connect("notify::selected", self._changed)

    def _changed(self, *_):
        if not self.quiet and self.value() is not None:
            self.repicked = True  # (this row's activation told already: not again by _activated)
            GLib.idle_add(lambda: setattr(self, "repicked", False))
            self.on_pick(self.value())

    def pick_again(self):
        """on_pick fired too as the row already picked is picked again in its list (☰'s carets: it opened there)."""
        listed = next((w for w in walk(self) if isinstance(w, Gtk.ListView)), None)
        if listed is not None:
            self.repicked = False
            listed.connect("activate", self._activated)

    def _activated(self, _list, at):
        if not getattr(self, "repicked", False) and at == self.get_selected() and self.value() is not None:
            self.on_pick(self.value())

    def _quietly(self, fn):
        self.quiet = True
        try:
            fn()
        finally:
            self.quiet = False

    def set_choices(self, choices):
        """Replace the entries, keeping the current value selected if it is still there."""
        keep = self.value()
        def swap():
            self.values = [v for v, _ in choices]
            self.strings.splice(0, self.strings.get_n_items(), [lbl for _, lbl in choices])
            self.set_selected(self.values.index(keep) if keep in self.values else Gtk.INVALID_LIST_POSITION)
        self._quietly(swap)

    def set_dimmed(self, values):
        """Those values greyed in its list (a host out of reach, say), still there to pick. Its list's rows are its
        own from the first call: each a label, and the tick on the one picked, as GTK's own are."""
        self.dimmed = set(values)
        if getattr(self, "rows", None) is None:
            self.rows = set()
            factory = Gtk.SignalListItemFactory()
            factory.connect("setup", self._row_setup)
            factory.connect("bind", self._row_bind)
            factory.connect("unbind", lambda _f, item: self.rows.discard(item))
            self.set_list_factory(factory)
            self.connect("notify::selected", lambda *_: [self._row_bind(None, item) for item in list(self.rows)])
        for item in list(self.rows):
            self._row_bind(None, item)

    def _row_setup(self, _factory, item):
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(xalign=0, hexpand=True))
        row.append(Gtk.Image(icon_name="object-select-symbolic"))
        item.set_child(row)

    def _row_bind(self, _factory, item):
        row, at = item.get_child(), item.get_position()
        if row is None or item.get_item() is None:
            return
        self.rows.add(item)
        label, tick = row.get_first_child(), row.get_last_child()
        label.set_text(item.get_item().get_string())
        value = self.values[at] if at < len(self.values) else None
        (label.add_css_class if value in self.dimmed else label.remove_css_class)("dim-label")
        tick.set_opacity(1 if at == self.get_selected() else 0)

    def select(self, value):
        """Show `value` as selected (appending it if unknown) without firing on_pick."""
        def pick():
            if value not in self.values:
                self.values.append(value)
                self.strings.append(value)
            self.set_selected(self.values.index(value))
        self._quietly(pick)

    def value(self):
        i = self.get_selected()
        return self.values[i] if i < len(self.values) else None

    def label(self):
        i = self.get_selected()
        return self.strings.get_string(i) if i < len(self.values) else ""


# Each model's family, by the program that runs it (Antigravity's: whatever its models are called), else by what its
# value or name says (the first that matches): the monotone icon its row in a model picker shows (schema/icons/models)
# and the heading it's listed under. The phone reads the same file.
MODEL_FAMILIES = icons.model_families()
MODEL_PROGRAMS = icons.model_programs()
MODEL_ICONS = icons.folder("models")


def model_family(value, label=""):
    """(key, name) of the family value (labelled label) is of, or (None, "Other"): its program's (MODEL_PROGRAMS), else
    by its name, else by its value's last part (not what serves it otherwise: ollama/…'s "llama",
    openai/<endpoint>/…'s "openai")."""
    if (program := MODEL_PROGRAMS.get(models.model_program(value))) is not None:
        return program[0], wording.say(program[1])
    for said in ((label or "").lower().split(), [(value or "").lower().rsplit("/", 1)[-1]]):
        for key, name, pattern in MODEL_FAMILIES:
            if any(re.search(pattern, s) for s in said):
                return key, name
    return None, wording.say("common.other")


def model_icon(value, label=""):
    """The themed icon name of value's family (recoloured as the text is: a -symbolic icon), or None."""
    key = model_family(value, label)[0]
    display = Gdk.Display.get_default()
    if key is None or display is None:
        return None
    theme = Gtk.IconTheme.get_for_display(display)
    if MODEL_ICONS not in theme.get_search_path():
        theme.add_search_path(MODEL_ICONS)
    return f"baude-model-{key}-symbolic"


class ModelPicker(Gtk.MenuButton):
    """A model picker as a command palette: a search box over the models, each with its family's icon, listed under
    its family's name in the order first given. Typing narrows them (every word, in the name, the value or the
    family's), ↑ ↓ move, Enter picks, Esc closes. ChoiceDropDown's interface: on_pick(value) for the user's picks only,
    not set_choices() or select()."""

    def __init__(self, choices, tooltip, on_pick, chars=30):
        super().__init__(tooltip_text=tooltip, always_show_arrow=True, css_classes=["choice", "model-picker"])
        self.on_pick, self.values, self.labels, self.quiet, self.picked = on_pick, [], {}, False, None
        self.slugs = {}
        face = Gtk.Box(spacing=6)
        self.face_icon = Gtk.Image(pixel_size=16, visible=False)
        self.shown = Gtk.Label(ellipsize=Pango.EllipsizeMode.END, max_width_chars=chars)
        face.append(self.face_icon)
        face.append(self.shown)
        self.set_child(face)
        pop = Gtk.Popover(css_classes=["model-palette"])
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.search = Gtk.SearchEntry(placeholder_text=wording.say("model_picker.search"))
        self.search.connect("search-changed", lambda *_: self._filter())
        self.search.connect("activate", lambda *_: self._activate(self.list.get_selected_row()))
        self.search.connect("stop-search", lambda *_: self.popdown())
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.search.add_controller(keys)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE, css_classes=["model-list"])
        self.list.set_filter_func(self._shown)
        self.list.set_header_func(self._header)
        self.list.connect("row-activated", lambda _l, row: self._activate(row))
        self.none = Gtk.Label(label=wording.say("model_picker.none"), css_classes=["dim-label"], margin_top=12,
                              margin_bottom=12)
        self.list.set_placeholder(self.none)
        self.scroll = Gtk.ScrolledWindow(child=self.list, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                         propagate_natural_height=True, max_content_height=420, min_content_width=280)
        box.append(self.search)
        box.append(self.scroll)
        pop.set_child(box)
        pop.connect("show", lambda *_: self._opened())
        self.set_popover(pop)
        self.set_choices(choices)

    def set_choices(self, choices, slugs=None):
        """Replace the entries ((value, label)), keeping the picked one picked if it's still there. slugs: each
        value's slug (providers.model_slug), shown on hover over its name only."""
        keep = self.value()
        self.values, self.labels = [v for v, _ in choices], dict(choices)
        self.slugs = dict(slugs or {})
        order = {}  # (each family's place: where its first model was)
        for v, lbl in choices:
            order.setdefault(model_family(v, lbl)[1], len(order))
        rows = sorted(enumerate(choices), key=lambda c: (order[model_family(*c[1])[1]], c[0]))
        while (row := self.list.get_row_at_index(0)) is not None:
            self.list.remove(row)
        for _i, (value, lbl) in rows:
            row = Gtk.ListBoxRow(css_classes=["model-row"])
            row.value, row.family = value, model_family(value, lbl)[1]
            row.said = f"{value} {lbl} {row.family}".lower()
            line = Gtk.Box(spacing=8)
            icon = model_icon(value, lbl)
            line.append(Gtk.Image(icon_name=icon, pixel_size=16) if icon else Gtk.Box(width_request=16))
            line.append(Gtk.Label(label=lbl, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
                                  max_width_chars=40, tooltip_text=self.tip(value, lbl)))
            row.check = Gtk.Image(icon_name="object-select-symbolic", pixel_size=16, opacity=0)
            line.append(row.check)
            row.set_child(line)
            self.list.append(row)
        self.select(keep if keep in self.labels else None)

    def select(self, value):
        """Show value as picked (appended if unknown; None: nothing), without on_pick."""
        if value is not None and value not in self.labels:
            self.set_choices([*self.labels.items(), (value, providers.model_name(value))], self.slugs)
        self.picked = value
        self.shown.set_label(self.labels.get(value, ""))
        self.shown.set_tooltip_text(self.tip(value, self.labels.get(value, "")) if value is not None else None)
        icon = model_icon(value, self.labels.get(value, "")) if value is not None else None
        self.face_icon.set_from_icon_name(icon)
        self.face_icon.set_visible(icon is not None)
        for row in self._rows():
            row.check.set_opacity(1 if row.value == value else 0)

    def value(self):
        return self.picked

    def label(self):
        return self.labels.get(self.picked, "")

    def tip(self, value, lbl):
        """What hovering a model's name shows: its slug (the name in full where it's cut and its slug is its name)."""
        slug = self.slugs.get(value, value)
        return slug if slug != lbl else lbl if len(lbl) > 40 else None

    def _rows(self):
        i = 0
        while (row := self.list.get_row_at_index(i)) is not None:
            yield row
            i += 1

    def _shown(self, row):
        return all(w in row.said for w in self.search.get_text().lower().split())

    def _header(self, row, before):
        if before is None or before.family != row.family:  # (its family's name over its first model; filtered: shown's)
            row.set_header(Gtk.Label(label=row.family, xalign=0, css_classes=["model-family", "dim-label"]))
        else:
            row.set_header(None)

    def _filter(self):
        self.list.invalidate_filter()
        self.list.invalidate_headers()
        first = next((r for r in self._rows() if r.get_child_visible() and self._shown(r)), None)
        self.list.select_row(first)

    def _opened(self):
        self.search.set_text("")
        self._filter()
        now = next((r for r in self._rows() if r.value == self.picked), None)
        if now is not None:  # (the picked one lit, Enter's; typing narrows from the first)
            self.list.select_row(now)
        self.search.grab_focus()

    def _key(self, _c, keyval, _code, _state):
        if keyval not in (Gdk.KEY_Down, Gdk.KEY_Up):
            return False
        shown = [r for r in self._rows() if self._shown(r)]
        if not shown:
            return True
        now = self.list.get_selected_row()
        i = shown.index(now) if now in shown else -1
        row = shown[max(0, min(len(shown) - 1, i + (1 if keyval == Gdk.KEY_Down else -1)))]
        self.list.select_row(row)
        adj, (ok, rect) = self.scroll.get_vadjustment(), row.compute_bounds(self.list)
        if ok:  # (kept in view)
            y, h = rect.get_y(), rect.get_height()
            if y < adj.get_value():
                adj.set_value(y)
            elif y + h > adj.get_value() + adj.get_page_size():
                adj.set_value(y + h - adj.get_page_size())
        return True

    def _activate(self, row):
        if row is None or not self._shown(row):
            return
        self.popdown()
        if row.value != self.picked:
            self.select(row.value)
            if not self.quiet:
                self.on_pick(row.value)


CONTEXTS_SEARCHED = 15  # a context picker with more than this many has a search, and its contexts grouped by prefix


def context_order(name):
    """A context's place in a picker: by name, any case, numbers by their value (slice-2 before slice-10)."""
    return [(0, int(t), "") if t.isdigit() else (1, 0, t.lower()) for t in re.split(r"(\d+)", name) if t]


def context_groups(names):
    """Each context's group, by the start of its name up to its first - _ . : / or @, where two or more share it
    ("slice" for slice-a and slice-b); "" for one alone."""
    starts = {n: re.split(r"[-_.:/@]", n, 1)[0] if re.search(r"[-_.:/@]", n) else "" for n in names}
    count = {}
    for start in starts.values():
        count[start] = count.get(start, 0) + 1
    return {n: start if start and count[start] > 1 else "" for n, start in starts.items()}


class ContextPicker(Gtk.MenuButton):
    """A kubeconfig context picker: its face the context picked (or "Choose a context", dim, when none is); a list of
    the contexts by name, the picked one ticked. Past CONTEXTS_SEARCHED of them, a search over them (every word typed
    in the name) and their groups' names over them (context_groups). set_contexts() puts in and takes out only the rows
    that changed, so a list open as tools add and delete clusters doesn't flicker or lose its place. on_pick(name)
    for the user's picks only."""

    def __init__(self, tooltip, on_pick, chars=32):
        super().__init__(tooltip_text=tooltip, always_show_arrow=True, css_classes=["choice", "model-picker"])
        self.on_pick, self.picked, self.rows, self.groups = on_pick, None, {}, {}
        self.shown = Gtk.Label(ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=chars)
        self.set_child(self.shown)
        pop = Gtk.Popover(css_classes=["model-palette"])
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.search = Gtk.SearchEntry(placeholder_text=wording.say("kube.find_context"), visible=False)
        self.search.connect("search-changed", lambda *_: self._filter())
        self.search.connect("activate", lambda *_: self._activate(self.list.get_selected_row()))
        self.search.connect("stop-search", lambda *_: self.popdown())
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.search.add_controller(keys)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE, css_classes=["model-list"])
        self.list.set_sort_func(lambda a, b: (context_order(a.value) > context_order(b.value)) -
                                (context_order(a.value) < context_order(b.value)))
        self.list.set_filter_func(self._shown)
        self.list.set_header_func(self._header)
        self.list.connect("row-activated", lambda _l, row: self._activate(row))
        self.list.set_placeholder(Gtk.Label(label=wording.say("kube.no_context_match"), css_classes=["dim-label"],
                                            margin_top=12, margin_bottom=12))
        self.scroll = Gtk.ScrolledWindow(child=self.list, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                         propagate_natural_height=True, propagate_natural_width=True,
                                         max_content_height=420, min_content_width=200)
        box.append(self.search)
        box.append(self.scroll)
        pop.set_child(box)
        pop.connect("show", lambda *_: self._opened())
        self.set_popover(pop)
        self.select(None)

    def set_contexts(self, names):
        """The contexts listed now: rows for new ones put in, those gone taken out, the rest left as they are."""
        names = list(dict.fromkeys(names))
        for name in [n for n in self.rows if n not in names]:
            self.list.remove(self.rows.pop(name))
        for name in names:
            if name in self.rows:
                continue
            row = Gtk.ListBoxRow(css_classes=["model-row"])
            row.value = name
            line = Gtk.Box(spacing=8)
            line.append(Gtk.Label(label=name, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE,
                                  max_width_chars=44, tooltip_text=name if len(name) > 44 else None))
            row.check = Gtk.Image(icon_name="object-select-symbolic", pixel_size=16,
                                  opacity=1 if name == self.picked else 0)
            line.append(row.check)
            row.set_child(line)
            self.rows[name] = row
            self.list.append(row)
        many = len(names) > CONTEXTS_SEARCHED
        self.groups = context_groups(names) if many else {}
        self.search.set_visible(many)
        if not many and self.search.get_text():
            self.search.set_text("")
        self.list.invalidate_headers()
        self.list.invalidate_filter()

    def select(self, name):
        """Show name as picked (None: none is, the face asks for one), without on_pick."""
        self.picked = name
        self.shown.set_label(name or wording.say("kube.pick_context"))
        (self.shown.remove_css_class if name else self.shown.add_css_class)("dim-label")
        self.shown.set_tooltip_text(name if name and len(name) > 32 else None)
        for value, row in self.rows.items():
            row.check.set_opacity(1 if value == name else 0)

    def value(self):
        return self.picked

    def names(self):
        """The contexts listed, in the list's order."""
        return sorted(self.rows, key=context_order)

    def _shown(self, row):
        return all(w in row.value.lower() for w in self.search.get_text().lower().split())

    def _header(self, row, before):
        group = self.groups.get(row.value, "")
        if group and (before is None or self.groups.get(before.value, "") != group):
            row.set_header(Gtk.Label(label=group, xalign=0, css_classes=["model-family", "dim-label"]))
        elif not group and before is not None and self.groups.get(before.value, ""):
            row.set_header(Gtk.Separator(margin_top=4, margin_bottom=4))  # (past a group: one on its own)
        else:
            row.set_header(None)

    def _filter(self):
        self.list.invalidate_filter()
        self.list.invalidate_headers()
        first = next((r for r in self._rows() if self._shown(r)), None)
        self.list.select_row(first)

    def _rows(self):
        return [self.rows[n] for n in self.names()]

    def _opened(self):
        self.search.set_text("")
        self._filter()
        now = self.rows.get(self.picked)
        if now is not None:
            self.list.select_row(now)
            GLib.idle_add(lambda: (now.get_parent() is not None and now.grab_focus(), False)[1])
        if self.search.get_visible():
            self.search.grab_focus()

    def _key(self, _c, keyval, _code, _state):
        if keyval not in (Gdk.KEY_Down, Gdk.KEY_Up):
            return False
        shown = [r for r in self._rows() if self._shown(r)]
        if not shown:
            return True
        now = self.list.get_selected_row()
        i = shown.index(now) if now in shown else -1
        row = shown[max(0, min(len(shown) - 1, i + (1 if keyval == Gdk.KEY_Down else -1)))]
        self.list.select_row(row)
        adj, (ok, rect) = self.scroll.get_vadjustment(), row.compute_bounds(self.list)
        if ok:  # (kept in view)
            y, h = rect.get_y(), rect.get_height()
            if y < adj.get_value():
                adj.set_value(y)
            elif y + h > adj.get_value() + adj.get_page_size():
                adj.set_value(y + h - adj.get_page_size())
        return True

    def _activate(self, row):
        if row is None or not self._shown(row):
            return
        self.popdown()
        if row.value != self.picked:
            self.select(row.value)
            self.on_pick(row.value)


def ellipsize_dropdown(dd, chars=30):
    """dd's button shows its choice shortened with … (a long server or database name would widen the bar past the
    window); its list shows every choice whole."""
    def factory(short):
        f = Gtk.SignalListItemFactory()
        f.connect("setup", lambda _f, item: item.set_child(Gtk.Label(
            xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE if short else Pango.EllipsizeMode.NONE,
            max_width_chars=chars if short else -1)))
        f.connect("bind", lambda _f, item: (item.get_child().set_label(item.get_item().get_string()),
                                            item.get_child().set_tooltip_text(item.get_item().get_string())))
        return f
    dd.set_factory(factory(True))
    dd.set_list_factory(factory(False))
    return dd


class HostPicker(Gtk.MenuButton):
    """A picker of choices by host, as ⋮'s menu lists its sections: each host's name a header, its choices under it,
    a rule between hosts. Its button shows the choice picked after its host's name (local / MLX), shortened with … past
    chars. on_pick(key) for the user's picks only, not for select() or set_choices()."""

    def __init__(self, tooltip, on_pick, chars=30):
        super().__init__(tooltip_text=tooltip, always_show_arrow=True, css_classes=["choice"])
        self.on_pick, self.labels, self.quiet = on_pick, {}, False
        self.shown = Gtk.Label(ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=chars)
        self.set_child(self.shown)
        self.action = Gio.SimpleAction.new_stateful("pick", GLib.VariantType.new("s"), GLib.Variant.new_string(""))
        self.action.connect("change-state", self._changed)
        group = Gio.SimpleActionGroup()
        group.add_action(self.action)
        insert_actions(self, "hostpicker", group)

    def _changed(self, action, value):
        action.set_state(value)
        self.shown.set_label(self.labels.get(value.get_string(), ""))
        if not self.quiet and value.get_string() in self.labels:
            self.on_pick(value.get_string())

    def set_choices(self, hosts):
        """hosts: [(host name, [(key, label)])], in order; a host named None: its choices with no heading, each
        shown as its label alone. The key picked stays picked if it's still there."""
        menu, self.labels = Gio.Menu(), {}
        for host, choices in hosts:
            section = Gio.Menu()
            for key, label in choices:
                item = Gio.MenuItem.new(label, None)
                item.set_action_and_target_value("hostpicker.pick", GLib.Variant.new_string(key))
                section.append_item(item)
                self.labels[key] = label if host is None else f"{host} / {label}"
            menu.append_section(host, section)
        self.set_menu_model(menu)
        self.select(self.value())

    def select(self, key):
        """Show key as picked (None, or one not listed: nothing), without on_pick."""
        self.quiet = True
        try:
            self.action.change_state(GLib.Variant.new_string(key if key in self.labels else ""))
        finally:
            self.quiet = False

    def value(self):
        key = self.action.get_state().get_string()
        return key if key in self.labels else None

    def label(self):
        return self.shown.get_label()


def press_opens_dropdowns():
    """As an app starts, once: every dropdown (a Gtk.DropDown, an Adw.ComboRow, a HostPicker) opens its list as the
    mouse button goes down on it, not as it comes up (BaudeInternal#74), as a right-click's menu opens as the button
    goes down on what's clicked. Whatever a press opens, held down and let go over a choice (a menu's item, a list's
    row, a popover's button) a quarter second or more after the press: that one's picked, and it closes
    (BaudeInternal#273); sooner, it's a click, as a native menu takes it, and picks nothing. Let go on what was
    pressed, or between its choices: it stays open, to pick from with a click. Let go outside it: it closes. Each
    taken as it's realized (the hooks on GtkWidget's realize and map), so none is missed."""
    if getattr(press_opens_dropdowns, "started", False):
        return
    press_opens_dropdowns.started = True
    GObject.add_emission_hook(Gtk.Widget, "realize", _dropdown_realized)
    GObject.add_emission_hook(Gtk.Widget, "map", _held_mapped)


def _dropdown_realized(widget):
    if isinstance(widget, (Gtk.DropDown, Adw.ComboRow, HostPicker)) and not getattr(widget, "press_opens", None):
        # (before the dropdown's own button: that one opens it on the release)
        widget.press_opens = Gtk.GestureClick(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        widget.press_opens.connect("pressed", _dropdown_pressed)
        widget.add_controller(widget.press_opens)
    if isinstance(widget, Gtk.Native) and not getattr(widget, "held_watch", None):
        # (each window's and popover's presses, moves and releases, whichever widget in it they're for)
        widget.held_watch = Gtk.EventControllerLegacy(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        widget.held_watch.connect("event", _held_event)
        widget.add_controller(widget.held_watch)
    return True  # (a hook stays only while it says so)


def _dropdown_list(widget):
    """The popover widget's choices are in."""
    if isinstance(widget, Gtk.MenuButton):
        return widget.get_popover()
    return next((w for w in walk(widget) if isinstance(w, Gtk.Popover)), None)


def _dropdown_pressed(gesture, n_press, _x, _y):
    widget = gesture.get_widget()
    pop = _dropdown_list(widget)
    if n_press != 1 or pop is None or pop.get_visible():
        return
    device = gesture.get_current_event_device()
    if device is not None and device.get_source() == Gdk.InputSource.TOUCHSCREEN:
        return  # (a finger's left to GTK, opening on the lift: a drag from here is a scroll, not a pick)
    gesture.set_state(Gtk.EventSequenceState.CLAIMED)  # (its own click, on the release, isn't to toggle it again)
    # (opened as it opens itself; a Gtk.DropDown's activate() is Space's, which waits a quarter second to)
    if isinstance(widget, Gtk.MenuButton):
        widget.popup()
    elif isinstance(button := widget.get_first_child(), Gtk.ToggleButton) and isinstance(widget, Gtk.DropDown):
        button.set_active(True)
    else:
        widget.activate()


# (the press under way: what it's on and when; the popovers it opened, while it's held: _held_event, _held_mapped)
HELD = {"on": None, "when": 0, "pops": []}
HELD_CLICK_MS = 250  # (let go sooner than this after the press: a click, as a native menu takes it, picking nothing)


def _held_event(ctl, _event):
    """A window's or a popover's event (EventControllerLegacy's, the event got from it: PyGObject gives none)."""
    event = ctl.get_current_event()
    kind = event.get_event_type() if event is not None else None
    if kind == Gdk.EventType.BUTTON_PRESS:
        device = event.get_device()
        touch = device is not None and device.get_source() == Gdk.InputSource.TOUCHSCREEN
        ok, x, y = event.get_position()
        native = ctl.get_widget()
        tx, ty = native.get_surface_transform()
        HELD.update(on=None if touch or not ok else native.pick(x - tx, y - ty, Gtk.PickFlags.DEFAULT),
                    when=event.get_time(), pops=[])
    elif kind == Gdk.EventType.MOTION_NOTIFY and (pop := _held_pop()) is not None:
        choice = _held_choice(pop, *_held_point(pop, _window_point(event)))
        _dropdown_light(pop, None if choice is pop else choice)
    elif kind == Gdk.EventType.BUTTON_RELEASE:
        pop, on, when, now = _held_pop(), HELD["on"], HELD["when"], _window_point(event)
        HELD.update(on=None, pops=[])
        if pop is not None and now is not None:
            _dropdown_light(pop, None)
            if (pressed := _window_bounds(on)) is not None and pressed.contains_point(Graphene.Point().init(*now)):
                return False  # (on what was pressed: it stays open)
            choice = _held_choice(pop, *_held_point(pop, now))
            if choice is None:
                pop.popdown()  # (let go outside it)
            elif choice is not pop and event.get_time() - when >= HELD_CLICK_MS:  # (sooner: a click that slipped)
                # (after GTK's own handling of it: what it's let go on hears it first, as it would)
                GLib.idle_add(lambda: _held_pick(pop, choice) and False)
    return False


def _held_mapped(widget):
    """A popover shown while a press is held, by what's pressed (its parent the pressed widget, or holding it): the
    release picks in it (_held_event)."""
    on = HELD["on"]
    if isinstance(widget, Gtk.Popover) and on is not None and (parent := widget.get_parent()) is not None \
            and (on is parent or on.is_ancestor(parent)):
        HELD["pops"].append(widget)
        if not getattr(widget, "unlit_closed", False):  # (closed while held, Escape: nothing left lit in it)
            widget.unlit_closed = True
            widget.connect("closed", lambda p: _dropdown_light(p, None))
    return True


def _held_pop():
    """The last popover the press under way opened that's still open."""
    return next((p for p in reversed(HELD["pops"]) if p.get_mapped()), None)


def _surface_origin(native):
    """Where native's surface is in its window's (a popover's: placed in the surface of what it's on, and so on)."""
    x = y = 0
    while isinstance(native, Gtk.Popover) and native.get_parent() is not None:
        surface = native.get_surface()
        x, y = x + surface.get_position_x(), y + surface.get_position_y()
        native = native.get_parent().get_native()
    return x, y


def _window_point(event):
    """event's point in its window's surface, whichever surface it's of; None if it has none."""
    ok, x, y = event.get_position()
    native = Gtk.Native.get_for_surface(event.get_surface())
    if not ok or native is None:
        return None
    nx, ny = _surface_origin(native)
    return x + nx, y + ny


def _held_point(pop, at):
    """at (a point of its window's surface: _window_point's) as a point of pop's."""
    if at is None:
        return None, None
    px, py = _surface_origin(pop)
    tx, ty = pop.get_surface_transform()
    return at[0] - px - tx, at[1] - py - ty


def _window_bounds(widget):
    """widget's bounds in its window's surface (through the popovers it's in), or None."""
    native = widget.get_native() if widget is not None else None
    ok, b = widget.compute_bounds(native) if native is not None else (False, None)
    if not ok:
        return None
    nx, ny = _surface_origin(native)
    tx, ty = native.get_surface_transform()
    return Graphene.Rect().init(b.get_x() + nx + tx, b.get_y() + ny + ty, b.get_width(), b.get_height())


def _held_choice(pop, x, y):
    """What's at (x, y) of pop: a choice (a row of a Gtk.DropDown's or an Adw.ComboRow's list, a menu's item, a
    button of a popover's own); pop itself if it's in pop but on no choice (or a greyed one); None if it's outside."""
    if x is None:
        return None
    under = pop.pick(x, y, Gtk.PickFlags.DEFAULT)
    while under is not pop and under is not None:
        if under.__gtype__.name in ("GtkListItemWidget", "GtkModelButton") or \
                isinstance(under, (Gtk.Button, Gtk.CheckButton)):
            return under if under.is_sensitive() else pop
        under = under.get_parent()
    return under


def _held_pick(pop, choice):
    """The held press let go over choice (a row of a Gtk.DropDown's or an Adw.ComboRow's list, a menu's item, a
    button): picked as a click on it picks it. Over none: nothing, the popover left open."""
    if choice is None or not pop.get_mapped() or not choice.get_mapped():
        return
    if isinstance(choice, (Gtk.Button, Gtk.CheckButton)) or choice.__gtype__.name == "GtkModelButton":
        choice.activate()
        return
    view = choice.get_ancestor(Gtk.ListView)  # (a row: as a click on it, by its position)
    if view is not None and choice.activate_action("listitem.select", GLib.Variant("(bb)", (False, False))):
        chosen = view.get_model().get_selection()
        if not chosen.is_empty():
            choice.activate_action("list.activate-item", GLib.Variant.new_uint32(chosen.get_minimum()))


def _dropdown_light(pop, choice):
    """choice (a row or a menu's item of pop) lit as under the pointer; the one before, not."""
    lit = getattr(pop, "lit", None)
    if lit is choice:
        return
    if lit is not None:
        lit.unset_state_flags(Gtk.StateFlags.PRELIGHT)
    if choice is not None:
        choice.set_state_flags(Gtk.StateFlags.PRELIGHT, False)
    pop.lit = choice


def tree_line(x, top, mid, end):
    """One parent's line in a tree (TreeList, TreeListView): down at x from top (under the parent) to mid (its last
    child's middle), then a corner across to end (just short of that child). end None: that child is out of sight
    below, and the line runs on down to mid with no corner."""
    if mid <= top or end is not None and end <= x:
        return []
    down = Graphene.Rect().init(x, top, 1, mid - top + 1)
    return [down] if end is None else [down, Graphene.Rect().init(x + 1, mid, end - x - 1, 1)]


def snapshot_tree_lines(widget, snapshot, rects):
    """A tree's lines (tree_line's rectangles), in the widget's text colour, faint."""
    if not rects:
        return
    color = widget.get_color()
    color.alpha *= 0.3
    for rect in rects:
        snapshot.append_color(color, rect)


class TreeList(Gtk.ListBox):
    """A ListBox whose nested rows hang off one joined line per parent, like a file tree: down from
    under the parent's anchor to its last (visible) child, then a corner across to it. Rows nest by
    `tree_parent` (a row) and are indented by INDENT per level; `tree_anchor` is the widget the line
    starts under (the parent's) and points at (the child's)."""
    INDENT = 18

    def __init__(self, line_x=5, **kw):
        super().__init__(**kw)
        self.line_x = line_x  # how far into the parent's anchor the line runs down
        self.lines = None  # the lines' rectangles, worked out at the first draw after each layout

    def do_size_allocate(self, width, height, baseline):
        Gtk.ListBox.do_size_allocate(self, width, height, baseline)
        self.lines = None  # (not every draw: a working session's pulsing dot redraws the list every frame)

    def tree_lines(self):
        last, row, out = {}, self.get_first_child(), []
        while row is not None:
            p = getattr(row, "tree_parent", None)
            if p is not None and row.is_drawable() and p.is_drawable():
                last[p] = row
            row = row.get_next_sibling()
        for p, child in last.items():
            ok, pb = p.tree_anchor.compute_bounds(self)
            ok2, cb = child.tree_anchor.compute_bounds(self)
            if ok and ok2:
                out += tree_line(round(pb.get_x() + self.line_x), round(pb.get_y() + pb.get_height() + 1),
                                 round(cb.get_y() + cb.get_height() / 2), round(cb.get_x() - 4))
        return out

    def do_snapshot(self, snapshot):
        Gtk.ListBox.do_snapshot(self, snapshot)
        if self.lines is None:
            self.lines = self.tree_lines()
        snapshot_tree_lines(self, snapshot, self.lines)


class TreeListView(Gtk.ListView):
    """TreeList's lines on a ListView of a Gtk.TreeListModel, whose rows are TreeExpanders: one joined line per open
    folder, down from under its anchor to its last child, then a corner across to it. A row's `tree_anchor` (on its
    TreeExpander; the expander itself if it has none) is what the line starts under and points at. Only the rows in
    sight have widgets, so a parent scrolled out of sight above has its line come in from the top, and one whose last
    child is out of sight below has its line run on down off the bottom."""

    def __init__(self, line_x=5, **kw):
        super().__init__(**kw)
        self.line_x = line_x
        self.lines = None

    def do_size_allocate(self, width, height, baseline):
        Gtk.ListView.do_size_allocate(self, width, height, baseline)
        self.lines = None  # (scrolled, opened or closed: it's laid out again, and drawn again, though no row in sight
        self.queue_draw()  # moved: a row past the bottom added or gone changes where a line ends)

    def tree_lines(self):
        shown, height = {}, self.get_height()  # (the rows in sight, by place: row, its widget's and anchor's bounds)
        step, w = 0, self.get_first_child()  # (step: how far in a level is, read off a nested row's indent)
        while w is not None:
            exp = w.get_first_child()
            row = exp.get_list_row() if isinstance(exp, Gtk.TreeExpander) else None
            ok, wb = w.compute_bounds(self)
            ok2, ab = getattr(exp, "tree_anchor", exp).compute_bounds(self) if row is not None else (False, None)
            if ok and ok2 and w.is_drawable() and wb.get_y() < height and wb.get_y() + wb.get_height() > 0:
                shown[row.get_position()] = (row, wb, ab)
                if not step and row.get_depth() and exp.get_child() is not None:
                    ok3, kb = exp.get_child().compute_bounds(exp)
                    step = kb.get_x() / row.get_depth() if ok3 else 0
            w = w.get_next_sibling()
        if not shown:
            return []
        first, last = min(shown), max(shown)
        top = min(wb.get_y() for _r, wb, _a in shown.values())
        bottom = max(wb.get_y() + wb.get_height() for _r, wb, _a in shown.values())
        parents = {}
        for row, _wb, ab in shown.values():
            depth, p = row.get_depth(), row.get_parent()
            while p is not None and p.get_position() not in parents:
                parents[p.get_position()] = (p, ab, depth)  # (with a row under it, to place it across by)
                p = p.get_parent()
        out = []
        for pos, (p, ub, depth) in parents.items():
            kids = p.get_children()
            n = kids.get_n_items() if kids is not None else 0
            end = p.get_child_row(n - 1).get_position() if n else -1
            if end < first or pos > last:
                continue
            if pos in shown:
                pb = shown[pos][2]
                x, y = pb.get_x() + self.line_x, pb.get_y() + pb.get_height() + 1
            else:  # (above: as far left of the row under it as it's levels up from it)
                x, y = ub.get_x() - (depth - p.get_depth()) * step + self.line_x, top - 1
            if end in shown:
                cb = shown[end][2]
                out += tree_line(round(x), round(y), round(cb.get_y() + cb.get_height() / 2), round(cb.get_x() - 4))
            else:
                out += tree_line(round(x), round(y), round(bottom + 1), None)
        return out

    def do_snapshot(self, snapshot):
        Gtk.ListView.do_snapshot(self, snapshot)
        if self.lines is None:
            self.lines = self.tree_lines()
        snapshot_tree_lines(self, snapshot, self.lines)


def sidebar_action(icon, label, tip, fn):
    """A list's New … button, as Baude's sessions have New session at their top: an icon and a word, flat."""
    box = Gtk.Box(spacing=10)
    box.append(Gtk.Image.new_from_icon_name(icon))
    box.append(Gtk.Label(label=label, xalign=0))
    btn = Gtk.Button(child=box, tooltip_text=tip)
    btn.add_css_class("flat")
    btn.add_css_class("sidebar-action")
    btn.connect("clicked", lambda *_: fn())
    return btn


# A name too long for its row in a list on the left (a chat's, a page's, a notebook's): it fades out at the end it's cut
# at, instead of ending in "…" (FadeLabel)
CUT_FADE = 28  # px: how far in from the cut the fade begins


def _fade_stops():
    if not hasattr(_fade_stops, "stops"):
        stops = []
        for offset, alpha in ((0.0, 1.0), (1.0, 0.0)):  # (what's drawn: as it is, then gone)
            stop = Gsk.ColorStop()
            stop.offset, stop.color = offset, Gdk.RGBA(red=0, green=0, blue=0, alpha=alpha)  # (a copy: set whole)
            stops.append(stop)
        _fade_stops.stops = stops
    return _fade_stops.stops


def snapshot_faded(snapshot, draw, width, height, at_start):
    """draw(snapshot), what's cut at width, with its last CUT_FADE px faded out: the right end, or at_start the left
    (right-to-left text). The mask is only over the end that fades: one over the whole of it costs the renderer more."""
    fade = min(CUT_FADE, width / 3)
    if at_start:
        strip, start, end = Graphene.Rect().init(0, 0, fade, height), Graphene.Point().init(fade, 0), \
            Graphene.Point().init(0, 0)
        rest = Graphene.Rect().init(fade, 0, width - fade, height)
    else:
        strip, start, end = Graphene.Rect().init(width - fade, 0, fade, height), \
            Graphene.Point().init(width - fade, 0), Graphene.Point().init(width, 0)
        rest = Graphene.Rect().init(0, 0, width - fade, height)
    snapshot.push_clip(rest)  # (the most of it, as it is)
    draw(snapshot)
    snapshot.pop()
    snapshot.push_mask(Gsk.MaskMode.ALPHA)  # (the mask, then what it masks)
    snapshot.append_linear_gradient(strip, start, end, _fade_stops())
    snapshot.pop()
    draw(snapshot)
    snapshot.pop()


class FadeLabel(Gtk.Label):
    """A one-line label that, too long for the width it's given, is cut and fades out at the cut instead of ending in
    "…": as narrow as anything (its minimum width is nothing), the whole of it its natural width (or max_chars
    characters' worth, the most). Right-to-left text shows its start, at the right, and fades at the left. Whatever's
    behind it (a row hovered or selected, light or dark) shows through the fade: nothing's painted over it. Its text
    isn't laid out again as its width changes, as an ellipsized label's is."""

    __gtype_name__ = "BaudeFadeLabel"

    def __init__(self, max_chars=0, **kw):
        super().__init__(**kw)
        self.max_chars = max_chars
        self.set_overflow(Gtk.Overflow.HIDDEN)

    def do_measure(self, orientation, for_size):
        low, nat, low_base, nat_base = Gtk.Label.do_measure(self, orientation, for_size)
        if orientation == Gtk.Orientation.HORIZONTAL:
            low, low_base, nat_base = 0, -1, -1
            if self.max_chars:
                metrics = self.get_pango_context().get_metrics(None, None)
                nat = min(nat, math.ceil(self.max_chars * metrics.get_approximate_char_width() / Pango.SCALE))
        return low, nat, low_base, nat_base

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        layout = self.get_layout()
        wide = layout.get_pixel_extents()[1].width
        if wide <= width:
            Gtk.Label.do_snapshot(self, snapshot)
            return
        # (cut: its start shown, whichever way it reads. GtkLabel holds it to the left, xalign 0; to the right where the
        # widgets are laid out right to left)
        rtl = layout.get_direction(0) == Pango.Direction.RTL
        held_right = self.get_direction() == Gtk.TextDirection.RTL
        shift = 0 if rtl == held_right else (wide - width if held_right else width - wide)

        def draw(s):
            if shift:
                s.save()
                s.translate(Graphene.Point().init(shift, 0))
            Gtk.Label.do_snapshot(self, s)
            if shift:
                s.restore()
        snapshot_faded(snapshot, draw, width, height, at_start=rtl)


def _data_home():
    return os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")


# the glossary's search: ⌘K on macOS (GTK calls Command Meta there), Ctrl+K elsewhere, as editors have it
PALETTE_KEY = "<Meta>k" if sys.platform == "darwin" else "<Control>k"


class RefPaletteRow(GObject.Object):
    """A row of the glossary's search: one name (a ref_palette_items item), and the words typed that found it."""
    __gtype_name__ = "BaudeRefPaletteRow"

    def __init__(self, item, words=()):
        super().__init__()
        self.item, self.words = item, list(words)


class RefPalette(Adw.Dialog):
    """⌘K / Ctrl+K: every name in the glossary, and every one Signal's catalogue has (files, branches, Kubernetes
    objects and contexts, running workloads, database tables: with Signal off too), searched as you type (each word,
    in any order; the best match first), in a table as the Kubernetes view's: Name (with the logo its links have),
    Kind, Where. Enter, or a click, opens it (on_open(item): Baude opens a looked-up name as its link does, Record
    where it is, in its viewers); Up and Down (Page Up, Page Down) pick; Esc closes. The catalogue is made afresh if
    it's old, and the table follows, a part at a time as it comes, dots at the bottom left till it's all in. space: a
    lookup.SearchSpace (Baude's what @ lists: every host's chats and the chat's folder's names from its host; Record's
    its notebook's folders, the chats and the notebooks)."""
    LIMIT = 400  # rows shown at most (a glossary has thousands)

    def __init__(self, win, space, on_open):
        super().__init__(title=wording.say("glossary.title"), content_width=760, content_height=520)
        self.win, self.space, self.on_open = win, space, on_open
        self.signal = getattr(space, "source", None)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=12, margin_end=12, margin_top=12,
                      margin_bottom=8)
        self.entry = Gtk.SearchEntry(placeholder_text=wording.say("common.search"), hexpand=True)
        self.entry.connect("search-changed", lambda *_: self.fill())
        self.entry.connect("activate", lambda *_: self.choose(self.selection.get_selected_item()))
        self.entry.connect("stop-search", lambda *_: self.close())
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_key)
        self.entry.add_controller(keys)
        box.append(self.entry)
        self.store = Gio.ListStore(item_type=RefPaletteRow)
        self.selection = Gtk.SingleSelection(model=self.store, autoselect=True, can_unselect=False)
        self.table = self.make_table(self.selection, (300, 150, 220),
                                     lambda pos: self.choose(self.store.get_item(pos)))
        self.scroll = Gtk.ScrolledWindow(child=self.table, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        box.append(self.scroll)
        # (as @'s list has it, PickList: dots at the left while more names are on their way, the count at the right)
        self.count = Gtk.Label(xalign=1, hexpand=True)
        self.count.add_css_class("dim-label")
        self.count.add_css_class("caption")
        self.dots = WorkingDots()
        self.dots.add_css_class("dim-label")
        foot = Gtk.Box(spacing=6)
        foot.append(self.dots)
        foot.append(self.count)
        box.append(foot)
        self.set_child(box)
        # (while the catalogue is made: checked for, to list what's come in as it comes)
        self.waiting = self.space.refresh(self.catalogue_in)
        self.dots.set_working(self.waiting is not None)
        self.connect("closed", lambda *_: self.waiting and GLib.source_remove(self.waiting))
        self.items = self.space.items()
        self.fill()
        self.connect("map", lambda *_: self.entry.grab_focus())

    # ---- the table (the multiple-matches popover's too)
    @classmethod
    def make_table(cls, model, widths, on_activate):
        """A ColumnView of RefPaletteRows: Name (with its logo), Kind, Where, at these widths; a click activates."""
        table = Gtk.ColumnView(model=model, show_row_separators=False, show_column_separators=False,
                               single_click_activate=True, reorderable=False)
        table.add_css_class("data-table")
        table.add_css_class("kube-table")
        for (title, cell), width in zip((("Name", cls.name_cell), ("Kind", cls.kind_cell), ("Where", cls.where_cell)),
                                        widths):
            f = Gtk.SignalListItemFactory()
            f.connect("setup", cell)
            f.connect("bind", lambda _f, li, title=title: cls.bind(li, title))
            col = Gtk.ColumnViewColumn(title=title, factory=f, expand=title != "Kind", resizable=True)
            col.set_fixed_width(width)
            table.append_column(col)
        header = table.get_first_child()  # (no headings: the columns say what they are)
        if header is not None and header.get_css_name() == "header":
            header.set_visible(False)
        table.connect("activate", lambda _t, pos: on_activate(pos))
        return table

    @staticmethod
    def name_cell(_f, li):
        line = Gtk.Box(spacing=8)
        line.append(RefIcon())
        line.append(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE))
        li.set_child(line)

    @staticmethod
    def kind_cell(_f, li):
        label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        label.add_css_class("dim-label")
        li.set_child(label)

    where_cell = kind_cell

    @staticmethod
    def bind(li, title):
        row, w = li.get_item(), li.get_child()
        item, words = row.item, row.words  # (what was typed: highlighted where it matched)
        if title == "Name":
            icon, label = w.get_first_child(), w.get_last_child()
            icon.icon = item["icon"]
            icon.queue_draw()
            find_label(label, item.get("label") or item["surface"], words, letters=True)
            label.set_tooltip_text(item["surface"] if len(item["surface"]) > 40 or item.get("label") else None)
        else:
            text = item["what"] if title == "Kind" else item["summary"]
            find_label(w, text, words)
            w.set_tooltip_text(text if len(text) > 30 else None)
            if title != "Kind":
                time_tip(w, item.get("at"), item.get("ago"))
        w.set_opacity(0.6 if item["faint"] else 1.0)  # (a place's older version: fainter)

    # ---- filling and picking
    def catalogue_in(self):
        """What's come in listed (all of it, or a part, more on its way: waited for in turn), what's picked kept picked
        if it's still there and isn't the first."""
        row = self.selection.get_selected_item() if self.selection.get_selected() else None
        self.waiting = self.space.refresh(self.catalogue_in) if self.space.busy else None
        self.dots.set_working(self.waiting is not None)
        self.items = self.space.items()
        self.fill()
        if row is not None:  # (the same row: two folders, or two hosts, can have a name)
            same = lambda it: all(it.get(k) == row.item.get(k) for k in ("surface", "what", "summary", "host", "root"))  # noqa: E731
            at = next((i for i in range(self.store.get_n_items()) if same(self.store.get_item(i).item)), None)
            if at is not None:
                self.selection.set_selected(at)
                self.table.scroll_to(at, None, Gtk.ListScrollFlags.NONE, None)

    def fill(self):
        q = self.entry.get_text().strip().lower()
        self.items = self.space.items()
        shown, matched = self.space.search(q, self.LIMIT)
        if self.waiting is None and self.space.busy:  # (made afresh since, while it's up: a search can start it)
            self.waiting = self.space.refresh(self.catalogue_in)
            self.dots.set_working(self.waiting is not None)
        self.store.splice(0, self.store.get_n_items(), [RefPaletteRow(it, q.split()) for it in shown])
        if self.store.get_n_items():
            self.selection.set_selected(0)
            self.table.scroll_to(0, None, Gtk.ListScrollFlags.NONE, None)
        self.count.set_label(wording.say("glossary.count", matched=matched, total=len(self.items)))

    def shown(self):
        """The names shown, in order."""
        return [self.store.get_item(i).item["surface"] for i in range(self.store.get_n_items())]

    def selected(self):
        row = self.selection.get_selected_item()
        return row.item if row is not None else None

    def on_key(self, _c, keyval, _code, _state):
        """Up and Down (and a page of them) pick in the table while you type."""
        step = {Gdk.KEY_Up: -1, Gdk.KEY_Down: 1, Gdk.KEY_Page_Up: -10, Gdk.KEY_Page_Down: 10}.get(keyval)
        if step is None:
            return False
        n = self.store.get_n_items()
        if n:
            at = self.selection.get_selected()
            i = min(n - 1, max(0, (0 if at == Gtk.INVALID_LIST_POSITION else at) + step))
            self.selection.set_selected(i)
            self.table.scroll_to(i, None, Gtk.ListScrollFlags.NONE, None)
        return True

    def choose(self, row):
        """Open the name, once the search is gone."""
        if row is None:
            return
        item = row.item
        self.close()
        GLib.idle_add(lambda: self.on_open(item) and False)
