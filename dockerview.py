"""The Docker view (DockerView: ☰ → Docker, Baude's and Record's), laid out as the Kubernetes view is: its categories
in the sidebar (Containers, running and all; Images; Volumes; Networks; Compose Projects), a category's objects in a
table, an object inspected beside it (JSON or YAML), its log followed and a shell in a container in the dock under
them, and a container or Compose project started, stopped, restarted or removed (a remove asked first). It's a thin
client: everything it shows and does is asked of the host picked in the window's host dropdown (this machine's own
too), through the wire's docker and docker_follow ops (baudelib/hostdocker.py); a shell is a terminal run there
(the terminal op), its command the host's. Its window is a host (what it asks of one: input, to focus when it
closes)."""
import threading
import time
import weakref

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from widgets import (  # noqa: E402
    ensure_icons, find_label, inside, insert_actions, load_settings, paned_grip, save_settings, time_tip, toast,
    view_bar, _scales,
)
from imageviews import Fade, RefreshButton  # noqa: E402
from pickers import ellipsize_dropdown  # noqa: E402
from codeview import CodeView  # noqa: E402
from baudelib import tokens, wire, wording  # noqa: E402
import kubehost  # noqa: E402
from kubedock import KubeDock, LogsTab, ShellTab  # noqa: E402
from kubeview import KubeRow, _KUBE_ORDER, kube_order  # noqa: E402
import viewwindow  # noqa: E402

REFRESH_S = 10  # (the list read again this often while the view's open, as the Kubernetes view's unwatched ones)
HEADINGS = {"running": "docker.remove_container_heading", "containers": "docker.remove_container_heading",
            "images": "docker.remove_image_heading", "volumes": "docker.remove_volume_heading",
            "networks": "docker.remove_network_heading", "compose": "docker.remove_compose_heading"}
BODIES = {"images": "docker.remove_image_body", "volumes": "docker.remove_volume_body",
          "networks": "docker.remove_network_body", "compose": "docker.remove_compose_body"}
LABELS = {"start": "new_session.start", "stop": "common.stop", "restart": "shared_settings.restart",
          "remove": "common.remove_ellipsis"}
DOING = {"start": "docker.starting", "stop": "docker.stopping", "restart": "docker.restarting",
         "remove": "docker.removing"}


def ask(host, msg, done):
    """A docker request of the host (kubehost.Host's: another one's Remote, or this machine's own host process, over
    its socket): done(reply) on the main thread; an error's reply is {"error": why}."""
    if not host.here:
        return host.ask("docker", msg, done)

    def run():
        try:
            ch, _info = wire.reach(host.where, timeout=10)
            try:
                ch.send({"op": "docker", "req": 1, **{k: v for k, v in msg.items() if v is not None}})
                while (r := ch.reply()) is not None and r.get("req") != 1:
                    pass
            finally:
                ch.close()
        except (OSError, ValueError) as e:
            r = {"op": "error", "error": str(e)}
        r = r or {"op": "error", "error": wording.say("host_status.no_answer")}
        r = {"error": wire.said(r) or wording.say("host_status.no_answer")} if r.get("op") == "error" else r
        GLib.idle_add(lambda: done(r) and False)
    threading.Thread(target=run, daemon=True, name="docker").start()


class Follow:
    """A docker_follow on a host, on a connection of its own (the host ends its docker when that goes): on_text(f,
    text) as the log's lines come, on_end(f, code, said) once it ends by itself; both on the main thread."""
    REQ = 1

    def __init__(self, where, msg, on_end, on_text):
        self.on_end, self.on_text, self.ch, self.stopped = on_end, on_text, None, False
        threading.Thread(target=self._run, args=(where, msg), daemon=True, name="docker-follow").start()

    def _run(self, where, msg):
        try:
            ch, _info = wire.reach(where, timeout=15)
        except (OSError, ValueError) as e:
            GLib.idle_add(self._ended, 1, str(e) or wording.say("host_status.no_answer"))
            return
        self.ch = ch
        if self.stopped:
            ch.close()
            return
        ch.keep_alive()
        ch.send({"op": "docker_follow", "req": self.REQ, **msg})
        end = (1, wording.say("host_status.connection_dropped"))
        try:
            for r in ch:
                if r.get("req") != self.REQ:
                    continue
                if r.get("op") == "docker_text":
                    GLib.idle_add(self._text, r.get("text") or "")
                elif r.get("op") == "docker_end":
                    end = (r.get("code") or 0, r.get("said") or "")
                    break
                elif r.get("op") == "error":
                    end = (1, wire.said(r))
                    break
        except (OSError, ValueError) as e:
            end = (1, str(e))
        ch.close()
        GLib.idle_add(self._ended, *end)

    def _text(self, text):
        if not self.stopped:
            self.on_text(self, text)
        return False

    def _ended(self, code, said):
        if not self.stopped:
            self.stopped = True
            self.on_end(self, code, said)
        return False

    def stop(self):
        self.stopped = True
        if self.ch is not None:
            self.ch.close()


class DockerLogs(LogsTab):
    """A container's or a Compose project's log in the dock, followed (docker logs -f, run by the host), as a pod's is:
    follow, wrap, find."""

    def __init__(self, dock, host, context, page, row):
        super().__init__(dock, host, context, context, row["name"], [(row["name"], False)], 0)
        self.page, self.oid = page, row["id"]
        self.previous.set_visible(False)
        self.container.set_visible(False)

    def start(self):
        self.stop()
        self.logs.load("", "pod.log")
        self.follow_logs(True)
        self.tail = Follow(self.host.where, {"what": "logs", "context": self.pod_key[0], "page": self.page,
                                             "id": self.oid}, self._end, self._text)
        self.state.set_label(wording.say("kube.following"))

    def _end(self, tail, code, said):
        if tail is not self.tail:
            return
        self.tail = None
        self.state.set_label(wording.say("docker.error", error=said) if code and said else
                             wording.say("docker.exited", code=code) if code else wording.say("multiplex.ended"))


class DockerShell(ShellTab):
    """A shell in a container in the dock (Multiplex's terminal: a Bot can find it, read it and type in it), the
    command its host gave for it (docker exec -it: bash if it has it, else sh), run there."""

    def __init__(self, dock, host, context, row):
        super().__init__(dock, host, context, context, row["name"], [row["name"]], 0)
        self.argv = list(row["shell"])
        self.container.set_visible(False)

    def start(self):
        import multiplexterm
        self.stop()
        if (dep := multiplexterm.missing()) is not None:  # (what the shell shows instead: VTE, and how it's installed)
            import depsview
            if self.slot.get_first_child() is None:
                missing = depsview.Missing()
                missing.show(dep, "multiplex.terminal_menu")
                self.slot.append(missing)
            return
        context, _c, name = self.pod_key
        term = multiplexterm.HostedTerminal(self.host.where, argv=self.argv, title=f"{name} · {context}",
                                            on_exit=self._end, host_name=self.host.name)
        multiplexterm.Terminals.get(wire.HEAD_CLIENT).add(term)
        self.term = term
        self.slot.append(term)
        self.state.set_label("")
        term.vte.grab_focus()

    def _end(self, term, status):
        if term is self.term:
            self.state.set_label(wording.say("docker.exited", code=status))


class DockerView(Gtk.Box):
    """☰ → Docker: the host's Docker, laid out as the Kubernetes view is (see this module's docstring). The top bar:
    the context, a filter (Ctrl+F), Refresh. The sidebar: the categories. The table: a page's objects, sorted by any
    column; a row ends with its log, a shell (a running container's) and ⋮ (Start, Stop, Restart, Remove…, as it
    is). Selecting a row inspects it beside the table. Esc closes the picked dock tab, then the inspect, then the
    view."""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, visible=False, focusable=True)
        self.add_css_class("baude-frame")
        ensure_icons()
        self.win, self.contexts, self.context, self.sections = win, [], None, []
        self.page = {"title": "Running", "key": "running", "what": "running containers"}
        self.table_page = self.table_cols = None
        self.store = self.filter = self.selection = self.table = None
        self.generation, self.loading, self.timer, self.syncing, self.note = 0, False, None, False, None
        self.fault = None
        self.found = weakref.WeakKeyDictionary()
        self.host = kubehost.Host(None, {"via": "local"}, wording.say("senders.local"))
        self.last_context = {}
        self.inspected = None  # (the row whose inspect is shown: its id)
        bar = view_bar()
        title = Gtk.Label(label=wording.say("window.docker"))
        title.add_css_class("heading")
        bar.append(title)
        self.host_label = viewwindow.host_label()
        bar.append(self.host_label)
        self.context_dd = ellipsize_dropdown(Gtk.DropDown.new_from_strings([]), 32)
        self.context_dd.set_tooltip_text(wording.say("docker.context_tip"))
        self.context_dd.connect("notify::selected", self.on_pick)
        bar.append(self.context_dd)
        self.search = Gtk.SearchEntry(placeholder_text=wording.say("kube.filter"), width_chars=22,
                                     tooltip_text=wording.say("kube.filter_tip"))
        self.search.connect("search-changed", lambda *_: self.refilter())
        self.search.connect("stop-search", lambda e: e.set_text("") if e.get_text() else self.escape())
        bar.append(self.search)
        bar.append(Gtk.Box(hexpand=True))
        self.refresh_btn = RefreshButton(tooltip_text=wording.say("common.read_again"))
        self.refresh_btn.connect("clicked", lambda *_: self.load())
        bar.append(self.refresh_btn)
        self.out = viewwindow.Out(self, bar, wording.say("window.docker"), self.search)
        self.append(Gtk.WindowHandle(child=bar))
        self.side = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.side.add_css_class("navigation-sidebar")
        self.side.add_css_class("kube-side")
        self.side.connect("row-selected", self.on_side)
        side_scroll = Gtk.ScrolledWindow(child=self.side, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                         width_request=tokens.px("kube.sidebar-width"))
        self.table_scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.fade = Fade(self.table_scroll)
        self.status = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.status.add_css_class("kube-status-line")
        table_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.table_stack = Gtk.Stack(vexpand=True, hexpand=True)
        self.table_stack.add_named(self.table_scroll, "table")
        self.table_stack.add_named(self.build_error(), "error")
        import depsview
        self.missing = depsview.Missing(again=self.read_contexts)
        self.table_stack.add_named(self.missing, "missing")
        table_box.append(self.table_stack)
        table_box.append(self.status)
        self.details = CodeView()
        self.details_fade = Fade(self.details)
        head = Gtk.Box(spacing=6, margin_start=10, margin_end=6, margin_top=6)
        self.details_title = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE)
        self.details_title.add_css_class("heading")
        head.append(self.details_title)
        self.format_dd = Gtk.DropDown.new_from_strings(["JSON", "YAML"])
        self.format_dd.set_selected(1 if load_settings().get("docker_inspect") == "yaml" else 0)
        self.format_dd.set_tooltip_text(wording.say("docker.format_tip"))
        self.format_dd.connect("notify::selected", lambda *_: self.syncing or (
            save_settings(docker_inspect=self.fmt()), self.inspect(again=True)))
        head.append(self.format_dd)
        hide = Gtk.Button(icon_name="window-close-symbolic", tooltip_text=wording.say("kube.hide"))
        hide.add_css_class("flat")
        hide.connect("clicked", lambda *_: self.hide_details())
        head.append(hide)
        self.details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=False, width_request=360)
        self.details_box.append(head)
        self.details_box.append(self.details)
        self.right = paned_grip(Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, start_child=table_box,
                                          end_child=self.details_box, resize_start_child=True,
                                          resize_end_child=False, shrink_end_child=False))
        self.dock = KubeDock()
        self.dock_split = paned_grip(Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, start_child=inside(self.right),
                                               end_child=self.dock, resize_start_child=True, resize_end_child=False,
                                               shrink_end_child=False))
        self.dock_split.add_css_class("main-split")
        body = paned_grip(Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, start_child=side_scroll,
                                    end_child=self.dock_split, resize_start_child=False, shrink_start_child=False,
                                    vexpand=True))
        body.add_css_class("main-split")
        self.append(body)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, kv, _k, _s: (self.escape(), True)[1] if kv == Gdk.KEY_Escape else False)
        self.add_controller(keys)
        actions = Gio.SimpleActionGroup()  # (a row's buttons: the view's actions, the row's id their target)
        for name, fn in (("logs", self.show_logs), ("shell", self.show_shell),
                         *((a, lambda oid, a=a: self.act(oid, a)) for a in ("start", "stop", "restart", "remove"))):
            act = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            act.connect("activate", lambda _a, target, fn=fn: fn(target.get_string()))
            actions.add_action(act)
        insert_actions(self, "docker", actions)

    def build_error(self):
        """What's shown instead of the table when Docker can't be reached: why, the command that starts it to copy,
        Try Again, and all docker wrote, folded away (as the Kubernetes view's)."""
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
        again.connect("clicked", lambda *_: self.load(switch=True))
        box.append(again)
        self.error_said = Gtk.Label(selectable=True, xalign=0, yalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.error_said.add_css_class("mono")
        self.error_more = Gtk.Expander(label=wording.say("docker.output"), child=Gtk.ScrolledWindow(
            child=self.error_said, propagate_natural_height=True, max_content_height=220,
            hscrollbar_policy=Gtk.PolicyType.NEVER))
        box.append(self.error_more)
        return Gtk.ScrolledWindow(child=box, hscrollbar_policy=Gtk.PolicyType.NEVER)

    def show_error(self, fault):
        self.fault = fault
        self.error_title.set_label(fault.get("title") or "")
        self.error_why.set_label(fault.get("why") or "")
        self.error_why.set_visible(bool(fault.get("why")))
        self.error_fix.set_label(fault.get("fix") or "")
        self.error_fix_row.set_visible(bool(fault.get("fix")))
        said = fault.get("said") or ""
        self.error_said.set_label(said)
        self.error_more.set_visible(bool(said) and said.strip() != (fault.get("why") or ""))
        self.table_stack.set_visible_child_name("error")
        self.say(f"{self.context}: {fault.get('title') or ''}")

    # ---- the sidebar
    def fill_side(self, sections):
        """The host's categories: a category of several pages a heading over them, one of a page its row, with its
        icon (as the Kubernetes view's)."""
        if [s.get("title") for s in sections] == [s.get("title") for s in self.sections]:
            return
        self.sections = sections
        self.syncing = True
        while (row := self.side.get_first_child()) is not None:
            self.side.remove(row)
        for s in sections:
            pages = s.get("pages") or []
            single = len(pages) == 1 and pages[0].get("title") == s.get("title")
            if not single:
                head = Gtk.Box(spacing=8, margin_start=6, margin_end=4)
                if s.get("icon"):
                    head.append(Gtk.Image(icon_name=s["icon"]))
                head.append(Gtk.Label(label=s.get("title") or "", xalign=0, hexpand=True))
                head.add_css_class("kube-side-head")
                row = Gtk.ListBoxRow(child=head, activatable=False, selectable=False)
                row.add_css_class("kube-side-head-row")
                self.side.append(row)
            for p in pages:
                child = Gtk.Box(spacing=8, margin_start=6 if single else 30)
                if single and s.get("icon"):
                    child.append(Gtk.Image(icon_name=s["icon"]))
                child.append(Gtk.Label(label=p.get("title") or "", xalign=0, ellipsize=Pango.EllipsizeMode.END))
                row = Gtk.ListBoxRow(child=child)
                row.page = p
                self.side.append(row)
        self.syncing = False
        self.select_page_row()

    def select_page_row(self):
        self.syncing = True
        row = self.side.get_first_child()
        while row is not None:
            if getattr(row, "page", {}).get("key") == self.page["key"]:
                self.side.select_row(row)
                break
            row = row.get_next_sibling()
        self.syncing = False

    def on_side(self, _box, row):
        if self.syncing or row is None or not hasattr(row, "page") or row.page["key"] == self.page["key"]:
            return
        self.page = row.page
        self.hide_details()
        self.load(switch=True)

    # ---- opening and closing
    def open(self, context=None):
        """Show the view, on the host picked in the window's host dropdown (as ☰ → Terminal's tabs are): on context
        (else the one shown there last, else docker's current one). Out in a window of its own: that window brought
        up."""
        host = kubehost.picked(self.win)
        same_host = host.key() == self.host.key()
        if not same_host:
            self.leave_host()
        self.host = host
        self.host_label.set_label(host.name)
        if self.out.present():
            if same_host and context is None:
                return
        else:
            self.set_visible(True)
            self.grab_focus()
        if self.timer is None:
            self.timer = GLib.timeout_add_seconds(REFRESH_S, self.tick)
        self.read_contexts(context)

    def read_contexts(self, context=None):
        """The host's contexts and categories asked for; the one wanted then read. Without docker there, what's
        missing shown instead."""
        host = self.host
        if self.table_stack.get_visible_child_name() == "missing":
            self.table_stack.set_visible_child_name("table")
        self.say(wording.say("kube.connecting", context=host.name))

        def got(r):
            if host is not self.host:
                return
            if r.get("missing"):
                return self.show_missing(r["missing"])
            self.fill_side(r.get("sections") or [])
            self.contexts = r.get("contexts") or []
            names = [c["name"] for c in self.contexts]
            if r.get("error") or not names:
                self.say(f"{host.name}: {r['error']}" if r.get("error") else wording.say("docker.no_context"))
                return
            last = load_settings().get("docker_context") if host.here else self.last_context.get(host.key())
            current = next((c["name"] for c in self.contexts if c.get("current")), None)
            want = context if context in names else last if last in names else current or names[0]
            self.syncing = True
            self.context_dd.set_model(Gtk.StringList.new(names))
            self.context_dd.set_selected(names.index(want))
            self.syncing = False
            self.switch_context(want)
        ask(host, {"what": "contexts"}, got)

    def show_missing(self, dep):
        self.generation += 1
        self.loading = False
        self.refresh_btn.set_loading(False)
        if self.store is not None:
            self.store.remove_all()
        self.fade.done()
        self.missing.show(dep, "window.docker")
        self.table_stack.set_visible_child_name("missing")
        self.say("")

    def leave_host(self):
        self.dock.close_all()
        self.hide_details()
        self.generation += 1
        self.context, self.contexts = None, []
        self.syncing = True
        self.context_dd.set_model(Gtk.StringList.new([]))
        self.syncing = False
        if self.store is not None:
            self.store.remove_all()
        self.table_stack.set_visible_child_name("table")

    def close(self):
        self.set_visible(False)
        self.dock.close_all()
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None
        self.win.input.grab_focus()

    def escape(self):
        if self.dock.get_visible():
            self.dock.close_current()
        elif self.details_box.get_visible():
            self.hide_details()
        elif self.out.window is None:
            self.close()

    def find(self):
        """Ctrl+F while the view is open over the window: its filter, focused. False if it isn't."""
        if not self.out.here():
            return False
        self.search.grab_focus()
        self.search.select_region(0, -1)
        return True

    def tick(self):
        if self.get_visible() and not self.loading and self.context and self.fault is None:
            self.load(quiet=True)
        return True

    def say(self, text):
        self.status.set_label(text)

    def on_pick(self, dd, _pspec):
        if self.syncing or not self.contexts:
            return
        GLib.idle_add(lambda: self.switch_context(self.contexts[dd.get_selected()]["name"]) and False)

    def switch_context(self, name):
        self.context = name
        if self.host.here:
            save_settings(docker_context=name)
        else:
            self.last_context[self.host.key()] = name
        self.hide_details()
        self.dock.close_all()
        self.load(switch=True)

    # ---- a page's objects
    def load(self, quiet=False, switch=False):
        """The page's objects read (switch: another context or page, so the table fades till they're in)."""
        if not self.context:
            return
        page, context = self.page, self.context
        gen = self.generation = self.generation + 1
        self.loading = True
        if switch:
            self.fade.hold()
        if not quiet:
            self.refresh_btn.set_loading(True)
            self.say(wording.say("kube.reading", kind=page["what"], context=context))
        ask(self.host, {"what": "list", "context": context, "page": page["key"]},
            lambda r: self._loaded(gen, page, r))

    def _loaded(self, gen, page, r):
        if gen != self.generation:
            return
        self.loading = False
        self.refresh_btn.set_loading(False)
        self.fade.done()
        if r.get("missing"):
            return self.show_missing(r["missing"])
        if r.get("fault") or r.get("error"):
            return self.show_error(r.get("fault") or {"title": r["error"]})
        self.fault = None
        self.table_stack.set_visible_child_name("table")
        cols = r.get("columns") or []
        if self.table_page is not page or self.table_cols != cols:
            self.build_table(page, cols)
        picked = self.selected_id()
        now = time.time()
        self.store.splice(0, self.store.get_n_items(),
                          [KubeRow(list(row.get("cells") or []), list(row.get("keys") or []), dict(row, now=now))
                           for row in r.get("rows") or []])
        if picked is not None:
            for i in range(self.selection.get_n_items()):
                if self.selection.get_item(i).obj["id"] == picked:
                    self.selection.set_selected(i)
                    break
        self.update_status()

    def build_table(self, page, cols):
        self.store = Gio.ListStore(item_type=KubeRow)
        self.filter = Gtk.CustomFilter.new(self.row_visible, None)
        cv = Gtk.ColumnView(show_row_separators=False, show_column_separators=False)
        cv.add_css_class("data-table")
        cv.add_css_class("kube-table")
        columns = [self.make_column(i, col) for i, col in enumerate(cols)]
        for c in columns:
            cv.append_column(c)
        cv.append_column(self.make_buttons())
        header = cv.get_first_child()
        last = header.get_last_child() if header is not None and header.get_css_name() == "header" else None
        if last is not None:
            last.set_can_target(False)
        model = Gtk.SortListModel(model=Gtk.FilterListModel(model=self.store, filter=self.filter),
                                  sorter=cv.get_sorter())
        self.selection = Gtk.SingleSelection(model=model, autoselect=False, can_unselect=True)
        self.selection.set_selected(Gtk.INVALID_LIST_POSITION)
        self.selection.connect("notify::selected-item", self.on_select)
        cv.set_model(self.selection)
        if columns:
            first = next((c for c, col in zip(columns, cols) if col.get("kind") == "name"), columns[0])
            cv.sort_by_column(first, Gtk.SortType.ASCENDING)
        self.table, self.table_page, self.columns, self.table_cols = cv, page, columns, cols
        self.table_scroll.set_child(cv)

    def make_column(self, i, col):
        f = Gtk.SignalListItemFactory()
        kind = col.get("kind")

        def setup(_f, item):
            item.set_child(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                     max_width_chars=60 if kind == "name" else 40))

        def bind(_f, item):
            row, w = item.get_item(), item.get_child()
            text = row.texts[i] if i < len(row.texts) else ""
            self.find_label(w, text)
            tips = row.obj.get("tips") or []
            w.set_tooltip_text((tips[i] if i < len(tips) else "") or (text if len(text) > 40 else None))
            if kind == "age":
                age = row.keys[i] if i < len(row.keys) else None
                time_tip(w, row.obj["now"] - age if isinstance(age, (int, float)) else None)
            for t in ("ok", "warn", "bad"):
                w.remove_css_class(f"kube-{t}")
            tones = row.obj.get("tones") or []
            if i < len(tones) and tones[i]:
                w.add_css_class(f"kube-{tones[i]}")
        f.connect("setup", setup)
        f.connect("bind", bind)
        c = Gtk.ColumnViewColumn(title=col.get("title") or "", factory=f, resizable=True, expand=kind == "name")
        if kind == "name":
            c.set_fixed_width(tokens.px("kube.name-width"))
        c.set_sorter(Gtk.CustomSorter.new(lambda a, b, _d: _KUBE_ORDER[kube_order(
            a.keys[i] if i < len(a.keys) else None, b.keys[i] if i < len(b.keys) else None)], None))
        return c

    def make_buttons(self):
        """A row's last column, as a pod's in the Kubernetes view: its log, a shell (a running container's), and ⋮, what
        can be done to it as it is (Start, Stop, Restart, Remove…). Each is the view's action, the row's id its
        target."""
        f = Gtk.SignalListItemFactory()

        def setup(_f, item):
            box = Gtk.Box(spacing=2, halign=Gtk.Align.END)
            logs = Gtk.Button(icon_name="baude-logs-symbolic", tooltip_text=wording.say("kube.logs"),
                              action_name="docker.logs")
            shell = Gtk.Button(icon_name="utilities-terminal-symbolic", tooltip_text=wording.say("kube.shell"),
                               action_name="docker.shell")
            more = Gtk.MenuButton(icon_name="view-more-symbolic")
            for b in (logs, shell, more):
                b.add_css_class("flat")
                b.add_css_class("kube-row-btn")
                box.append(b)
            item.set_child(box)

        def bind(_f, item):
            obj, box = item.get_item().obj, item.get_child()
            key = GLib.Variant("s", obj["id"])
            logs = box.get_first_child()
            shell, more = logs.get_next_sibling(), box.get_last_child()
            logs.set_action_target_value(key)
            shell.set_action_target_value(key)
            logs.set_opacity(1 if obj.get("logs") else 0)
            logs.set_can_target(bool(obj.get("logs")))
            shell.set_opacity(1 if obj.get("shell") else 0)
            shell.set_can_target(bool(obj.get("shell")))
            menu = Gio.Menu()
            for a in obj.get("actions") or []:
                entry = Gio.MenuItem.new(wording.say(LABELS[a]), None)
                entry.set_action_and_target_value(f"docker.{a}", key)
                menu.append_item(entry)
            more.set_menu_model(menu)
            more.set_opacity(1 if obj.get("actions") else 0)
            more.set_can_target(bool(obj.get("actions")))
        f.connect("setup", setup)
        f.connect("bind", bind)
        return Gtk.ColumnViewColumn(title="", factory=f, resizable=False)

    def row_visible(self, row, _data=None):
        text = self.search.get_text().strip().lower()
        return not text or any(text in t.lower() for t in row.texts)

    def refilter(self):
        if self.filter is not None:
            self.filter.changed(Gtk.FilterChange.DIFFERENT)
        q = self.search.get_text().strip()
        for label, text in list(self.found.items()):
            find_label(label, text, q)
        self.update_status()

    def find_label(self, label, text):
        self.found[label] = text
        find_label(label, text, self.search.get_text().strip())

    def update_status(self):
        if self.selection is None:
            return
        n, total = self.selection.get_n_items(), self.store.get_n_items()
        what = self.table_page["what"]
        shown = wording.say("kube.count_of", n=n, what=what, total=total) if n != total else \
            wording.say("kube.count", n=n, what=what)
        bits = [self.note and self.note[1] > time.monotonic() and self.note[0], shown, self.context]
        self.say(" · ".join(b for b in bits if b))

    def set_note(self, text, seconds=30):
        self.note = (text, time.monotonic() + seconds)
        self.update_status() if self.selection is not None else self.say(text)

    # ---- an object: inspected
    def row(self, oid):
        if self.store is None:
            return None
        for i in range(self.store.get_n_items()):
            if self.store.get_item(i).obj["id"] == oid:
                return self.store.get_item(i).obj
        return None

    def selected_id(self):
        item = self.selection.get_selected_item() if self.selection is not None else None
        return item.obj["id"] if item is not None else None

    def fmt(self):
        return "yaml" if self.format_dd.get_selected() == 1 else "json"

    def on_select(self, sel, _pspec):
        item = sel.get_selected_item()
        if item is None:
            return
        obj = item.obj
        self.details_title.set_label(obj["name"])
        self.details_box.set_visible(True)
        if self.right.get_position() < 200 or self.right.get_position() > self.right.get_width() - 200:
            self.right.set_position(max(320, int(self.right.get_width() * 0.62)))
        if self.inspected != obj["id"]:
            self.inspected = obj["id"]
            self.inspect()

    def inspect(self, again=False):
        """The object selected, inspected by its host (JSON or YAML, as picked), shown beside the table."""
        oid, page, context, fmt = self.inspected, self.table_page, self.context, self.fmt()
        if oid is None or page is None:
            return
        self.details_fade.hold()

        def got(r):
            if oid != self.inspected or context != self.context:
                return
            self.details.load(r.get("text") or f"# {r.get('error') or ''}\n", r.get("file") or f"inspect.{fmt}")
            self.details_fade.done()
        ask(self.host, {"what": "inspect", "context": context, "page": page["key"], "id": oid, "format": fmt}, got)

    def hide_details(self):
        self.details_box.set_visible(False)
        self.inspected = None
        if self.selection is not None:
            self.selection.set_selected(Gtk.INVALID_LIST_POSITION)

    # ---- logs and shells: a tab each in the dock
    def show_dock(self):
        if not self.dock.get_visible():
            self.dock.set_visible(True)
            self.dock_split.set_position(max(160, self.dock_split.get_height() // 2))

    def show_logs(self, oid):
        obj = self.row(oid)
        if obj is None or not obj.get("logs"):
            return
        key = (self.context, self.context, obj["name"])
        if (tab := self.dock.find(DockerLogs, key)) is not None:
            return self.dock.show(tab)
        self.show_dock()
        self.dock.add(DockerLogs(self.dock, self.host, self.context, self.table_page["key"], obj))

    def show_shell(self, oid):
        obj = self.row(oid)
        if obj is None or not obj.get("shell"):
            return
        self.show_dock()
        self.dock.add(DockerShell(self.dock, self.host, self.context, obj))

    @property
    def logs(self):
        tab = self.dock.current()
        return tab.logs if isinstance(tab, DockerLogs) else None

    @property
    def shell_term(self):
        tab = self.dock.current()
        return tab.term if isinstance(tab, DockerShell) else None

    # ---- acting: start, stop, restart; a remove asked first
    def act(self, oid, what):
        obj = self.row(oid)
        if obj is None:
            return
        if what == "remove":
            return self.ask_remove(obj)
        self.do(obj, what)

    def ask_remove(self, obj):
        """Ask before removing it: what it is, its name, the context, and what goes with it. Nothing's removed unless
        you say Remove."""
        page, context = self.table_page["key"], self.context
        body = [wording.say("docker.on_context", context=context),
                wording.say(BODIES.get(page) or ("docker.remove_running_body" if obj.get("running") else
                                                 "docker.remove_container_body"))]
        dlg = Adw.AlertDialog(heading=wording.say(HEADINGS[page], name=obj["name"]), body="\n\n".join(body),
                              default_response="cancel", close_response="cancel")
        dlg.add_response("cancel", wording.say("common.cancel"))
        dlg.add_response("remove", wording.say("common.remove"))
        dlg.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda _d, r: r == "remove" and context == self.context and self.do(obj, "remove"))
        self.dialog = dlg
        dlg.present(self.win)

    def do(self, obj, what):
        context, page = self.context, self.table_page["key"]
        self.set_note(wording.say(DOING[what], name=obj["name"]))

        def done(r):
            if context != self.context:
                return
            self.set_note(r.get("said") or r.get("error") or "")
            self.load(quiet=True)
        ask(self.host, {"what": what, "context": context, "page": page, "id": obj["id"]}, done)

