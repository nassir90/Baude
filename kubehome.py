"""The Kubernetes view's Home (KubeHome): the whole stack's CPU and memory, as the host adds them up (hostkube's
summary): requested, used and limits, each a bar against what the nodes can give pods, with how they went; then the
same by namespace, and by node on a cluster of more than one. Baude and Record both show it (kubeview's KubeView)."""
import time

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Graphene, Gsk, Gtk, Pango  # noqa: E402

from baudelib import wording  # noqa: E402
from kubedata import kube_fmt_bytes, kube_fmt_cores, kube_shares_other  # noqa: E402
from widgets import accent_rgba  # noqa: E402

MEASURES = (("requested", "kube-requested"), ("used", "kube-used"), ("limits", "kube-limits"))


SPARK_FULL, SPARK_OWN = 0.25, 0.2  # (a history scaled to allocatable from a quarter of it; to its own peak again below
# a fifth: one that hovers about a quarter doesn't flip between the two)


def spark_top(peak, allocatable, full=False):
    """The value a history's top stands for, and whether that's allocatable: allocatable (and a little), so the lines
    read against what the nodes can give, while what's used or requested reaches a quarter of it (once it has, till it
    falls under a fifth: full, how it was scaled before); under that, the most they reached (and a little),
    allocatable then out of view, so a small use's ups and downs still show."""
    if allocatable and peak >= allocatable * (SPARK_OWN if full else SPARK_FULL):
        return max(peak, allocatable) * 1.08, True
    return peak * 1.15 or 1, False


class KubeSpark(Gtk.Widget):
    """How a resource went (the host's history): what's used, a line in the used bar's colour, and what's requested,
    dashed in the requested bar's, each sample at its time across the last `minutes` (to the newest), so a time with
    none (the host off, the cluster unreachable) is a gap; across them, allocatable dashed in the text's colour and
    the limits dashed in the limits bar's, as they are now, each while it's in view (spark_top). Hovered: a line at the
    sample nearest the pointer, its time, used, requested, limits and allocatable in the tooltip. Nothing is drawn with
    fewer than two samples."""
    HEIGHT = 48

    def __init__(self):
        super().__init__(hexpand=True, height_request=self.HEIGHT, margin_top=6, has_tooltip=True)
        self.add_css_class("kube-spark")  # (its colour: the used bar's)
        self.samples, self.minutes, self.every = [], 30, 10
        self.allocatable = self.limits = None
        self.fmt, self.hover, self.full = kube_fmt_cores, None, False
        self.connect("query-tooltip", self._tip)
        motion = Gtk.EventControllerMotion()
        motion.connect("leave", lambda *_: self._hovered(None))
        self.add_controller(motion)

    def set_samples(self, samples, minutes, every, allocatable=None, limits=None, fmt=kube_fmt_cores):
        """samples: (at, used, requested) each, oldest first; allocatable and limits: now's (None: none)."""
        self.samples, self.minutes, self.every = samples, minutes or 30, every or 10
        self.allocatable, self.limits, self.fmt = allocatable, limits, fmt
        if self.hover is not None and self.hover >= len(samples):
            self.hover = None
        self.queue_draw()

    def _lines(self):
        return [i for i in (2, 1) if sum(s[i] is not None for s in self.samples) >= 2]

    def _scale(self, lines):
        peak = max([s[i] for i in lines for s in self.samples if s[i] is not None] or [0])
        top, self.full = spark_top(peak, self.allocatable, self.full)
        return top

    def _x(self, at, w):
        return 1 + (w - 2) * (1 - (self.samples[-1][0] - at) / (60 * self.minutes))

    def _hovered(self, i):
        if i != self.hover:
            self.hover = i
            self.queue_draw()

    def _tip(self, _w, x, _y, _kb, tooltip):
        w = self.get_width()
        lines = self._lines()
        if not lines or w <= 2:
            self._hovered(None)
            tooltip.set_text(wording.say("kube.history_tip", minutes=self.minutes))
            return True
        at = self.samples[-1][0] - 60 * self.minutes * (1 - (x - 1) / (w - 2))
        near = [i for i, s in enumerate(self.samples) if any(s[j] is not None for j in lines)]
        i = min(near, key=lambda i: abs(self.samples[i][0] - at))
        if abs(self.samples[i][0] - at) > 2 * self.every:  # (in a gap)
            self._hovered(None)
            tooltip.set_text(wording.say("kube.history_tip", minutes=self.minutes))
            return True
        self._hovered(i)
        tooltip.set_text(spark_values(self.samples[i], self.limits, self.allocatable, self.fmt))
        return True

    def do_snapshot(self, snap):
        w, h = self.get_width(), self.get_height()
        lines = self._lines()
        if not lines or w <= 0:
            return
        top = self._scale(lines)
        fg = self.get_parent().get_color()
        cr = snap.append_cairo(Graphene.Rect().init(0, 0, w, h))

        def y(v):
            return h - 2 - (h - 4) * v / top
        for value, alpha, dash, width in ((self.allocatable, 0.6, [4, 3], 1.2), (self.limits, 0.45, [6, 3], 1.5)):
            if value is not None and 0 < value <= top:
                cr.set_source_rgba(fg.red, fg.green, fg.blue, alpha)
                cr.set_line_width(width)
                cr.set_dash(dash)
                cr.move_to(1, y(value))
                cr.line_to(w - 1, y(value))
                cr.stroke()
        for i in lines:
            color, dash = (accent_rgba(), [3, 3]) if i == 2 else (self.get_color(), [])
            cr.set_source_rgba(color.red, color.green, color.blue, 0.9)
            cr.set_line_width(1.5)
            cr.set_dash(dash)
            before = None
            for s in self.samples:
                if s[i] is None:
                    before = None
                    continue
                x = self._x(s[0], w)
                cr.line_to(x, y(s[i])) if before is not None and s[0] - before <= 2 * self.every else cr.move_to(x, y(s[i]))
                before = s[0]
            cr.stroke()
        if self.hover is not None:  # (the sample hovered: a line down, a dot on each line)
            s = self.samples[self.hover]
            x = self._x(s[0], w)
            cr.set_dash([])
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.35)
            cr.set_line_width(1)
            cr.move_to(x, 0)
            cr.line_to(x, h)
            cr.stroke()
            for i in lines:
                if s[i] is not None:
                    color = accent_rgba() if i == 2 else self.get_color()
                    cr.set_source_rgba(color.red, color.green, color.blue, 1)
                    cr.arc(x, y(s[i]), 2.5, 0, 6.2832)
                    cr.fill()


def spark_values(sample, limits, allocatable, fmt):
    """A history sample's tooltip: its time, then used, requested, limits and allocatable (the last two as now); N/A
    where there's none, as on the card."""
    say = fmt
    return "\n".join([time.strftime("%H:%M:%S", time.localtime(sample[0])),
                      f"{wording.say('kube.used')}  {say(sample[1])}",
                      f"{wording.say('kube.requested')}  {say(sample[2])}",
                      f"{wording.say('kube.limits')}  {say(limits)}",
                      f"{wording.say('kube.allocatable')}  {say(allocatable)}"])


class KubeBar(Gtk.Widget):
    """A measure's bar (KubeUse's): its track the card's scale (the most of allocatable, requested, used and limits);
    the measure filled in its colour up to allocatable, any part past it a segment of its own in orange, and a tick
    at allocatable when the scale goes past it."""

    def __init__(self, css):
        super().__init__(hexpand=True, valign=Gtk.Align.CENTER)
        self.add_css_class("kube-bar")
        self.fill, self.over, self.tick = Gtk.Box(), Gtk.Box(), Gtk.Box()
        self.fill.add_css_class("kube-fill")
        self.fill.add_css_class(css)
        self.over.add_css_class("kube-fill")
        self.over.add_css_class("kube-over")
        self.tick.add_css_class("kube-tick")
        for part in (self.fill, self.over, self.tick):
            part.set_parent(self)
        self.value, self.allocatable, self.scale = 0, 0, 1

    def do_dispose(self):
        for part in (self.fill, self.over, self.tick):
            part.unparent()
        Gtk.Widget.do_dispose(self)

    def set(self, value, allocatable, scale):
        """What's shown changed: which parts there are (and the fill's end square where the part past allocatable meets
        it), set here; where they go, in do_size_allocate."""
        self.value, self.allocatable, self.scale = value or 0, allocatable or 0, scale or 1
        past = self.value > self.allocatable
        self.fill.set_visible(min(self.value, self.allocatable) > 0)
        self.over.set_visible(past)
        self.tick.set_visible(self.scale > self.allocatable * 1.001)
        (self.fill.add_css_class if past else self.fill.remove_css_class)("kube-joined")
        self.queue_allocate()

    def do_measure(self, orientation, for_size):
        least = 0 if orientation == Gtk.Orientation.HORIZONTAL else 10
        for part in (self.fill, self.over):  # (the tick reaches past the bar: it's not what the bar needs)
            least = max(least, part.measure(orientation, -1)[0])
        return least, least, -1, -1

    def do_size_allocate(self, w, h, _baseline):
        def put(part, x, width, y=0, height=h):
            if part.get_visible():
                width = max(round(width), part.measure(Gtk.Orientation.HORIZONTAL, height)[0])
                height = max(height, part.measure(Gtk.Orientation.VERTICAL, width)[0])
                part.allocate(width, height, -1, Gsk.Transform().translate(Graphene.Point().init(round(x), y)))
        within = min(self.value, self.allocatable) * w / self.scale
        put(self.fill, 0, within)
        put(self.over, within, max(0, self.value - self.allocatable) * w / self.scale)
        put(self.tick, self.allocatable * w / self.scale - 1, 2, -3, h + 6)


class KubeCard(Gtk.Box):
    """One resource's totals (KubeUse): its name, what the nodes can give pods, then Requested, Used and Limits each a
    bar (KubeBar) of the most of them and allocatable, its amount and its share of allocatable; past allocatable, how
    far the most over is (Limits 2.3× allocatable); how it went under them."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        self.add_css_class("kube-home-card")
        head = Gtk.Box(spacing=8, margin_bottom=4)
        self.title = Gtk.Label(xalign=0, hexpand=True)
        self.title.add_css_class("heading")
        head.append(self.title)
        self.allocatable = Gtk.Label(xalign=1, tooltip_text=wording.say("kube.allocatable_tip"))
        self.allocatable.add_css_class("dim-label")
        head.append(self.allocatable)
        self.append(head)
        grid = Gtk.Grid(column_spacing=10, row_spacing=7)
        self.bars, self.amounts, self.shares = [], [], []
        for i, (key, css) in enumerate(MEASURES):
            label = Gtk.Label(label=wording.say(f"kube.{key}"), xalign=0, tooltip_text=wording.say(f"kube.{key}_tip"))
            grid.attach(label, 0, i, 1, 1)
            bar = KubeBar(css)
            grid.attach(bar, 1, i, 1, 1)
            amount = Gtk.Label(xalign=1, width_chars=9)
            amount.add_css_class("numeric")
            grid.attach(amount, 2, i, 1, 1)
            share = Gtk.Label(xalign=1, width_chars=5)
            share.add_css_class("numeric")
            share.add_css_class("dim-label")
            grid.attach(share, 3, i, 1, 1)
            self.bars.append(bar)
            self.amounts.append(amount)
            self.shares.append(share)
        self.append(grid)
        self.over = Gtk.Label(xalign=0, visible=False, margin_top=2)
        self.over.add_css_class("kube-warn")
        self.append(self.over)
        self.spark = KubeSpark()
        self.append(self.spark)

    def show(self, use, samples, minutes, every, memory=False):
        self.title.set_label(use.get("title") or "")
        cells, shares = use.get("cells") or ["", "", "", ""], use.get("shares") or ["", "", ""]
        self.allocatable.set_label(cells[3])
        alloc = use.get("allocatable")
        values = [use.get(key) for key in ("requests", "used", "limits")]
        scale = max([alloc or 0] + [v or 0 for v in values])
        for i, value in enumerate(values):
            self.bars[i].set_visible(bool(alloc))
            self.bars[i].set(value, alloc, scale)
            self.amounts[i].set_label(cells[i])
            self.shares[i].set_label(shares[i])
        over = max(((v, MEASURES[i][0]) for i, v in enumerate(values) if alloc and (v or 0) >= alloc * 1.05),
                   default=None)  # (under 5% over: no "1.0×")
        self.over.set_visible(over is not None)
        if over is not None:
            self.over.set_label(wording.say("kube.over_allocatable", what=wording.say(f"kube.{over[1]}"),
                                            times=f"{over[0] / alloc:.1f}"))
        self.spark.set_samples(samples, minutes, every, alloc, use.get("limits"),
                               kube_fmt_bytes if memory else kube_fmt_cores)


def share_order(rows, col, desc):
    """rows (KubeShares) sorted by column col's key, most first when desc; a row without one last either way, rows
    alike kept in the host's order (most CPU used first)."""
    def key(r):
        keys = r.get("keys") or []
        return keys[col] if col < len(keys) else None
    have = [r for r in rows if key(r) is not None]
    return sorted(have, key=key, reverse=desc) + [r for r in rows if key(r) is None]


class KubeShares(Gtk.Grid):
    """A table of namespaces' or nodes' parts (KubeShare), as the host lays it out: its columns' titles, each a button
    sorting by it (again: the other way), a row each, numbers to the right; the `shown` first as sorted, the rest
    added up in an "Other (n)" row. A namespace's name is a link: only its objects (on_namespace)."""

    def __init__(self, on_namespace=None):
        super().__init__(column_spacing=22, row_spacing=6, margin_top=18)
        self.add_css_class("kube-home-table")
        self.on_namespace, self.rows, self.columns, self.laid = on_namespace, [], [], None
        self.shares, self.shown, self.metrics, self.text = [], 10, True, ""
        self.sort = None  # (column, most first), once there's a table

    def show(self, columns, shares, text="", sorted_by=None, shown=10, metrics=True):
        if [c.get("title") for c in columns] != [c.get("title") for c in self.columns]:
            self.sort = (sorted_by, True) if sorted_by is not None else None
        self.columns, self.shares, self.shown, self.metrics, self.text = list(columns), list(shares), shown, metrics, text
        self.lay()

    def refilter(self, text):
        self.text = text
        self.lay()

    def sort_by(self, col):
        if self.sort and self.sort[0] == col:
            self.sort = (col, not self.sort[1])
        else:
            self.sort = (col, self.columns[col].get("kind") != "name")
        self.lay()

    def lay(self):
        """The rows with the filter's text in a cell (any case), sorted, the first `shown` and an "Other (n)" row of the
        rest: their texts changed in place while the columns, the order and the rows' names stay; else laid out anew."""
        q = self.text.strip().lower()
        rows = [s for s in self.shares if not q or any(q in c.lower() for c in s.get("cells") or [])]
        if self.sort is not None:
            rows = share_order(rows, *self.sort)
        rest = rows[self.shown:] if len(rows) > self.shown + 1 else []
        rows = rows[:len(rows) - len(rest)]
        if rest:
            rows.append({**kube_shares_other(self.columns, rest, wording.say("kube.other", n=len(rest)), self.metrics),
                         "other": True})
        laid = ([c.get("title") for c in self.columns], self.sort, [(r.get("name"), r["name"] == "" and
                                                                     r["cells"][0]) for r in rows])
        if laid == self.laid:
            for share, (_name, cells) in zip(rows, self.rows):
                for cell, text_ in zip(cells, share.get("cells") or []):
                    label = cell.get_child() if isinstance(cell, Gtk.Button) else cell
                    if label.get_label() != text_:
                        label.set_label(text_)
            return
        self.laid = laid
        while (child := self.get_first_child()) is not None:
            self.remove(child)
        self.rows = []
        for i, column in enumerate(self.columns):
            head = Gtk.Box(spacing=2, halign=Gtk.Align.START if i == 0 else Gtk.Align.END)
            head.append(Gtk.Label(label=column.get("title") or ""))
            if self.sort and self.sort[0] == i:
                head.append(Gtk.Image(icon_name="pan-down-symbolic" if self.sort[1] else "pan-up-symbolic",
                                      pixel_size=10))
            button = Gtk.Button(child=head, hexpand=i == 0, halign=Gtk.Align.FILL)
            button.add_css_class("flat")
            button.add_css_class("kube-home-head")
            button.connect("clicked", lambda _b, i=i: self.sort_by(i))
            self.attach(button, i, 0, 1, 1)
        for r, share in enumerate(rows, 1):
            cells = []
            for i, text_ in enumerate(share.get("cells") or []):
                if i == 0 and self.on_namespace is not None and not share.get("other"):
                    cell = Gtk.Button(child=Gtk.Label(label=text_, xalign=0, ellipsize=Pango.EllipsizeMode.END),
                                      halign=Gtk.Align.START,
                                      tooltip_text=wording.say("kube.only_namespace", namespace=text_))
                    cell.add_css_class("flat")
                    cell.add_css_class("kube-link")
                    cell.connect("clicked", lambda _b, ns=share.get("name"): self.on_namespace(ns))
                else:
                    cell = Gtk.Label(label=text_, xalign=0 if i == 0 else 1)
                    cell.add_css_class("numeric")
                    if share.get("other") and i == 0:
                        cell.add_css_class("dim-label")
                self.attach(cell, i, r, 1, 1)
                cells.append(cell)
            self.rows.append((share.get("name") or "", cells))


class KubeHome(Gtk.ScrolledWindow):
    """Home (see the module): shown in the table's place while it's the page picked."""

    def __init__(self, on_namespace):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, hexpand=True)  # (the cards fit the
        # width, one above the other where there isn't room; a table wider than it scrolls sideways on its own)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=22, margin_end=22, margin_top=18,
                      margin_bottom=18)
        self.cards = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, max_children_per_line=2,
                                 min_children_per_line=1, column_spacing=18, row_spacing=18)
        self.cpu, self.memory = KubeCard(), KubeCard()
        for card in (self.cpu, self.memory):
            self.cards.append(card)
            card.get_parent().set_focusable(False)
        box.append(self.cards)
        self.namespaces = KubeShares(on_namespace)
        box.append(self.sideways(self.namespaces))
        self.nodes = KubeShares()
        box.append(self.sideways(self.nodes))
        self.set_child(box)
        self.text = ""

    @staticmethod
    def sideways(table):
        return Gtk.ScrolledWindow(child=table, vscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True)

    def show(self, summary):
        history, minutes, every = summary.get("history") or [], summary.get("minutes") or 0, summary.get("every") or 0
        for card, key in ((self.cpu, "cpu"), (self.memory, "memory")):
            card.show(summary.get(key) or {}, [(s["at"], s.get(f"{key}_used"), s.get(f"{key}_requests"))
                                               for s in history], minutes, every, key == "memory")
        how = {"sorted_by": summary.get("sorted_by"), "shown": summary.get("shown") or 10,
               "metrics": bool(summary.get("metrics"))}
        self.namespaces.show(summary.get("namespace_columns") or [], summary.get("namespaces") or [], self.text, **how)
        nodes = summary.get("nodes") or []
        self.nodes.get_ancestor(Gtk.ScrolledWindow).set_visible(len(nodes) > 1)  # (as the Nodes page: one node's is the stack's own)
        self.nodes.show(summary.get("node_columns") or [], nodes, self.text, **how)

    def refilter(self, text):
        self.text = text
        self.namespaces.refilter(text)
        self.nodes.refilter(text)
