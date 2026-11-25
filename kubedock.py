"""The Kubernetes view's dock (KubeDock), under its table and YAML, as Freelens' is: a tab per pod's log followed
(LogsTab) or shell in a pod (ShellTab), any number of each, on a strip in the frame's grey; the picked tab's controls at
the strip's end, its log or terminal inside under it. On this machine kubectl runs here; on another host, there (its
host process: kubehost.Follow for a log, a hosted terminal for a shell)."""
import os

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk, Pango  # noqa: E402

from baudelib import tokens, wire, wording  # noqa: E402
from codeview import CodeView  # noqa: E402
from kubedata import KUBE_LOG_LINES, KUBE_LOG_TAIL  # noqa: E402
from lookupkube import kube_tool  # noqa: E402
from pickers import ellipsize_dropdown  # noqa: E402
from widgets import INSIDE_GAP, inside, load_settings, save_settings  # noqa: E402

SHELL = ["sh", "-c", "command -v bash >/dev/null 2>&1 && exec bash || exec sh"]


def _flat(*buttons):
    for b in buttons:
        b.add_css_class("flat")
    return buttons


class DockTab(Gtk.Box):
    """A tab's inside (its log, its terminal) and its controls (shown at the strip's end while it's picked)."""
    ICON = None

    def __init__(self, dock, host, context, namespace, pod):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True, hexpand=True)
        self.dock, self.host, self.pod_key = dock, host, (context, namespace, pod)
        self.syncing = False
        self.controls = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER, margin_end=4)
        self.state = Gtk.Label(xalign=1, ellipsize=Pango.EllipsizeMode.END, max_width_chars=28)
        self.state.add_css_class("dim-label")
        self.state.add_css_class("caption")
        self.controls.append(self.state)
        self.container = ellipsize_dropdown(Gtk.DropDown.new_from_strings([]), 28)
        self.container.set_tooltip_text(wording.say("kube.container"))
        self.container.connect("notify::selected", lambda *_: self.syncing or self.start())
        self.controls.append(self.container)

    def title(self):
        return f"{self.pod_key[2]} · {self.pod_key[1]}"

    def pick(self, names, index):
        self.syncing = True
        self.container.set_model(Gtk.StringList.new(names))
        self.container.set_selected(index)
        self.syncing = False

    def start(self):
        pass

    def stop(self):
        pass

    def focus(self):
        pass


class LogsTab(DockTab):
    """A pod's logs: its containers to pick from (init containers marked), the previous run's, follow, wrap, find; the
    log itself (CodeView: Ctrl+F finds in it). kubectl logs -f runs while the tab's open, and is stopped as it closes."""
    ICON = "baude-logs-symbolic"

    def __init__(self, dock, host, context, namespace, pod, containers, pick):
        super().__init__(dock, host, context, namespace, pod)
        self.containers, self.tail, self.following = containers, None, True
        self.logs = CodeView()
        self.previous = Gtk.ToggleButton(label=wording.say("kube.previous"), tooltip_text=wording.say("kube.previous_tip"))
        self.previous.connect("toggled", lambda *_: self.syncing or self.start())
        self.follow = Gtk.ToggleButton(icon_name="go-bottom-symbolic", active=True,
                                       tooltip_text=wording.say("kube.follow_tip"))
        self.follow.connect("toggled", lambda b: self.syncing or self.follow_logs(b.get_active()))
        wrap = Gtk.ToggleButton(icon_name="baude-wrap-symbolic", tooltip_text=wording.say("code_view.wrap"),
                                active=bool(load_settings().get("kube_log_wrap")))
        wrap.connect("toggled", lambda b: (self.dock.wrap_all(b.get_active()), save_settings(kube_log_wrap=b.get_active())))
        self.wrap = wrap
        self.logs.set_wrap(wrap.get_active())
        find = Gtk.Button(icon_name="edit-find-symbolic", tooltip_text=wording.say("code_view.find_tip"))
        find.connect("clicked", lambda *_: self.logs.open_search())
        for b in _flat(self.previous, self.follow, wrap, find):
            self.controls.append(b)
        # following stops when you scroll up (the wheel, the scrollbar, the keys), not whenever the view moves: a
        # text view moves itself as it measures lines it had guessed at, and that mustn't stop it
        adj = self.logs.scroll.get_vadjustment()
        adj.connect("changed", self._moved)
        adj.connect("value-changed", self._moved)
        self.wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        self.wheel.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.wheel.connect("scroll", lambda _c, _dx, dy: (dy < 0 and self.follow_logs(False), False)[1])
        self.logs.scroll.add_controller(self.wheel)
        self.dragging = False
        drag = Gtk.GestureDrag(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        drag.connect("drag-begin", lambda *_: setattr(self, "dragging", True))
        drag.connect("drag-end", lambda *_: setattr(self, "dragging", False))
        self.logs.scroll.get_vscrollbar().add_controller(drag)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        up = {Gdk.KEY_Up, Gdk.KEY_Page_Up, Gdk.KEY_Home, Gdk.KEY_KP_Up, Gdk.KEY_KP_Page_Up, Gdk.KEY_KP_Home}
        keys.connect("key-pressed", lambda _c, kv, _k, _s: (kv in up and self.follow_logs(False), False)[1])
        self.logs.view.add_controller(keys)
        self.append(self.logs)
        self.pick([wording.say("kube.init_container", name=n) if init else n for n, init in containers], pick)

    def start(self):
        """The container picked, its log from the start: this run's followed (kubectl logs -f), or, with Previous,
        its last run's (which has ended). The one followed before is stopped first."""
        self.stop()
        i = self.container.get_selected()
        if not 0 <= i < len(self.containers):
            return
        context, ns, pod = self.pod_key
        previous, container = self.previous.get_active(), self.containers[i][0]
        self.logs.load("", "pod.log")
        self.follow_logs(True)
        if self.host.here:
            from kubeview import KubeLogTail
            args = ["--context", context, "logs", "--previous" if previous else "-f", f"--tail={KUBE_LOG_TAIL}",
                    "-n", ns, pod, "-c", container]
            try:
                self.tail = KubeLogTail(args, self._text, lambda t, code: self._end(t, code, ""))
            except OSError as e:
                self.state.set_label(wording.say("kube.kubectl_error", error=e))
                return
        else:
            import kubehost
            self.tail = kubehost.Follow(self.host.where, {"what": "logs", "context": context, "namespace": ns,
                                                          "pod": pod, "container": container, "previous": previous},
                                        self._end, on_text=self._text)
        self.state.set_label(wording.say("kube.previous_run") if previous else wording.say("kube.following"))

    def stop(self):
        if self.tail is not None:
            self.tail.stop()
            self.tail = None

    def focus(self):
        self.logs.view.grab_focus()

    def _text(self, tail, text):
        if tail is not self.tail:
            return
        b = self.logs.buffer
        before = b.get_line_count()
        b.insert(b.get_end_iter(), text.replace("\r\n", "\n"))
        if b.get_line_count() > KUBE_LOG_LINES * 1.1:  # (the oldest go, a tenth at a time)
            b.delete(b.get_start_iter(), b.get_iter_at_line(b.get_line_count() - KUBE_LOG_LINES)[1])
            if self.logs.search_bar.get_search_mode():  # (the matches' places moved)
                self.logs.find(self.logs.search_entry.get_text(), scroll=False)
        if len(str(b.get_line_count())) != len(str(before)):
            self.logs.gutter.queue_resize()  # (wider line numbers)

    def _end(self, tail, code, said):
        if tail is not self.tail:
            return
        self.tail = None
        if code:
            self.state.set_label(wording.say("kube.kubectl_error", error=said) if said and not self.host.here else
                                 wording.say("kube.kubectl_exited", code=code))
        elif not self.previous.get_active():
            self.state.set_label(wording.say("kube.ended"))

    def follow_logs(self, on):
        """Keep to the newest line as lines come (on), or stay where you've scrolled to."""
        self.following = on
        was, self.syncing = self.syncing, True
        self.follow.set_active(on)
        self.syncing = was
        if on:
            adj = self.logs.scroll.get_vadjustment()
            adj.set_value(adj.get_upper() - adj.get_page_size())

    def _moved(self, adj):
        """More lines, another size, the view moved: while following, back to the end (unless it's you dragging the
        scrollbar away from it); not following, the end reached again follows again."""
        end = adj.get_upper() - adj.get_page_size()
        if adj.get_value() >= end - 2:
            if not self.following:
                self.follow_logs(True)
        elif self.following:
            if self.dragging:
                self.follow_logs(False)
            else:
                adj.set_value(end)


class ShellTab(DockTab):
    """A shell in a pod (Multiplex's terminal: a Bot can find it, read it and type in it, as Multiplex's own), kubectl
    exec -it in the container picked of its running ones; or why there can't be one."""
    ICON = "utilities-terminal-symbolic"

    def __init__(self, dock, host, context, namespace, pod, containers, pick):
        super().__init__(dock, host, context, namespace, pod)
        self.containers, self.term = containers, None
        self.slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.append(self.slot)
        self.pick(containers, pick)

    def start(self):
        """kubectl exec -it in the container picked: bash if it has it, else sh. The one before is ended first."""
        import multiplexterm
        self.stop()
        i = self.container.get_selected()
        if not 0 <= i < len(self.containers):
            return
        if (why := multiplexterm.available()) is not None:
            self.state.set_label(why)
            return
        context, ns, pod = self.pod_key
        args = ["--context", context, "exec", "-it", "-n", ns, pod, "-c", self.containers[i], "--", *SHELL]
        title = f"{pod} · {ns} · {context}"
        if self.host.here:
            term = multiplexterm.Terminal([kube_tool("kubectl"), *args], title=title, on_exit=self._end)
        else:  # (run there by its host process, as the phone's are)
            term = multiplexterm.HostedTerminal(self.host.where, argv=["kubectl", *args], title=title,
                                                on_exit=self._end, host_name=self.host.name)
        multiplexterm.Terminals.get(wire.HEAD_CLIENT).add(term)
        self.term = term
        self.slot.append(term)
        self.state.set_label("")
        term.vte.grab_focus()

    def _end(self, term, status):
        if term is self.term:
            import multiplexterm
            code = status if isinstance(term, multiplexterm.HostedTerminal) else \
                os.waitstatus_to_exitcode(status) if status > 0 else status  # (a host's says its exit code)
            self.state.set_label(wording.say("kube.kubectl_exited", code=code))

    def stop(self):
        if self.term is not None:
            import multiplexterm
            self.term.stop()
            multiplexterm.Terminals.get().remove(self.term)
            self.slot.remove(self.term)
            self.term = None

    def focus(self):
        if self.term is not None:
            self.term.vte.grab_focus()


class KubeDock(Gtk.Box):
    """The dock: a strip of tabs in the frame's grey, as Freelens' dock has them (each a pod's logs or a shell in one:
    its icon, "<pod> · <namespace>", ×; the picked one joined to what's under it), the picked one's controls at the
    strip's end; under it, inside, the picked one. Closing a tab stops what it runs; the last one closed hides the dock."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, visible=False, vexpand=True,
                         height_request=tokens.px("kube.logs-min"))
        self.tabs, self.picked = [], None  # [(tab, its strip button)]
        self.strip = Gtk.Box(spacing=2, valign=Gtk.Align.END)
        strip = Gtk.ScrolledWindow(child=self.strip, hexpand=True, vscrollbar_policy=Gtk.PolicyType.NEVER,
                                   hscrollbar_policy=Gtk.PolicyType.EXTERNAL, propagate_natural_height=True)
        self.controls = Gtk.Stack(hhomogeneous=False, transition_type=Gtk.StackTransitionType.NONE,
                                  valign=Gtk.Align.CENTER)
        bar = Gtk.Box(spacing=6, margin_start=INSIDE_GAP, margin_end=INSIDE_GAP)
        bar.add_css_class("kube-dock-bar")  # (on the frame, its grey: theming.CSS)
        bar.append(strip)
        bar.append(self.controls)
        self.append(bar)
        self.body = Gtk.Stack(vexpand=True, hexpand=True, transition_type=Gtk.StackTransitionType.NONE)
        self.append(inside(self.body))

    def all(self):
        return [t for t, _b in self.tabs]

    def current(self):
        return self.picked

    def add(self, tab, select=True):
        head = Gtk.Box(spacing=6)
        head.append(Gtk.Image(icon_name=tab.ICON))
        head.append(Gtk.Label(label=tab.title(), ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=40))
        pick = Gtk.Button(child=head, tooltip_text=" · ".join(reversed(tab.pod_key)))
        pick.add_css_class("flat")
        pick.connect("clicked", lambda *_: self.show(tab))
        close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text=wording.say("common.close"),
                           valign=Gtk.Align.CENTER)
        close.add_css_class("flat")
        close.add_css_class("circular")
        close.connect("clicked", lambda *_: self.close(tab))
        button = Gtk.Box(css_classes=["kube-dock-tab"])
        button.append(pick)
        button.append(close)
        middle = Gtk.GestureClick(button=Gdk.BUTTON_MIDDLE)  # (a middle click closes it, as a browser's tab)
        middle.connect("released", lambda *_: self.close(tab))
        button.add_controller(middle)
        self.strip.append(button)
        self.controls.add_child(tab.controls)
        self.body.add_child(tab)
        self.tabs.append((tab, button))
        self.set_visible(True)
        if select or self.picked is None:
            self.show(tab)
        tab.start()
        return tab

    def find(self, kind, pod_key):
        return next((t for t in self.all() if isinstance(t, kind) and t.pod_key == pod_key), None)

    def show(self, tab):
        """The tab picked: its inside and its controls shown, its button joined to them."""
        self.picked = tab
        for t, button in self.tabs:
            (button.add_css_class if t is tab else button.remove_css_class)("picked")
        self.body.set_visible_child(tab)
        self.controls.set_visible_child(tab.controls)
        tab.focus()

    def close(self, tab):
        """A tab closed: what it ran stopped; the one beside it picked (the dock hidden with the last one)."""
        at = next((i for i, (t, _b) in enumerate(self.tabs) if t is tab), None)
        if at is None:
            return
        _t, button = self.tabs.pop(at)
        tab.stop()
        self.strip.remove(button)
        self.controls.remove(tab.controls)
        self.body.remove(tab)
        if self.picked is tab:
            self.picked = None
            if self.tabs:
                self.show(self.tabs[min(at, len(self.tabs) - 1)][0])
        if not self.tabs:
            self.set_visible(False)

    def close_current(self):
        if self.picked is not None:
            self.close(self.picked)

    def close_all(self, kind=None):
        for t in [t for t in self.all() if kind is None or isinstance(t, kind)]:
            self.close(t)

    def wrap_all(self, on):
        """Wrap, remembered, is every log's: the other tabs' buttons and logs follow the one toggled."""
        for t in self.all():
            if isinstance(t, LogsTab):
                t.logs.set_wrap(on)
                if t.wrap.get_active() != on:
                    t.wrap.set_active(on)
