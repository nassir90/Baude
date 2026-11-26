"""The Kubernetes view (KubeView: your clusters' objects, as Freelens lists them; KubeWatch keeps its lists live), with what only it needs. Baude and Record both use it; its window is a host (what it asks of one: input, to focus when it closes)."""
import argparse
import array
import base64
import glob
import hashlib
import html
import itertools
import json
import math
import os
import re
import shutil
import socket
import struct
import weakref
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import queue
import urllib.parse

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Graphene, Gsk, Gtk, Pango  # noqa: E402,F401

import lookup  # noqa: E402,F401
from lookupkube import (  # noqa: E402,F401  (⌘K's engine: what a folder has)
    _km, _ks, kube_contexts, kube_fault, kube_fmt_age, kube_get, kube_local, kube_run, kube_seconds, kube_tool,
    kubeconfig_paths,
)
from widgets import (  # noqa: E402,F401
    WorkerPool, ensure_icons, find_label, full_time, inside, load_settings, paned_grip, pretty_path, save_settings,
    insert_actions, time_tip, toast, view_bar, _scales,
)
from imageviews import Fade, RefreshButton  # noqa: E402,F401
from pickers import CheckList, ellipsize_dropdown  # noqa: E402,F401
from codeview import (  # noqa: E402,F401
    CodeView,
)
from baudelib import deps, tokens, wording  # noqa: E402
import kubehost  # noqa: E402
from baudelib import hostkube  # noqa: E402
from kubehome import KubeHome  # noqa: E402
from kubedock import KubeDock, LogsTab, ShellTab  # noqa: E402
import viewwindow  # noqa: E402


# ---------------------------------------------------------- kubernetes --
# ☰ → Kubernetes…: a view of a cluster, laid out like Freelens: its sidebar's categories, each kind's list with
# Freelens' columns, pods with their CPU and memory from the metrics API (every 10 s, as Freelens reads them).
# kubectl does the talking, with your kubeconfig's own auth; it's asked to get things (and to list the API's
# resources), to follow a pod's logs, and to delete a pod only once you've confirmed it; helm only to list releases.
# A Secret's values are never read: only its keys' names.

KUBE_POOL = WorkerPool(3)


from kubedata import (  # noqa: E402,F401  (the data the view shows, with no GTK)
    KUBE_REFRESH_S, KUBE_LOG_TAIL, KUBE_LOG_LINES, _KUBE_KLOG, kube_diagnose, kube_signin, kube_api_resources,
    kube_delete_pod, kube_uid, kube_cpu, kube_bytes, kube_fmt_bytes, kube_pod_status, KUBE_RESTARTED_RECENTLY_S,
    kube_container_look, kube_containers, kube_status_tone, _kcount, kube_col, _age_col, K_NAME, K_NS, K_AGE, _ncol,
    _pod_ready, _pod_usage, KUBE_REF_FIELDS, KUBE_REF_SKIP, kube_yaml_refs, KUBE_CLUSTER_KINDS, kube_resource,
    KUBE_CLIMB, kube_controller, _owner, _true_conditions, _job_status, _job_duration, _svc_ports, _lb, _svc_status,
    _ing_rules, _endpoints, _slice_endpoints, _subjects, _keys, _node_roles, _node_ip, _node_ready, _node_usage,
    _cpu_col, _mem_col, _status_col, _KUBE_JSONPATHS, kube_jsonpath, _kube_jp_name, _kube_jp_close, _kube_jp_literal,
    _kube_jp_bracket, _KUBE_JP_OPS, _kube_jp_test, _kube_jp_descend, kube_jsonpath_find, _kube_cell_text,
    kube_printer_cell, _KUBE_MUST_HOLD, KUBE_CONDITION_TONES, _KUBE_CONDITION, kube_condition_tone, kube_col_title,
    kube_printer_col, _KUBE_PRINTED, kube_printer_columns, kube_crd_printer, kube_crd_page, _kp, _kp_condition,
    KEDA_SCALED_OBJECT, KEDA_SCALED_JOB, KEDA_TRIGGER_AUTH, CERT_MANAGER_CERTIFICATE, CERT_MANAGER_ISSUER, kube_page,
    KUBE_HOME, KUBE_PODS, KUBE_NODES, KUBE_SECTIONS, KUBE_SECTION_ICONS, KUBE_SECRETS_TEMPLATE, kube_custom_page, _KUBE_PRINTERS,
    kube_page_columns, kube_page_crd, kube_list, kube_metrics_ctx, kube_pod_metrics, kube_node_metrics, kube_rows,
)
from kubedata import kube_cell_tone, kube_delete, kube_secret_yaml  # noqa: E402,F401


class KubeLogTail:
    """kubectl logs (args), followed: what it writes read on a thread of its own and handed to on_text(tail, text) on
    the main thread in batches, however fast lines come; on_end(tail, exit code) once it exits by itself. stop()
    ends it: kubectl is terminated (killed if it lingers) and reaped, and nothing more is handed on."""
    BATCH_MS = 100

    def __init__(self, args, on_text, on_end):
        self.on_text, self.on_end, self.args = on_text, on_end, args
        self.pending, self.lock, self.scheduled, self.stopped = [], threading.Lock(), False, False
        self.proc = subprocess.Popen([kube_tool("kubectl"), *args], stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        threading.Thread(target=self._read, daemon=True, name="baude-kube-logs").start()

    def _read(self):
        import codecs
        decode = codecs.getincrementaldecoder("utf-8")(errors="replace").decode
        fd = self.proc.stdout.fileno()
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                chunk = b""
            if not chunk:
                break
            with self.lock:
                self.pending.append(decode(chunk))
                if not self.scheduled:
                    self.scheduled = True
                    GLib.timeout_add(self.BATCH_MS, self._flush)
        self.proc.stdout.close()
        code = self.proc.wait()
        GLib.idle_add(self._ended, code)

    def _flush(self):
        with self.lock:
            text, self.pending, self.scheduled = "".join(self.pending), [], False
        if text and not self.stopped:
            self.on_text(self, text)
        return False

    def _ended(self, code):
        self._flush()  # (what came last, before saying it ended)
        if not self.stopped:
            self.stopped = True
            self.on_end(self, code)
        return False

    def stop(self):
        self.stopped = True
        if self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass
            GLib.timeout_add(3000, lambda: (self.proc.poll() is None and self.proc.kill()) and False)


class KubeWatch:
    """A kind's objects, watched as Freelens watches them (its KubeObjectStore): kubectl lists them and then watches
    from that list's version (`get --watch --output-watch-events -o json`, so nothing falls between the two), and the
    events it writes are handed to on_events(watch, [event]) on the main thread a second's worth at a time, however
    fast they come (Freelens' eventsBuffer, flushed after 1000 ms). on_end(watch, exit code, all kubectl wrote) once
    it exits by itself (the API server ends a watch after a while: it's started again). stop() ends it."""
    BATCH_MS = 1000

    def __init__(self, args, on_events, on_end):
        self.on_events, self.on_end, self.args = on_events, on_end, args
        self.pending, self.lock, self.scheduled, self.stopped, self.seen = [], threading.Lock(), False, False, 0
        if (fault := kube_fault("kubectl")) is not None:  # (BAUDE_KUBE_FAULT: it fails as kubectl would)
            self.proc = None
            GLib.idle_add(self._ended, 1, fault)
            return
        self.proc = subprocess.Popen([kube_tool("kubectl"), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     stdin=subprocess.DEVNULL)
        threading.Thread(target=self._read, daemon=True, name="baude-kube-watch").start()

    def _read(self):
        import codecs
        decode, parse = codecs.getincrementaldecoder("utf-8")(errors="replace").decode, json.JSONDecoder().raw_decode
        fd, buf = self.proc.stdout.fileno(), ""
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                chunk = b""
            if not chunk:
                break
            buf += decode(chunk)
            events = []
            while True:  # (kubectl writes each event as indented JSON: one can straddle two reads)
                buf = buf.lstrip()
                try:
                    event, end = parse(buf)
                except ValueError:
                    break
                buf = buf[end:]
                if isinstance(event, dict) and event.get("type"):
                    events.append(event)
            if events:
                with self.lock:
                    self.pending += events
                    if not self.scheduled:
                        self.scheduled = True
                        GLib.timeout_add(self.BATCH_MS, self._flush)
        self.proc.stdout.close()
        said = (self.proc.stderr.read() or b"").decode(errors="replace").strip()
        self.proc.stderr.close()
        code = self.proc.wait()
        GLib.idle_add(self._ended, code, said)

    def _flush(self):
        with self.lock:
            events, self.pending, self.scheduled = self.pending, [], False
        if events and not self.stopped:
            self.seen += len(events)
            self.on_events(self, events)
        return False

    def _ended(self, code, said):
        self._flush()
        if not self.stopped:
            self.stopped = True
            self.on_end(self, code, said)
        return False

    def stop(self):
        self.stopped = True
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass
            GLib.timeout_add(3000, lambda: (self.proc.poll() is None and self.proc.kill()) and False)


class KubeRow(GObject.Object):
    """A row of the Kubernetes view's table: its cells' texts, what each sorts by, and which object it is."""
    __gtype_name__ = "BaudeKubeRow"

    def __init__(self, texts, keys, obj):
        super().__init__()
        self.texts, self.keys, self.obj = texts, keys, obj


_KUBE_ORDER = {-1: Gtk.Ordering.SMALLER, 0: Gtk.Ordering.EQUAL, 1: Gtk.Ordering.LARGER}


def kube_order(a, b):
    """Two sort keys compared (-1, 0, 1): numbers (and tuples of them) before text, nothing last."""
    def norm(k):
        if k is None or k == "":
            return (2, 0, "")
        if isinstance(k, (bool, int, float)) or isinstance(k, tuple):
            return (0, k, "")
        return (1, 0, str(k))
    x, y = norm(a), norm(b)
    try:
        return (x > y) - (x < y)
    except TypeError:  # (a tuple against a number: by their texts)
        return (str(x) > str(y)) - (str(x) < str(y))


class KubeSquares(Gtk.Widget):
    """A pod's containers in the Pods table, a small square each, as Freelens draws them: solid green, running and
    ready; solid orange, getting ready; green ringed with orange, ready but restarted lately; solid red, crashed or failed;
    grey, done (kube_container_look). Its tooltip names each container and its state."""
    SIZE, GAP = tokens.px("kube.square"), tokens.px("kube.square-gap")
    # GNOME's palette (light, dark): fills, so the brighter shades, not the theme's text colours
    COLORS = {look: tuple(tokens.color_hex(f"kube.{name}", theme) for theme in ("light", "dark"))
              for look, name in (("ok", "ok"), ("starting", "starting"), ("restarted", "starting"), ("bad", "bad"))}

    def __init__(self):
        super().__init__(halign=Gtk.Align.START, valign=Gtk.Align.CENTER)
        self.looks = []

    def set_containers(self, containers):
        """containers: kube_containers' [(name, look, words, init)]."""
        self.looks = [look for _n, look, _w, _i in containers]
        self.set_tooltip_text("\n".join((wording.say("kube.init_container", name=name) if init else name) + ": " + words
                                        for name, _l, words, init in containers) or None)
        self.queue_resize()

    def do_measure(self, orientation, _for_size):
        n = len(self.looks)
        size = n * self.SIZE + max(0, n - 1) * self.GAP if orientation == Gtk.Orientation.HORIZONTAL else self.SIZE
        return size, size, -1, -1

    def do_snapshot(self, snap):
        dark = Adw.StyleManager.get_default().get_dark()
        y = round((self.get_height() - self.SIZE) / 2)
        for i, look in enumerate(self.looks):
            color = Gdk.RGBA()
            if look == "done":  # (grey: the text's colour, faint)
                color = self.get_color()
                color.alpha *= 0.35
            else:
                color.parse(self.COLORS[look][dark])
            rect = Graphene.Rect().init(i * (self.SIZE + self.GAP), y, self.SIZE, self.SIZE)
            box = Gsk.RoundedRect()  # (kept in a variable: init_from_rect on a temporary returns freed memory)
            box.init_from_rect(rect, 2)
            if look == "restarted":  # (ready, so green; ringed with orange: it fell over lately)
                fill = Gdk.RGBA()
                fill.parse(self.COLORS["ok"][dark])
                snap.push_rounded_clip(box)
                snap.append_color(fill, rect)
                snap.pop()
                snap.append_border(box, [1] * 4, [color] * 4)  # (a whole pixel: crisp on any screen)
            else:
                snap.push_rounded_clip(box)
                snap.append_color(color, rect)
                snap.pop()


class KubeView(Gtk.Box):
    """☰ → Kubernetes…: a cluster's objects, as Freelens lays them out (see "kubernetes" above). The top bar: your
    kubeconfig's context, the namespaces shown (any set of them; a pod's namespace, clicked, shows only that one), a
    filter (Ctrl+F), Refresh. The sidebar: Freelens' categories (Nodes only when there's more than one). The table:
    the kind's objects with Freelens' columns; click a column to sort by it, again to reverse; a pod's row ends with
    its logs and a menu to delete it. Selecting a row shows its YAML (a Secret: only its keys' names); a pod's logs
    show under the table, followed. Esc closes the logs, then the YAML, then the view."""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, visible=False, focusable=True)
        self.add_css_class("baude-frame")  # (the bar and the sidebar: theming.CSS; the rest the inside, set into it)
        ensure_icons()
        self.win, self.contexts, self.context = win, [], None
        self.ns_shown, self.ns_by_context, self.ns_checks = None, {}, {}  # (None: all of them)
        self.page, self.table_page, self.resources, self.namespaces = KUBE_HOME, None, None, []
        self.generation, self.loading, self.timer, self.syncing = 0, False, None, False
        self.store = self.filter = self.selection = self.table = self.table_cols = None
        self.pending_pick = None  # (name, namespace) to select once the page is in (an owner clicked)
        # the page's objects by uid, as last read or watched; how its rows were made (now, metrics); the watch
        self.objects, self.ctx, self.watch, self.watch_fails = {}, {"now": 0, "metrics": None, "node_metrics": None}, None, 0
        self.metrics_note, self.node_count, self.note, self.dialog = "", 0, None, None
        self.found = weakref.WeakKeyDictionary()  # the table's labels and their text (find_label)
        self.host = kubehost.Host(None, {"via": "local"}, wording.say("senders.local"))  # (where it reads: open)
        self.last_context = {}  # (by host: the context it showed last; this machine's is in the settings)
        bar = view_bar()
        title = Gtk.Label(label=wording.say("window.kubernetes"))
        title.add_css_class("heading")
        bar.append(title)
        self.host_label = viewwindow.host_label()
        bar.append(self.host_label)
        self.context_dd = ellipsize_dropdown(Gtk.DropDown.new_from_strings([]), 32)
        self.context_dd.set_tooltip_text(wording.say("kube.context_tip"))
        self.context_dd.connect("notify::selected", self.on_pick)
        bar.append(self.context_dd)
        # the namespaces shown: a check each, any set of them at once (Freelens' multi-select), or all of them
        self.ns_label = Gtk.Label(label=wording.say("kube.all_namespaces"), ellipsize=Pango.EllipsizeMode.END,
                                  max_width_chars=24)
        pick = self.ns_pick = CheckList(wording.say("kube.all_namespaces"), self.show_namespaces)  # (the sidebar's
        # ⋮ → Groups too)
        self.ns_all = pick.all
        self.ns_btn = Gtk.MenuButton(child=self.ns_label, always_show_arrow=True, popover=Gtk.Popover(child=pick),
                                     tooltip_text=wording.say("kube.namespaces_tip"))
        bar.append(self.ns_btn)
        self.search = Gtk.SearchEntry(placeholder_text=wording.say("kube.filter"), width_chars=22,
                                     tooltip_text=wording.say("kube.filter_tip"))
        self.search.connect("search-changed", lambda *_: self.refilter())
        self.search.connect("stop-search", lambda e: e.set_text("") if e.get_text() else self.escape())  # (Esc in it)
        bar.append(self.search)
        bar.append(Gtk.Box(hexpand=True))
        self.refresh_btn = RefreshButton(tooltip_text=wording.say("common.read_again"))
        self.refresh_btn.connect("clicked", lambda *_: self.load())
        bar.append(self.refresh_btn)
        self.out = viewwindow.Out(self, bar, wording.say("window.kubernetes"), self.search)  # (out to a window of
        # its own, as ☰ → Terminal's goes; its close)
        self.append(Gtk.WindowHandle(child=bar))  # (dragged, it moves the window; double-clicked, maximizes)
        # the sidebar: Freelens' categories (a category of one kind is just that kind: Nodes, Namespaces, Events)
        self.side = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.side.add_css_class("navigation-sidebar")
        self.side.add_css_class("kube-side")
        self.page_rows, self.head_rows = {}, {}
        # a category folds its kinds away (click its heading), as Freelens' do; which are folded is remembered
        self.folded = set(load_settings().get("kube_folded") or [])
        for section, pages in KUBE_SECTIONS:
            icon = KUBE_SECTION_ICONS.get(section)
            if not (len(pages) == 1 and pages[0]["title"] == section):
                head = Gtk.Box(spacing=8, margin_start=6, margin_end=4)  # (its gap above is the row's: kube-side-head-row)
                if icon:
                    head.append(Gtk.Image(icon_name=icon))
                head.append(Gtk.Label(label=section, xalign=0, hexpand=True))
                head.append(Gtk.Image(icon_name="pan-end-symbolic" if section in self.folded else "pan-down-symbolic"))
                head.add_css_class("kube-side-head")
                row = Gtk.ListBoxRow(child=head, activatable=True, selectable=False)
                row.add_css_class("kube-side-head-row")
                row.section = section
                self.head_rows[section] = row
                self.side.append(row)
                for page in pages:
                    self.add_page_row(page, section=section)
            else:  # (a category of one kind: its row has the category's icon)
                self.add_page_row(pages[0], nested=False, icon=icon)
        self.show_heads()
        self.update_nodes_row()
        self.side.connect("row-selected", self.on_side)
        self.side.connect("row-activated", lambda _b, row: hasattr(row, "section") and not hasattr(row, "page")
                          and self.fold(row.section, row.section not in self.folded))
        side_scroll = Gtk.ScrolledWindow(child=self.side, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                       width_request=tokens.px("kube.sidebar-width"))
        # the table, its status line; the selected object's YAML beside it
        self.table_scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.fade = Fade(self.table_scroll)  # (another context, kind or namespace: the table fades until it's in)
        self.status = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.status.add_css_class("kube-status-line")
        table_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.table_stack = Gtk.Stack(vexpand=True, hexpand=True)  # (the table, or why it can't be read)
        self.table_stack.add_named(self.table_scroll, "table")
        self.table_stack.add_named(self.build_error(), "error")
        self.home = KubeHome(self.set_namespace)  # (Home: the stack's CPU and memory, in the table's place)
        self.table_stack.add_named(self.home, "home")
        import depsview
        self.missing = depsview.Missing(again=self.read_contexts)  # (kubectl isn't on the host: show_missing)
        self.table_stack.add_named(self.missing, "missing")
        table_box.append(self.table_stack)
        table_box.append(self.status)
        self.details = CodeView()
        self.details_fade = Fade(self.details)  # (read again: the YAML shown goes grey until the new one's in)
        self.details_shown = None  # (the object whose YAML is shown, and its version)
        head = Gtk.Box(spacing=6, margin_start=10, margin_end=6, margin_top=6)
        self.details_title = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE)
        self.details_title.add_css_class("heading")
        head.append(self.details_title)
        hide = Gtk.Button(icon_name="window-close-symbolic", tooltip_text=wording.say("kube.hide"))
        hide.add_css_class("flat")
        hide.connect("clicked", lambda *_: self.hide_details())
        head.append(hide)
        self.details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=False, width_request=360)
        self.details_box.append(head)
        self.details_box.append(self.details)
        right = paned_grip(Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, start_child=table_box,
                                     end_child=self.details_box, resize_start_child=True, resize_end_child=False,
                                     shrink_end_child=False))
        self.right = right
        # pods' logs and shells under the table and the YAML, the width of both, as Freelens docks them (a log's lines
        # are long): a tab each, on a strip in the frame's grey
        self.dock = KubeDock()
        self.lower = inside(right)
        self.dock_split = paned_grip(Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, start_child=self.lower,
                                               end_child=self.dock, resize_start_child=True, resize_end_child=False,
                                               shrink_end_child=False))
        self.dock_split.add_css_class("main-split")  # (on the frame: its rule unseen, still dragged)
        body = paned_grip(Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, start_child=side_scroll,
                                    end_child=self.dock_split, resize_start_child=False, shrink_start_child=False,
                                    vexpand=True))
        body.add_css_class("main-split")  # (its rule unseen: the inside's edge is the line; still dragged)
        self.append(body)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, kv, _k, _s: (self.escape(), True)[1] if kv == Gdk.KEY_Escape else False)
        self.add_controller(keys)
        # a pod's row: its logs, deleting it (targets: "namespace/name"; a row's buttons hold no pod of their own)
        actions = Gio.SimpleActionGroup()
        for name, fn in (("logs", self.show_logs), ("shell", self.show_shell), ("delete", lambda key: self.ask_delete(key, False)),
                         ("force-delete", lambda key: self.ask_delete(key, True))):
            act = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            act.connect("activate", lambda _a, target, fn=fn: fn(target.get_string()))
            actions.add_action(act)
        insert_actions(self, "kube", actions)

    def build_error(self):
        """What's shown instead of the table when the cluster can't be read: what went wrong (kube_diagnose), the
        command that fixes it (a sign-in, say) to copy, Try Again, and all kubectl wrote, folded away."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, valign=Gtk.Align.CENTER,
                      halign=Gtk.Align.CENTER, margin_start=24, margin_end=24, margin_top=24, margin_bottom=24)
        box.add_css_class("kube-error")
        icon = Gtk.Image(icon_name="dialog-warning-symbolic", pixel_size=2 * _scales["icon_px"])
        icon.add_css_class("dim-label")
        box.append(icon)
        self.error_title = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.error_title.add_css_class("title-3")
        box.append(self.error_title)
        self.error_why = Gtk.Label(wrap=True, selectable=True, justify=Gtk.Justification.CENTER, max_width_chars=72)
        self.error_why.add_css_class("dim-label")
        box.append(self.error_why)
        self.error_fix = Gtk.Label(selectable=True, xalign=0)
        self.error_fix.add_css_class("mono")
        copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text=wording.say("common.copy"))
        copy.add_css_class("flat")
        copy.connect("clicked", lambda *_: (self.get_clipboard().set(self.error_fix.get_label()),
                                            toast(self.win, wording.say("kube.copied_try_again"))))
        self.error_fix_row = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER)
        self.error_fix_row.add_css_class("kube-error-fix")
        self.error_fix_row.append(self.error_fix)
        self.error_fix_row.append(copy)
        box.append(self.error_fix_row)
        again = Gtk.Button(label=wording.say("common.try_again"), halign=Gtk.Align.CENTER)
        again.add_css_class("pill")
        again.connect("clicked", lambda *_: self.try_again())
        box.append(again)
        self.error_said = Gtk.Label(selectable=True, xalign=0, yalign=0, wrap=True,
                                    wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.error_said.add_css_class("mono")
        self.error_more = Gtk.Expander(label=wording.say("kube.kubectl_output"), child=Gtk.ScrolledWindow(
            child=self.error_said, propagate_natural_height=True, max_content_height=220,
            hscrollbar_policy=Gtk.PolicyType.NEVER))
        box.append(self.error_more)
        return Gtk.ScrolledWindow(child=box, hscrollbar_policy=Gtk.PolicyType.NEVER)

    def show_error(self, diag, said):
        """The cluster can't be read: why, instead of the table (and a word of it in the status line)."""
        self.error_title.set_label(diag["title"])
        self.error_why.set_label(diag["why"])
        self.error_why.set_visible(bool(diag["why"]))
        self.error_fix.set_label(diag["fix"] or "")
        self.error_fix_row.set_visible(bool(diag["fix"]))
        self.error_said.set_label(said or "")
        self.error_more.set_visible(bool(said) and said.strip() != diag["why"])
        self.table_stack.set_visible_child_name("error")
        self.say(f"{self.context}: {diag['title']}")

    def try_again(self):
        if self.resources is None:  # (it never connected: from the start)
            self.switch_context(self.context, self.ns_shown)
        else:
            self.load(switch=True)

    def add_page_row(self, page, nested=True, after=None, icon=None, section=None):
        label = Gtk.Label(label=page["title"], xalign=0, ellipsize=Pango.EllipsizeMode.END)
        # (a kind under a category lines up with the category's name, past its icon)
        child = Gtk.Box(spacing=8, margin_start=30 if nested else 6)
        if icon:
            child.append(Gtk.Image(icon_name=icon))
        child.append(label)
        row = Gtk.ListBoxRow(child=child)
        row.page = page
        row.section = section if section is not None else getattr(after, "section", None)
        row.served = not page.get("known")  # (a known custom resource's: not listed until the cluster serves it)
        if after is None:
            self.side.append(row)
        else:
            self.side.insert(row, after.get_index() + 1)
        self.page_rows[id(page)] = row
        row.set_visible(row.served and row.section not in self.folded)
        return row

    def show_heads(self):
        """A category's heading only while the cluster serves one of its kinds (KEDA's, on a cluster with KEDA)."""
        for section, head in self.head_rows.items():
            head.set_visible(any(r.served for r in self.page_rows.values() if r.section == section))

    def fold(self, section, folded):
        """Fold a category's kinds away (folded) or show them again; remembered (settings: "kube_folded")."""
        (self.folded.add if folded else self.folded.discard)(section)
        save_settings(kube_folded=sorted(self.folded))
        self.head_rows[section].get_child().get_last_child().set_from_icon_name(
            "pan-end-symbolic" if folded else "pan-down-symbolic")
        for row in self.page_rows.values():
            if row.section == section:
                row.set_visible(not folded and row.served)

    # ---- opening and closing
    def open(self, context=None, page=None, search=None, namespace=None):
        """Show the view, on the host picked in the window's host dropdown (as ☰ → Terminal's new tabs are; its
        kubeconfig, its kubectl): on context (else the one used last there, else its kubeconfig's current one), at page
        (Pods by default), filtered by search, in namespace (else the namespaces shown in that context last). Out in a
        window of its own: that window brought up."""
        host = kubehost.picked(self.win)
        same_host = host.key() == self.host.key()
        if not same_host:  # (another host's clusters: nothing of the last one's shown)
            self.leave_host()
        self.host = host
        self.host_label.set_label(host.name)
        if self.out.present():
            if same_host and context is page is search is namespace is None:
                return
        else:
            self.set_visible(True)
            self.grab_focus()
        if search is not None:
            self.search.set_text(search)
        if page is not None:
            self.page = page
        self.select_page_row(self.page)
        if self.timer is None:
            self.timer = GLib.timeout_add_seconds(KUBE_REFRESH_S, self.tick)
        self.read_contexts(context, namespace)

    def read_contexts(self, context=None, namespace=None):
        """The host's kubeconfig contexts read (there, by its host process, if it's another), the one wanted then
        picked and read; without kubectl there, what's missing shown instead."""
        host = self.host
        if self.table_stack.get_visible_child_name() == "missing":  # (a retry: the missing state is let go of first,
            self.table_stack.set_visible_child_name("table")  # as the phone's KubeState.open does)
        if host.here:
            if (missing := deps.missing("kubectl")) is not None:
                return self.show_missing(missing)
            contexts = [c for c in kube_contexts() if not c.get("cluster_only")]
            where = ", ".join(pretty_path(p) for p in kubeconfig_paths()) or wording.say("kube.no_kubeconfig")
            self._contexts(host, contexts, where, context, namespace)
            return
        self.say(wording.say("kube.connecting", context=host.name))
        host.ask("kube", {"what": "contexts"}, lambda r: self.show_missing(r["missing"], host) if r.get("missing") else
                 self._contexts(host, r.get("contexts") or [], r.get("where") or "", context, namespace, r.get("error")))

    def show_missing(self, dep, host=None):
        """kubectl (dep, a Dep) isn't on the host: what's missing and how it's installed, in the table's place."""
        if host is not None and host is not self.host:
            return
        self.generation += 1  # (nothing read before this shown over it)
        self.loading = False
        self.refresh_btn.set_loading(False)
        self.stop_watch()
        if self.store is not None:
            self.store.remove_all()
        self.fade.done()
        self.missing.show(dep, "window.kubernetes")
        self.table_stack.set_visible_child_name("missing")
        self.say("")

    def _contexts(self, host, contexts, where, context, namespace, error=None):
        """The host's kubeconfig contexts are in: the one wanted picked and read."""
        if host is not self.host:
            return
        self.contexts = contexts
        names = [c["name"] for c in self.contexts]
        if error or not names:
            self.say(f"{host.name}: {error}" if error else wording.say("kube.no_context", where=where))
            return
        last = load_settings().get("kube_context") if host.here else self.last_context.get(host.key())
        current = next((c["name"] for c in self.contexts if c.get("current")), None)
        want = context if context in names else last if last in names else current or names[0]
        self.syncing = True  # (no "(current)" after one: Freelens doesn't, and the one it opens on says enough)
        self.context_dd.set_model(Gtk.StringList.new(names))
        self.context_dd.set_selected(names.index(want))
        self.syncing = False
        self.switch_context(want, namespaces=[namespace] if namespace else None)

    def leave_host(self):
        """Another host picked: the last one's clusters let go of (its watch, its logs and shells, its table)."""
        self.dock.close_all()
        self.stop_watch()
        self.hide_details()
        self.generation += 1
        self.context, self.contexts, self.resources, self.ns_by_context = None, [], None, {}
        self.syncing = True
        self.context_dd.set_model(Gtk.StringList.new([]))
        self.syncing = False
        if self.store is not None:
            self.store.remove_all()
        self.table_stack.set_visible_child_name("table")

    def close(self):
        self.set_visible(False)
        self.dock.close_all()
        self.stop_watch()
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None
        self.win.input.grab_focus()

    def escape(self):
        if self.dock.get_visible():
            self.dock.close_current()
        elif self.details_box.get_visible():
            self.hide_details()
        elif self.out.window is None:  # (out in a window of its own: that window's to close)
            self.close()

    def find(self):
        """Ctrl+F while the view is open: its filter, focused, its text selected to type over. The window's Ctrl+F asks
        here first, as the view covers the window (so not the find bar of a Code page under it); in the YAML, the
        YAML's own Ctrl+F finds in it. False if the view is closed, or out in a window of its own (its own Ctrl+F)."""
        if not self.out.here():
            return False
        self.search.grab_focus()
        self.search.select_region(0, -1)
        return True

    def tick(self):  # (the pods' CPU and memory read again while the view is open; the list too, if it isn't watched)
        if self.get_visible() and not self.loading and self.context:
            if self.watch is not None:
                if self.host.here:  # (another host's watch reads them there)
                    self.refresh_metrics()
            else:
                self.load(quiet=True)
        return True

    def say(self, text):
        self.status.set_label(text)

    # ---- picking a context, namespaces, a kind
    def on_pick(self, dd, _pspec):
        if self.syncing:
            return
        GLib.idle_add(lambda: self.picked(dd) and False)  # (not from inside the drop-down's own signal)

    def picked(self, dd):
        if dd is self.context_dd and self.contexts:
            self.switch_context(self.contexts[dd.get_selected()]["name"])

    def switch_context(self, name, namespaces=None):
        """Read a context's namespaces and the resources it serves, then the page; the table fades meanwhile (the
        other cluster's object isn't shown beside it). It shows namespaces, else those it showed last time."""
        self.context, self.resources, self.namespaces, self.note = name, None, [], None
        self.ns_shown = namespaces if namespaces is not None else self.ns_by_context.get(name)
        self.ns_shown = None if self.ns_shown is None else list(self.ns_shown)
        self.ns_by_context[name] = self.ns_shown and list(self.ns_shown)
        self.fill_namespaces()
        if self.host.here:
            save_settings(kube_context=name)
        else:
            self.last_context[self.host.key()] = name
        self.say(wording.say("kube.connecting", context=name))
        self.hide_details()
        self.dock.close_all()  # (another cluster's: not its pods')
        self.stop_watch()  # (the other cluster's)
        self.fade.hold()
        self.loading = True  # (no refresh meanwhile: it would be read before the context's resources)
        gen = self.generation = self.generation + 1
        if not self.host.here:
            def connected(r):
                if r.get("missing"):
                    return gen == self.generation and self.show_missing(r["missing"])
                if r.get("error") or r.get("fault"):
                    fault = r.get("fault") or {"title": r["error"], "why": "", "fix": None, "said": ""}
                    return self._failed(gen, fault, fault.get("said") or "")
                served = {p["resource"] for s in r.get("sections") or [] for p in s.get("pages") or [] if p.get("served")}
                self._connected(gen, name, served, r.get("namespaces") or [], r.get("nodes") or 0)
            self.host.ask("kube", {"what": "connect", "context": name}, connected)
            return

        def work():
            try:
                resources = kube_api_resources(name)
            except RuntimeError as e:
                said = getattr(e, "said", str(e))
                GLib.idle_add(self._failed, gen, kube_diagnose(said, kube_signin(name)), said)
                return
            try:
                spaces = sorted(_km(n).get("name", "") for n in json.loads(kube_get(name, "namespaces", "-o", "json")
                                                                           or "{}").get("items") or [])
            except (RuntimeError, ValueError):
                spaces = []
            try:  # (names only: a big cluster's nodes, as JSON, list every image they hold)
                nodes = len(kube_get(name, "nodes", "-o", "name").split())
            except RuntimeError:  # (not allowed to list them: no Nodes page to show either)
                nodes = 0
            GLib.idle_add(self._connected, gen, name, resources, spaces, nodes)
        KUBE_POOL.submit(work)

    def _failed(self, gen, diag, said=""):
        if gen == self.generation:
            self.loading = False
            self.refresh_btn.set_loading(False)
            self.stop_watch()
            self.show_error(diag, said)
            if self.store is not None:
                self.store.remove_all()
            self.fade.done()
        return False

    def _connected(self, gen, name, resources, spaces, nodes):
        """A cluster's resources, namespaces and nodes, as switch_context read them. Kept whatever was picked while they
        came (a kind, a namespace: each a newer load), so long as it's still this cluster: else the sidebar listed the
        other cluster's custom resources."""
        if name != self.context:
            return False
        self.resources, self.namespaces, self.node_count = resources, [s for s in spaces if s], nodes
        self.fill_namespaces()
        for row in self.page_rows.values():
            api = row.page["api"]
            served = row.page["source"] == "summary" or (  # (Home: any cluster's)
                row.page["resource"] in resources if not self.host.here else
                not api or api in resources or row.page["resource"] in resources)  # (another host's: as it said)
            if row.page.get("known"):  # (KEDA's, cert-manager's…: listed only on a cluster that has them)
                row.served = served
                row.set_visible(served and row.section not in self.folded)
            else:  # (kinds this cluster doesn't serve: dimmed)
                row.set_sensitive(served)
                row.set_tooltip_text(None if served else wording.say("kube.not_served", context=self.context,
                                                                     kind=row.page['title']))
        self.show_heads()
        unserved = not getattr(self.page_rows.get(id(self.page)), "served", True)
        if unserved:
            self.page = KUBE_PODS  # (scaled objects shown, on a cluster without KEDA: its pods instead)
            self.select_page_row(KUBE_PODS)
        self.update_nodes_row()
        if unserved or gen == self.generation:  # (a page picked meanwhile is being read already)
            self.load()
        return False

    def update_nodes_row(self):
        """Nodes, in the sidebar, and the Node column, only for a cluster of more than one node: a local cluster's
        one node is no news, and every pod would say the same. The page stays while it's the one shown (opened from
        a link, say)."""
        self.page_rows[id(KUBE_NODES)].set_visible(self.node_count > 1 or self.page is KUBE_NODES)
        for c in getattr(self, "columns", None) or []:
            if c.get_title() == "Node":
                c.set_visible(self.node_count > 1)

    def on_side(self, _box, row):
        if row is None or self.syncing or not hasattr(row, "page"):
            return
        if row.page is not self.page:
            self.page = row.page
            self.hide_details()
            self.update_nodes_row()
            if self.context:
                self.load(switch=True)

    def select_page_row(self, page):
        row = self.page_rows.get(id(page))
        self.update_nodes_row()
        if row is not None:
            self.syncing = True
            self.side.select_row(row)
            self.syncing = False

    def fill_namespaces(self):
        """The namespace list: the cluster's namespaces, and any shown that it didn't list (one opened from a link),
        each a check; those shown checked, and named on the button."""
        self.ns_pick.fill([(ns, ns) for ns in sorted(set(self.namespaces) | set(self.ns_shown or ()))], self.ns_shown)
        self.ns_checks = self.ns_pick.checks
        self.sync_namespaces()

    def sync_namespaces(self):
        self.ns_pick.sync(self.ns_shown)
        self.ns_label.set_label(self.ns_summary())
        self.ns_btn.set_tooltip_text(", ".join(self.ns_shown) if len(self.ns_shown or ()) > 2 else
                                     wording.say("kube.namespaces_tip"))

    def ns_summary(self):
        """The namespaces shown, in a few words: All namespaces; shop; shop, jobs; 3 namespaces; 0 namespaces."""
        n = self.ns_shown
        return wording.say("kube.all_namespaces") if n is None else ", ".join(n) if 0 < len(n) <= 2 else \
            wording.say("kube.n_namespaces", n=len(n))

    def show_namespaces(self, names):
        """Show these namespaces' objects (in the order picked; [] for none), or every namespace's (None): picked from
        the list, a pod's namespace clicked. The list is every namespace's already: only the table fades, to be
        filtered."""
        self.ns_shown = None if names is None else list(dict.fromkeys(names))
        if self.context:
            self.ns_by_context[self.context] = self.ns_shown and list(self.ns_shown)
        if any(ns not in self.ns_checks for ns in self.ns_shown or ()):
            self.fill_namespaces()
        else:
            self.sync_namespaces()
        if self.page is KUBE_HOME:  # (its totals are the host's to add up again, for these namespaces)
            self.load(switch=True)
            return
        self.fade.swap(self.refilter)

    def set_namespace(self, ns):
        """Show only ns's objects (a pod's namespace clicked, a namespace opened); None: every namespace's."""
        if self.page["namespaced"]:
            self.show_namespaces([ns] if ns else None)
            return
        self.ns_shown = [ns] if ns else None  # (from the Namespaces page: its pods)
        if self.context:
            self.ns_by_context[self.context] = self.ns_shown and list(self.ns_shown)
        self.fill_namespaces()
        self.page = KUBE_PODS
        self.select_page_row(KUBE_PODS)
        self.load(switch=True)

    def go_to_owner(self, kind, name, ns):
        """Show what controls an object (a pod's Controlled By, clicked): a ReplicaSet's Deployment, a Job's CronJob,
        when it has one; its page, with it selected (its YAML beside the table)."""
        context = self.context
        self.say(wording.say("kube.finding", kind=kind, name=name))
        if not self.host.here:
            return self.host.ask("kube", {"what": "owner", "context": context, "kind": kind, "name": name,
                                          "namespace": ns or None},
                                 lambda r: self._go_to(context, r.get("kind") or kind, r.get("name") or name, ns))

        def work():
            k, n = kind, name
            for _ in range(3):  # (ReplicaSet → Deployment; Job → CronJob)
                if k not in KUBE_CLIMB:
                    break
                try:
                    up = kube_controller(json.loads(kube_get(context, KUBE_CLIMB[k], n, "-n", ns, "-o", "json")))
                except (RuntimeError, ValueError):
                    break
                if not up or not up.get("kind") or not up.get("name"):
                    break
                k, n = up["kind"], up["name"]
            GLib.idle_add(self._go_to, context, k, n, ns)
        KUBE_POOL.submit(work)

    def _go_to(self, context, kind, name, ns):
        if context != self.context:
            return False
        resource = KUBE_CLIMB.get(kind) or kube_resource(kind)
        row = next((r for r in self.page_rows.values() if r.page["resource"].split(".")[0] == resource and r.served),
                   None)
        if row is None:
            self.update_status()
            self.set_note(wording.say("kube.no_page", kind=kind, name=name))
            return False
        if ns and row.page["namespaced"] and self.ns_shown is not None and ns not in self.ns_shown:
            self.show_namespaces(self.ns_shown + [ns])
        self.search.set_text("")
        self.pending_pick = (name, ns if row.page["namespaced"] else "")
        self.page = row.page
        self.select_page_row(row.page)
        self.hide_details()
        self.load(switch=True)
        return False

    # ---- reading a page
    def load(self, quiet=False, switch=False):
        """Read the page's objects (and, for pods and nodes, their CPU and memory) in the background. switch: it's
        another kind, so the table fades until they're in (a refresh doesn't)."""
        if not self.context:
            return
        self.stop_watch()  # (read again: watched again from what's read)
        if switch:
            self.fade.hold()
        page, context = self.page, self.context
        gen = self.generation = self.generation + 1
        self.loading = True
        if not quiet:
            self.say(wording.say("kube.reading", kind=page['title'].lower(), context=context))
            self.refresh_btn.set_loading(True)
        if page is KUBE_HOME:
            return self.read_summary(gen, context)
        if not self.host.here:
            return self.host.ask("kube", {"what": "list", "context": context, **self.page_ref(page)},
                                 lambda r: self._listed(gen, page, r))

        def work():
            # (a custom resource's definition, read alongside the list: two kubectl round trips at once, not after)
            crd = threading.Thread(target=kube_page_crd, args=(context, page), daemon=True) if page.get("crd") else None
            if crd is not None:
                crd.start()
            try:
                items = kube_list(context, page)
            except (RuntimeError, ValueError) as e:
                said = getattr(e, "said", str(e))
                GLib.idle_add(self._failed, gen, kube_diagnose(said, kube_signin(context)), said)
                return
            if crd is not None:
                crd.join()
            columns = kube_page_columns(context, page, items=items)  # (its definition's, on this cluster)
            ctx = kube_metrics_ctx(context, page)
            GLib.idle_add(self._loaded, gen, page, columns, kube_rows(page, items, ctx, columns), ctx, items)
        KUBE_POOL.submit(work)

    def read_summary(self, gen, context):
        """Home's totals, for the namespaces shown, as the host adds them up (hostkube's summary: there, or here as a
        host process would)."""
        msg = {"what": "summary", "context": context}
        if self.ns_shown is not None:
            msg["namespaces"] = list(self.ns_shown)
        if not self.host.here:
            return self.host.ask("kube", msg, lambda r: self._summarized(gen, r))
        KUBE_POOL.submit(lambda: GLib.idle_add(self._summarized, gen, hostkube.answer(None, msg)))

    def _summarized(self, gen, r):
        if gen != self.generation:
            return False
        if r.get("missing"):
            return self.show_missing(r["missing"]) and False
        if r.get("error") or r.get("fault"):
            fault = r.get("fault") or {"title": r["error"], "why": "", "fix": None, "said": ""}
            return self._failed(gen, fault, fault.get("said") or "")
        self.loading = False
        self.refresh_btn.set_loading(False)
        summary = r.get("summary") or {}
        self.home.show(summary)
        self.table_stack.set_visible_child_name("home")
        self.metrics_note = wording.say("kube.metrics", seconds=KUBE_REFRESH_S) if summary.get("metrics") else \
            wording.say("kube.no_metrics")
        self.update_status()
        self.fade.done()
        return False

    @staticmethod
    def page_ref(page):
        """How another host's kube op knows a page: by its resource, or a custom resource's by its definition."""
        return {"crd": page["host_crd"]} if page.get("host_crd") else {"page": page["resource"]}

    def _listed(self, gen, page, r):
        """A page's objects, as another host read them: its rows made already, and whether its metrics were read."""
        if gen != self.generation:
            return
        if r.get("missing"):
            return self.show_missing(r["missing"])
        if r.get("error") or r.get("fault"):
            fault = r.get("fault") or {"title": r["error"], "why": "", "fix": None, "said": ""}
            return self._failed(gen, fault, fault.get("said") or "")
        if page.get("host_crd") and r.get("page"):  # (a custom resource's, as its definition names it there)
            page.update(title=r["page"].get("title") or page["title"], namespaced=bool(r["page"].get("namespaced")))
            if (row := self.page_rows.get(id(page))) is not None:
                row.get_child().get_last_child().set_label(page["title"])
        columns = kubehost.columns(r.get("columns"))
        if self.table_page is page and self.table_cols == columns:  # (the same: the table kept, scrolled as it was)
            columns = self.table_cols
        self._loaded(gen, page, columns, kubehost.rows(r.get("rows")), self.host_ctx(r.get("metrics")))

    @staticmethod
    def host_ctx(metrics):
        """What the view knows of a list's metrics read on another host: whether there were any."""
        read = {} if metrics else None
        return {"now": time.time(), "metrics": read, "node_metrics": read}

    # ---- kept up to date, as Freelens keeps its lists: one read, then the changes as they happen
    def watch_page(self, gen, page):
        """Watch the page's kind (not Secrets: their values would come with them; nor Helm's releases): each change
        lands in the table within a second of its happening. The list is read every KUBE_REFRESH_S only if the
        cluster can't be watched."""
        if page["source"] != "kubectl" or gen != self.generation or not self.get_visible():
            return
        if not self.host.here:  # (watched there: its rows sent whole as they change, its metrics read there)
            self.watch = kubehost.Follow(self.host.where, {"what": "watch", "context": self.context,
                                                           **self.page_ref(page)},
                                         lambda w, code, said: self._watch_ended(w, gen, page, code, said),
                                         on_rows=lambda w, r: self._host_rows(w, gen, page, r))
            return
        args = ["--context", self.context, "get", page["resource"]] + (["--all-namespaces"] if page["namespaced"]
                                                                        else [])
        self.watch = KubeWatch(args + ["--watch", "--output-watch-events", "-o", "json"],
                               lambda w, events: self._watched(w, gen, page, events),
                               lambda w, code, said: self._watch_ended(w, gen, page, code, said))

    def stop_watch(self):
        if self.watch is not None:
            self.watch.stop()
            self.watch = None

    def _watched(self, w, gen, page, events):
        """Changes, a second's worth: each object put in, replaced or taken out by its uid; one that's the same version
        as the one shown is skipped (the watch starts by listing what's there), as Freelens' updateFromEventsBuffer."""
        if w is not self.watch or gen != self.generation:
            return
        changed = False
        for event in events:
            o, kind = event.get("object") or {}, event.get("type")
            if kind not in ("ADDED", "MODIFIED", "DELETED") or not isinstance(o, dict):
                continue
            key = kube_uid(o)
            have = self.objects.get(key)
            if kind == "DELETED":
                changed |= self.objects.pop(key, None) is not None
            elif have is None or (_km(o).get("resourceVersion") != _km(have).get("resourceVersion")
                                  if _km(o).get("resourceVersion") else o != have):
                self.objects[key], changed = o, True
        self.watch_fails = 0
        if changed:
            self.show_rows(page, kube_rows(page, list(self.objects.values()), dict(self.ctx, now=time.time()),
                                           self.table_cols))
            self.update_status()

    def _host_rows(self, w, gen, page, r):
        """A watched list's rows as another host sent them, as it changed (with why it's read, not watched, if so)."""
        if w is not self.watch or gen != self.generation:
            return
        self.watch_fails = 0
        self.show_rows(page, kubehost.rows(r.get("rows")))
        self.ctx = self.host_ctx(r.get("metrics"))
        if r.get("note"):
            self.set_note(r["note"])
        self.update_status()

    def _watch_ended(self, w, gen, page, code, said):
        """The watch ended (the API server ends them after a while; or it failed): watched again after a second, or
        after 5 s if it failed (Freelens' retry); after three failures in a row, read every KUBE_REFRESH_S instead."""
        if w is not self.watch:
            return
        self.watch = None
        if gen != self.generation or not self.get_visible():
            return
        if code and not w.seen:
            self.watch_fails += 1
            if self.watch_fails >= 3:
                why = kube_diagnose(said)["title"].lower() if said else wording.say("kube.kubectl_exited", code=code)
                self.set_note(wording.say("kube.cant_watch", kind=page['title'].lower(), why=why,
                                          seconds=KUBE_REFRESH_S))
                return
        GLib.timeout_add(5000 if code and not w.seen else 1000,
                         lambda: self.watch is None and self.watch_page(gen, page) and False)

    def refresh_metrics(self):
        """While the list is watched: only the pods' or nodes' CPU and memory read again (and the ages moved on)."""
        page, context, gen = self.table_page, self.context, self.generation

        def work():
            ctx = kube_metrics_ctx(context, page)
            GLib.idle_add(lambda: gen == self.generation and self.watch is not None and
                          (setattr(self, "ctx", ctx), self.show_rows(page, kube_rows(page, list(self.objects.values()),
                                                                                       ctx, self.table_cols)),
                           self.update_status())
                          and False)
        KUBE_POOL.submit(work)

    def show_rows(self, page, rows):
        """The table's rows replaced, the object selected before selected again (or the one an owner link asked for)."""
        picked, owner = self.pending_pick or self.selected_obj(), self.pending_pick
        self.pending_pick = None
        self.store.splice(0, self.store.get_n_items(), [KubeRow(t, k, o) for t, k, o in rows])
        if picked is not None:  # (the same object, selected again after a refresh; or the owner clicked)
            for i in range(self.selection.get_n_items()):
                o = self.selection.get_item(i).obj
                if (o["name"], o["namespace"]) == picked:
                    self.selection.set_selected(i)
                    if owner:
                        self.table.scroll_to(i, None, Gtk.ListScrollFlags.NONE, None)
                    break
            else:
                if owner:
                    self.set_note(wording.say("kube.owner_missing", owner=owner[0], kind=page['title'].lower(),
                                              context=self.context))

    def _loaded(self, gen, page, columns, rows, ctx, items=()):
        if gen != self.generation:
            return False
        self.loading = False
        self.refresh_btn.set_loading(False)
        self.table_stack.set_visible_child_name("table")
        if self.table_page is not page or self.table_cols is not columns:  # (or another cluster's printer columns)
            self.build_table(page, columns)
        self.objects, self.ctx, self.watch_fails = {kube_uid(o): o for o in items}, ctx, 0
        self.show_rows(page, rows)
        self.watch_page(gen, page)
        if page is KUBE_PODS or page["resource"] == "nodes":
            got = ctx["metrics"] if page is KUBE_PODS else ctx["node_metrics"]
            self.metrics_note = wording.say("kube.metrics", seconds=KUBE_REFRESH_S) if got is not None \
                else wording.say("kube.no_metrics")
        else:
            self.metrics_note = ""
        self.update_status()
        self.fade.done()
        return False

    def build_table(self, page, cols):
        """The table for a page, with these columns (its own; a custom resource's, as the cluster declares them)."""
        self.store = Gio.ListStore(item_type=KubeRow)
        self.filter = Gtk.CustomFilter.new(self.row_visible, None)
        cv = Gtk.ColumnView(show_row_separators=False, show_column_separators=False)
        cv.add_css_class("data-table")
        cv.add_css_class("kube-table")
        columns = [self.make_column(i, col) for i, col in enumerate(cols)]
        for c in columns:
            cv.append_column(c)
        if page is KUBE_PODS:
            cv.append_column(self.make_pod_buttons())
            header = cv.get_first_child()  # (its heading has nothing to say or sort by: not a button to press)
            last = header.get_last_child() if header is not None and header.get_css_name() == "header" else None
            if last is not None:
                last.set_can_target(False)
        sorted_model = Gtk.SortListModel(model=Gtk.FilterListModel(model=self.store, filter=self.filter),
                                         sorter=cv.get_sorter())
        self.selection = Gtk.SingleSelection(model=sorted_model, autoselect=False, can_unselect=True)
        self.selection.set_selected(Gtk.INVALID_LIST_POSITION)
        self.selection.connect("notify::selected-item", self.on_select)
        cv.set_model(self.selection)
        cv.connect("activate", self.on_activate)
        first = next((c for c, col in zip(columns, cols) if col["kind"] == "name"), columns[0])
        if page["resource"] == "events":  # (the latest first)
            first = next(c for c in columns if c.get_title() == "Last Seen")
        cv.sort_by_column(first, Gtk.SortType.ASCENDING)
        self.table, self.table_page, self.columns, self.table_cols = cv, page, columns, cols
        self.table_scroll.set_child(cv)
        self.update_nodes_row()

    def make_column(self, i, col):
        tones = ("kube-ok", "kube-warn", "kube-bad")
        f = Gtk.SignalListItemFactory()

        def setup(_f, item):
            if col["kind"] == "namespace":  # (a namespace is a link: only its objects)
                btn = Gtk.Button(halign=Gtk.Align.START, child=Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END))
                btn.add_css_class("flat")
                btn.add_css_class("kube-link")
                btn.ns = None
                btn.connect("clicked", lambda b: b.ns and self.set_namespace(b.ns))
                item.set_child(btn)
            elif col["kind"] == "owner":  # (an owner is a link: it, or what controls it in turn)
                btn = Gtk.Button(halign=Gtk.Align.START, child=Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END))
                btn.add_css_class("flat")
                btn.add_css_class("kube-link")
                btn.owner = btn.ns = None
                btn.connect("clicked", lambda b: b.owner and self.go_to_owner(*b.owner))
                item.set_child(btn)
            elif col["kind"] == "containers":
                item.set_child(KubeSquares())
            else:
                item.set_child(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                         max_width_chars=80 if col["kind"] == "name" else 48))

        def bind(_f, item):
            text, w = item.get_item().texts[i], item.get_child()
            if col["kind"] == "owner":
                obj = item.get_item().obj
                ref = (obj["owner"] if obj["raw"] is None else kube_controller(obj["raw"])) or {}
                self.find_label(w.get_child(), text)
                w.owner = (ref["kind"], ref["name"], obj["namespace"]) if ref.get("kind") and ref.get("name") else None
                return
            if isinstance(w, Gtk.Button):
                self.find_label(w.get_child(), text)
                w.ns = text
                w.set_tooltip_text(wording.say("kube.only_namespace", namespace=text) if text else None)
                return
            if isinstance(w, KubeSquares):
                w.set_containers(self.pod_look(item.get_item().obj)[0])
                return
            self.find_label(w, text)
            w.set_tooltip_text(text if len(text) > (30 if col["kind"] == "name" else 40) else None)
            if col["kind"] == "age":  # (what it sorts by: how old, when the row was made)
                age = item.get_item().keys[i]
                time_tip(w, item.get_item().obj["now"] - age if isinstance(age, (int, float)) else None)
            if col["kind"] == "status":
                for t in tones:
                    w.remove_css_class(t)
                obj = item.get_item().obj
                if tone := (obj["tones"][i] if obj["raw"] is None and i < len(obj["tones"]) else
                            None if obj["raw"] is None else kube_cell_tone(col, text)):
                    w.add_css_class(f"kube-{tone}")
        f.connect("setup", setup)
        f.connect("bind", bind)
        c = Gtk.ColumnViewColumn(title=col["title"], factory=f, resizable=True,
                                 expand=col["kind"] == "name" or col["title"] in ("Message", "Rules"))
        # (fixed: the cells that matter most aren't squeezed, and the table scrolls sideways. A name and a namespace
        # no wider than this: the Pods table's Restarts is still in sight beside the YAML in a laptop's window)
        if width := {"name": tokens.px("kube.name-width"), "namespace": tokens.px("kube.namespace-width")}.get(col["kind"]) or {"Message": 420, "Rules": 320,
                                                                        "Involved Object": 260}.get(col["title"]):
            c.set_fixed_width(width)
        c.set_sorter(Gtk.CustomSorter.new(lambda a, b, _d: _KUBE_ORDER[kube_order(a.keys[i], b.keys[i])], None))
        return c

    def make_pod_buttons(self):
        """The Pods table's last column, as Freelens ends a pod's row: its logs (four lines of text), a shell in it, and
        ⋮, a menu to delete it or force-delete it. Each is the view's action with the pod as its target ("namespace/name")."""
        f = Gtk.SignalListItemFactory()

        def setup(_f, item):
            box = Gtk.Box(spacing=2, halign=Gtk.Align.END)
            logs = Gtk.Button(icon_name="baude-logs-symbolic", tooltip_text=wording.say("kube.logs"),
                              action_name="kube.logs")
            shell = Gtk.Button(icon_name="utilities-terminal-symbolic", tooltip_text=wording.say("kube.shell"),
                               action_name="kube.shell")
            more = Gtk.MenuButton(icon_name="view-more-symbolic", tooltip_text=wording.say("common.delete_ellipsis"))
            for b in (logs, shell, more):
                b.add_css_class("flat")
                b.add_css_class("kube-row-btn")
                box.append(b)
            item.set_child(box)

        def bind(_f, item):
            obj, box = item.get_item().obj, item.get_child()
            key = GLib.Variant("s", f"{obj['namespace']}/{obj['name']}")
            logs, more = box.get_first_child(), box.get_last_child()
            logs.set_action_target_value(key)
            logs.get_next_sibling().set_action_target_value(key)
            menu = Gio.Menu()
            for label, action in ((wording.say("common.delete"), "kube.delete"),
                                  (wording.say("kube.force_delete"), "kube.force-delete")):
                entry = Gio.MenuItem.new(label, None)
                entry.set_action_and_target_value(action, key)
                menu.append_item(entry)
            more.set_menu_model(menu)
        f.connect("setup", setup)
        f.connect("bind", bind)
        return Gtk.ColumnViewColumn(title="", factory=f, resizable=False)

    def row_visible(self, row, _data=None):
        if self.ns_shown is not None and self.table_page is not None and self.table_page["namespaced"] and \
                row.obj["namespace"] not in self.ns_shown:
            return False
        text = self.search.get_text().strip().lower()
        return not text or any(text in t.lower() for t in row.texts)

    def refilter(self):
        self.home.refilter(self.search.get_text())
        if self.filter is not None:
            self.filter.changed(Gtk.FilterChange.DIFFERENT)
        q = self.search.get_text().strip()
        for label, text in list(self.found.items()):  # (the cells already shown: what's typed highlighted in them)
            find_label(label, text, q)
        self.update_status()

    def find_label(self, label, text):
        """A cell's text, with the filter's text highlighted in it; kept, to highlight again as the filter changes."""
        self.found[label] = text
        find_label(label, text, self.search.get_text().strip())

    def update_status(self):
        if self.page is KUBE_HOME:  # (a fresh note, and the metrics note)
            if self.table_stack.get_visible_child_name() == "home":
                bits = [self.note and self.note[1] > time.monotonic() and self.note[0], self.metrics_note]
                self.say(" · ".join(b for b in bits if b))
            return
        if self.selection is None:
            return
        n, total = self.selection.get_n_items(), self.store.get_n_items()
        what = self.table_page["title"].lower()
        shown = wording.say("kube.count_of", n=n, what=what, total=total) if n != total else \
            wording.say("kube.count", n=n, what=what)
        if self.ns_shown is not None and self.table_page["namespaced"]:
            shown = wording.say("kube.in_namespaces", shown=shown, namespaces=self.ns_summary())
        bits = [self.note and self.note[1] > time.monotonic() and self.note[0], shown, self.metrics_note]
        self.say(" · ".join(b for b in bits if b))

    # ---- an object: its YAML; a namespace or a custom resource's definition, opened
    def selected_obj(self):
        item = self.selection.get_selected_item() if self.selection is not None else None
        return (item.obj["name"], item.obj["namespace"]) if item is not None else None

    def on_select(self, sel, _pspec):
        item = sel.get_selected_item()
        if item is None:
            return
        page, obj, context = self.table_page, item.obj, self.context
        kind = page["title"][:-1] if page["title"].endswith("s") else page["title"]
        self.details_title.set_label(f"{kind} {obj['name']}" + (f" · {obj['namespace']}" if obj["namespace"] else ""))
        self.details_box.set_visible(True)
        if self.right.get_position() < 200 or self.right.get_position() > self.right.get_width() - 200:
            # (the table keeps two thirds: the Pods table's Restarts stays in sight in a 1280-wide window)
            self.right.set_position(max(320, int(self.right.get_width() * 0.65)))
        if not self.host.here:  # (read there, as it's listed there: a Secret's keys' names only, too)
            return self.host_yaml(page, obj, context)
        if page["source"] in ("secrets", "helm"):
            self.details_shown = None  # (shown from what's listed: nothing read)
        if page["source"] == "secrets":  # (only its keys' names: its values are never read)
            self.details.load(kube_secret_yaml(obj["namespace"], obj["name"], obj["raw"].get("type", ""),
                                               obj["raw"].get("keys") or []), "secret.yaml")
            return
        if page["source"] == "helm":
            self.details.load(json.dumps(obj["raw"], indent=2), "release.json")
            return
        shown = ((context, page["resource"], obj["name"], obj["namespace"]), _km(obj["raw"]).get("resourceVersion"))
        if shown == self.details_shown:  # (the table read again, this object no different: its YAML stays)
            return
        again = self.details_shown is not None and self.details_shown[0] == shown[0]
        self.details_shown = shown
        self.details_fade.hold()  # (what's shown goes grey until the new YAML is in: no flash of nothing between)
        args = [page["resource"], obj["name"], "-o", "yaml"] + (["-n", obj["namespace"]] if obj["namespace"] else [])

        def work():
            try:
                text = kube_get(context, *args)
            except RuntimeError as e:
                d = kube_diagnose(getattr(e, "said", str(e)))
                text = f"# {d['title']}: {d['why']}\n"
            GLib.idle_add(self._details_read, shown, text, again)
        KUBE_POOL.submit(work)

    def host_yaml(self, page, obj, context):
        """An object's YAML read on another host (its kube op's yaml), shown as one read here is."""
        shown = ((context, page["resource"], obj["name"], obj["namespace"]), obj["version"])
        if shown == self.details_shown and page["source"] == "kubectl":
            return
        again = self.details_shown is not None and self.details_shown[0] == shown[0]
        self.details_shown = shown
        self.details_fade.hold()
        self.host.ask("kube", {"what": "yaml", "context": context, "namespace": obj["namespace"] or None,
                               "name": obj["name"], **self.page_ref(page)},
                      lambda r: self._details_read(shown, r.get("text") or f"# {r.get('error') or ''}\n", again,
                                                   r.get("file") or "object.yaml"))

    def _details_read(self, shown, text, again, file="object.yaml"):
        """An object's YAML is in: shown (where it was scrolled to, if it's the same object read again), with the
        names of other objects in it as links."""
        if shown != self.details_shown:
            return False
        adj = self.details.scroll.get_vadjustment()
        at = adj.get_value() if again else 0
        self.details.load(text, file)
        ns = shown[0][3] or None
        if file == "object.yaml":
            self.details.set_links(kube_yaml_refs(text, ns), self.open_ref)
        if at:
            GLib.idle_add(lambda: adj.set_value(min(at, adj.get_upper() - adj.get_page_size())) and False)
        self.details_fade.done()
        return False

    def open_ref(self, target):
        """A name in an object's YAML, clicked: that object (its page, it selected); a namespace, its objects."""
        kind, name, ns = target
        if kind == "Namespace":
            self.set_namespace(name)
        else:
            self._go_to(self.context, kind, name, ns)

    def hide_details(self):
        self.details_box.set_visible(False)
        self.details_shown = None
        if self.selection is not None:
            self.selection.set_selected(Gtk.INVALID_LIST_POSITION)

    def on_activate(self, _cv, position):
        row = self.selection.get_item(position)
        if row is None:
            return
        if self.table_page["resource"] == "namespaces":  # (a namespace: its pods)
            self.set_namespace(row.obj["name"])
        elif self.table_page["resource"] == "customresourcedefinitions":  # (a definition: its objects)
            page = kube_custom_page(row.obj["raw"]) if self.host.here else \
                kubehost.page({"title": row.obj["name"], "resource": row.obj["name"], "crd": row.obj["name"]})
            key = page["resource"]
            existing = next((r for r in self.page_rows.values() if r.page["resource"] == key), None)
            if existing is None:
                existing = self.add_page_row(page, after=self.page_rows[id(self.table_page)])
            self.page = existing.page
            self.select_page_row(existing.page)
            self.hide_details()
            self.load(switch=True)

    def pod_obj(self, key):
        """The pod "namespace/name" as the Pods table last listed it, or None."""
        ns, _, name = key.partition("/")
        if self.store is None or self.table_page is not KUBE_PODS:
            return None
        for i in range(self.store.get_n_items()):
            o = self.store.get_item(i).obj
            if (o["namespace"], o["name"]) == (ns, name):
                return o
        return None

    def set_note(self, text, seconds=30):
        """Say text first in the status line for a while, through the refreshes meanwhile (what a delete did)."""
        self.note = (text, time.monotonic() + seconds)
        if self.selection is not None:
            self.update_status()
        else:
            self.say(text)

    # ---- pods' logs, followed, and shells in pods: a tab each in the dock (kubedock)
    def pod_look(self, obj):
        """A pod's containers, as kube_containers has them ([(name, look, words, init)]), and its default container."""
        if obj["raw"] is None:  # (read on another host: as it said them)
            return obj["containers"], obj["default"]
        return kube_containers(obj["raw"], time.time()), \
            (_km(obj["raw"]).get("annotations") or {}).get("kubectl.kubernetes.io/default-container")

    def show_logs(self, key):
        """A pod's logs in a tab of the dock (its tab again, if it has one): its default container's (the one its
        kubectl.kubernetes.io/default-container annotation names, else its first), from its last KUBE_LOG_TAIL lines
        on, followed."""
        obj = self.pod_obj(key)
        if obj is None:
            return
        containers, default = self.pod_look(obj)
        pod_key = (self.context, obj["namespace"], obj["name"])
        if (tab := self.dock.find(LogsTab, pod_key)) is not None:
            return self.dock.show(tab)
        cs = [(name, init) for name, _l, _w, init in containers]
        if not cs:
            self.set_note(wording.say("kube.no_containers", pod=obj['name']))
            return
        names = [n for n, _i in cs]
        pick = names.index(default) if default in names else next((i for i, (_n, init) in enumerate(cs) if not init), 0)
        self.show_dock()
        self.dock.add(LogsTab(self.dock, self.host, *pod_key, cs, pick))

    def show_shell(self, key):
        """A shell in a pod in a new tab of the dock, in its default container (as its logs pick one), of its running
        ones (an init container's has ended)."""
        obj = self.pod_obj(key)
        if obj is None:
            return
        containers, default = self.pod_look(obj)
        names = [name for name, _l, _w, init in containers if not init]
        if not names:
            self.set_note(wording.say("kube.no_containers", pod=obj['name']))
            return
        self.show_dock()
        self.dock.add(ShellTab(self.dock, self.host, self.context, obj["namespace"], obj["name"], names,
                               names.index(default) if default in names else 0))

    def hide_logs(self):
        """Every log's tab closed: kubectl, following them, is stopped (never left running)."""
        self.dock.close_all(LogsTab)

    def hide_shell(self):
        """Every shell's tab closed: kubectl, and the shell in the pod with it, ended (never left running)."""
        self.dock.close_all(ShellTab)

    def show_dock(self):
        """The dock's place under the table, half the view's height as it opens."""
        if not self.dock.get_visible():
            self.dock.set_visible(True)
            self.dock_split.set_position(max(160, self.dock_split.get_height() // 2))

    @property
    def logs(self):
        """The log shown (the picked tab's, if it's a log's), or None."""
        tab = self.dock.current()
        return tab.logs if isinstance(tab, LogsTab) else None

    @property
    def shell_term(self):
        """The terminal shown (the picked tab's, if it's a shell's), or None."""
        tab = self.dock.current()
        return tab.term if isinstance(tab, ShellTab) else None

    # ---- deleting a pod: the view's only writes, each confirmed first
    def ask_delete(self, key, force):
        """Ask before deleting a pod (force: at once, not waiting for it to stop), naming it, its namespace and its
        context, and saying plainly when that's a remote cluster (its API server isn't on this machine). Nothing runs
        unless you say Delete."""
        ns, _, name = key.partition("/")
        context = self.context
        picked = next((c for c in self.contexts if c["name"] == context), {})
        obj = self.pod_obj(key)
        if obj is not None and obj["raw"] is None:  # (read on another host: its controller, as it said it)
            owners = [obj["owner"]] if obj["owner"] else []
        else:
            owners = (_km(obj["raw"]).get("ownerReferences") or []) if obj else []
        body = [wording.say("kube.in_namespace", namespace=ns, context=context)]
        if not (picked.get("local") if "local" in picked else kube_local(picked.get("server"))):
            body.append(wording.say("kube.remote_cluster", context=context))
        if force:
            body.append(wording.say("kube.force_delete_body"))
        else:
            body.append(wording.say("kube.delete_body"))
        body.append(wording.say("kube.controlled_by", kind=owners[0].get('kind'), name=owners[0].get('name'))
                    if owners else wording.say("kube.no_controller"))
        dlg = Adw.AlertDialog(heading=wording.say("kube.force_delete_heading", name=name) if force else
                              wording.say("kube.delete_heading", name=name), body="\n\n".join(body),
                              default_response="cancel", close_response="cancel")
        dlg.add_response("cancel", wording.say("common.cancel"))
        dlg.add_response("delete", wording.say("kube.force_delete_response") if force else wording.say("common.delete"))
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: r == "delete" and self.delete_pod(context, ns, name, force))
        self.dialog = dlg
        dlg.present(self.win)

    def delete_pod(self, context, ns, name, force):
        """Delete it (confirmed), off the main thread: what kubectl said goes in the status line, and the list is
        read again."""
        self.set_note(wording.say("kube.force_deleting", name=name) if force else
                      wording.say("kube.deleting", name=name))
        if not self.host.here:
            return self.host.ask("kube", {"what": "delete", "context": context, "namespace": ns, "name": name,
                                          "force": force},
                                 lambda r: self._deleted(context, r.get("said") or r.get("error") or ""))

        def work():
            GLib.idle_add(self._deleted, context, kube_delete(context, ns, name, force))
        KUBE_POOL.submit(work)

    def _deleted(self, context, said):
        if context == self.context:
            self.set_note(said)
            self.load(quiet=True)
        return False
