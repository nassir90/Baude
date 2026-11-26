"""The Kubernetes view's Home (KubeHome): the whole stack's CPU and memory, as the host adds them up (hostkube's
summary): requested, used and limits, each a bar against what the nodes can give pods, with how they went; then the
same by namespace, and by node on a cluster of more than one. Baude and Record both show it (kubeview's KubeView)."""
import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Graphene, Gtk, Pango  # noqa: E402

from baudelib import wording  # noqa: E402
from widgets import accent_rgba  # noqa: E402

MEASURES = (("requested", "kube-requested"), ("used", "kube-used"), ("limits", "kube-limits"))


class KubeSpark(Gtk.Widget):
    """How a resource went (the host's history): what's used, a line in the used bar's colour, and what's requested,
    dashed in the requested bar's, both scaled to the most either reached (so a change shows, however small beside the
    nodes'). Nothing is drawn with fewer than two samples."""
    HEIGHT = 34

    def __init__(self):
        super().__init__(hexpand=True, height_request=self.HEIGHT, margin_top=6)
        self.add_css_class("kube-spark")  # (its colour: the used bar's)
        self.used, self.requested = [], []

    def set_samples(self, used, requested):
        self.used, self.requested = used, requested
        self.queue_draw()

    def do_snapshot(self, snap):
        w, h = self.get_width(), self.get_height()
        lines = [v for v in (self.used, self.requested) if sum(x is not None for x in v) >= 2]
        if not lines or w <= 0:
            return
        top = max(x for v in lines for x in v if x is not None) * 1.15 or 1
        cr = snap.append_cairo(Graphene.Rect().init(0, 0, w, h))
        for values, color, dash in ((self.requested, accent_rgba(), [3, 3]), (self.used, self.get_color(), [])):
            if sum(x is not None for x in values) < 2:
                continue
            cr.set_source_rgba(color.red, color.green, color.blue, 0.9)
            cr.set_line_width(1.5)
            cr.set_dash(dash)
            n, started = len(values), False
            for i, v in enumerate(values):
                if v is None:
                    started = False
                    continue
                x, y = 1 + (w - 2) * i / (n - 1), h - 2 - (h - 4) * v / top
                cr.line_to(x, y) if started else cr.move_to(x, y)
                started = True
            cr.stroke()


class KubeCard(Gtk.Box):
    """One resource's totals (KubeUse): its name, what the nodes can give pods, then Requested, Used and Limits each a
    bar of that (full and orange past it), its amount and its share; how it went under them."""

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
            bar = Gtk.LevelBar(min_value=0, max_value=1, hexpand=True, valign=Gtk.Align.CENTER)
            for name in ("low", "high", "full"):  # (no colour by how full: each measure keeps its own)
                bar.remove_offset_value(name)
            bar.add_css_class("kube-bar")
            bar.add_css_class(css)
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
        self.spark = KubeSpark()
        self.append(self.spark)

    def show(self, use, used, requested, minutes):
        self.title.set_label(use.get("title") or "")
        cells, shares = use.get("cells") or ["", "", "", ""], use.get("shares") or ["", "", ""]
        self.allocatable.set_label(cells[3])
        alloc = use.get("allocatable")
        for i, key in enumerate(("requests", "used", "limits")):
            value, bar = use.get(key), self.bars[i]
            bar.set_visible(bool(alloc))
            bar.set_value(min(1.0, (value or 0) / alloc) if alloc else 0)
            (bar.add_css_class if alloc and (value or 0) > alloc else bar.remove_css_class)("kube-over")
            self.amounts[i].set_label(cells[i])
            self.shares[i].set_label(shares[i])
        self.spark.set_samples(used, requested)
        self.spark.set_tooltip_text(wording.say("kube.history_tip", minutes=minutes))


class KubeShares(Gtk.Grid):
    """A table of namespaces' or nodes' parts (KubeShare), as the host lays it out: its columns' titles, a row each,
    numbers to the right. A namespace's name is a link: only its objects (on_namespace)."""

    def __init__(self, on_namespace=None):
        super().__init__(column_spacing=22, row_spacing=6, margin_top=18)
        self.add_css_class("kube-home-table")
        self.on_namespace, self.rows = on_namespace, []

    def show(self, columns, shares, text=""):
        while (child := self.get_first_child()) is not None:
            self.remove(child)
        self.rows = []
        for i, title in enumerate(columns):
            head = Gtk.Label(label=title, xalign=0 if i == 0 else 1, hexpand=i == 0)
            head.add_css_class("kube-home-head")
            self.attach(head, i, 0, 1, 1)
        for r, share in enumerate(shares, 1):
            cells = []
            for i, text_ in enumerate(share.get("cells") or []):
                if i == 0 and self.on_namespace is not None:
                    cell = Gtk.Button(child=Gtk.Label(label=text_, xalign=0, ellipsize=Pango.EllipsizeMode.END),
                                      halign=Gtk.Align.START,
                                      tooltip_text=wording.say("kube.only_namespace", namespace=text_))
                    cell.add_css_class("flat")
                    cell.add_css_class("kube-link")
                    cell.connect("clicked", lambda _b, ns=share.get("name"): self.on_namespace(ns))
                else:
                    cell = Gtk.Label(label=text_, xalign=0 if i == 0 else 1)
                    cell.add_css_class("numeric")
                self.attach(cell, i, r, 1, 1)
                cells.append(cell)
            self.rows.append((share.get("name") or "", cells))
        self.refilter(text)

    def refilter(self, text):
        """Only the rows any cell of which has text in it (any case)."""
        q = text.strip().lower()
        for name, cells in self.rows:
            keep = not q or any(q in (c.get_label() if isinstance(c, Gtk.Label) else name).lower() for c in cells)
            for c in cells:
                c.set_visible(keep)


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
        history, minutes = summary.get("history") or [], summary.get("minutes") or 0
        self.cpu.show(summary.get("cpu") or {}, [s.get("cpu_used") for s in history],
                      [s.get("cpu_requests") for s in history], minutes)
        self.memory.show(summary.get("memory") or {}, [s.get("memory_used") for s in history],
                         [s.get("memory_requests") for s in history], minutes)
        self.namespaces.show(summary.get("namespace_columns") or [], summary.get("namespaces") or [], self.text)
        nodes = summary.get("nodes") or []
        self.nodes.get_ancestor(Gtk.ScrolledWindow).set_visible(len(nodes) > 1)  # (as the Nodes page: one node's is the stack's own)
        self.nodes.show(summary.get("node_columns") or [], nodes, self.text)

    def refilter(self, text):
        self.text = text
        self.namespaces.refilter(text)
        self.nodes.refilter(text)
