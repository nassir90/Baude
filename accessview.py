"""Who in the mesh may use what of a host's (baudelib.access; the access and access_set ops), as every app shows it:
Settings → Access (Baude's and Record's, for the host picked beside New session), a chat's Access… (Baude's session
row menu: its own rules for managing it), and Meta's rows (access_<rule>, each host's of the mesh), which Meta reads
and narrows and the user widens. Each capability is a row: its name, and a field like an email's To (RuleField) with
who may as pills: Everybody, the members, or none (Nobody); typing in it lists who may be added (a chat's has Same as
Host too). One split into read and write has a line for each, Read and Write, each with its rule's field. A change is
sent at once, and the row shows what the host answers (both of a split one's rules: the host keeps read letting in
whoever write does). The phone's Access.kt is the same."""
import threading
import time

from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk, Pango

from baudelib import access, wording

WHO = access.WHO


EVERYBODY, SAME = "*everybody", "*same"  # (a field's pills' keys beside the members' ids: Everybody, Same as Host)
AVATAR = 18  # px: a pill's picture
ROW_AVATAR = 24  # px: a picture in the list of who may be added


def keys_of(rule, chat=False):
    """A rule as its field's pills: [EVERYBODY] for everybody, [] for nobody, the members' ids for chosen ones; a
    chat's Same as Host (None, chat): [SAME]."""
    if rule is None and chat:
        return [SAME]
    who = (rule or {}).get("who") or "everybody"
    return [EVERYBODY] if who == "everybody" else list(rule.get("members") or ()) if who == "chosen" else []


def rule_of(keys):
    """The rule for a field's pills (keys_of's): None for Same as Host."""
    if SAME in keys:
        return None
    if EVERYBODY in keys:
        return {"who": "everybody"}
    return {"who": "chosen", "members": list(keys)} if keys else {"who": "nobody"}


def matches(typed, names):
    """The names (key -> name) typed picks out, in order: those starting with it, then those with it in them."""
    typed = typed.strip().lower()
    first = [k for k, n in names.items() if n.lower().startswith(typed)]
    return first + [k for k, n in names.items() if k not in first and typed in n.lower()]


class Pill(Gtk.Box):
    """One of a field's pills: a member's picture (its initials), or Everybody's or Same as Host's icon, the name, and
    × taking it out (remove(key); None: none)."""

    def __init__(self, key, name, remove=None):
        super().__init__(spacing=4, valign=Gtk.Align.CENTER, css_classes=["access-pill"])
        if key in (EVERYBODY, SAME):
            self.add_css_class("access-all")
            self.append(Gtk.Image(icon_name="system-users-symbolic" if key == EVERYBODY else "network-server-symbolic"))
        else:
            self.append(Adw.Avatar(size=AVATAR, text=name, show_initials=True, valign=Gtk.Align.CENTER))
        self.append(Gtk.Label(label=name, ellipsize=Pango.EllipsizeMode.END, max_width_chars=24))
        if remove is not None:
            x = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat", "circular"], can_focus=False,
                           tooltip_text=wording.say("common.remove"), valign=Gtk.Align.CENTER)
            x.connect("clicked", lambda _b: remove(key))
            self.append(x)
        else:
            self.add_css_class("fixed")


class Pills(Gtk.Widget):
    """A field's pills, then where more is typed (text): on one line as many as fit, and "<n> more" (more) for the
    rest, or, open, every one, wrapped onto more lines."""

    GAP = 4  # px between pills, and between lines
    TYPED = 48  # px left for typing at least

    def __init__(self, text, more):
        super().__init__(hexpand=True, valign=Gtk.Align.CENTER)
        self.pills, self.text, self.more, self.open = [], text, more, False
        text.set_parent(self)
        more.set_parent(self)

    def fill(self, pills):
        for p in self.pills:
            p.unparent()
        self.pills = list(pills)
        for p in self.pills:
            p.insert_before(self, self.text)
        self.queue_resize()

    def set_open(self, on):
        if on != self.open:
            self.open = on
            self.queue_resize()

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def width_of(self, w):
        return w.measure(Gtk.Orientation.HORIZONTAL, -1)[1] if w.get_visible() else 0

    def more_width(self, n):
        """How wide "<n> more" is."""
        label = self.more.get_child()
        said = wording.say("access.n_more", n=n)
        now = label.create_pango_layout(label.get_label()).get_pixel_size()[0]
        return self.more.measure(Gtk.Orientation.HORIZONTAL, -1)[1] - now + \
            label.create_pango_layout(said).get_pixel_size()[0]

    def place(self, width):
        """[(widget, x, line, w)] for width, the lines' count, and how many pills "<n> more" stands for."""
        typed = self.TYPED if self.text.get_visible() else 0
        out, x, line, hidden = [], 0, 0, 0
        widths = [self.width_of(p) for p in self.pills]
        if not self.open:
            room = width - (typed + self.GAP if typed else 0)
            if sum(widths) + self.GAP * len(widths) > room + self.GAP:
                for i, w in enumerate(widths):
                    left = len(widths) - i - 1
                    if i and x + w + (self.GAP + self.more_width(left) if left else 0) > room:
                        hidden = len(widths) - i
                        break
                    out.append((self.pills[i], x, 0, min(w, max(0, room - x))))
                    x += w + self.GAP
                if hidden:
                    w = self.more_width(hidden)
                    out.append((self.more, x, 0, w))
                    x += w + self.GAP
            else:
                for p, w in zip(self.pills, widths):
                    out.append((p, x, 0, w))
                    x += w + self.GAP
        else:
            for p, w in zip(self.pills, widths):
                if x and x + w > width:
                    x, line = 0, line + 1
                out.append((p, x, line, min(w, width)))
                x += w + self.GAP
        if typed:
            if x and x + typed > width:
                x, line = 0, line + 1
            out.append((self.text, x, line, max(typed, width - x)))
        return out, line + 1, hidden

    def line_height(self):
        return max([self.text.measure(Gtk.Orientation.VERTICAL, -1)[1]] +
                   [p.measure(Gtk.Orientation.VERTICAL, -1)[1] for p in self.pills + [self.more]])

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            least = self.TYPED if self.text.get_visible() else self.more_width(len(self.pills))
            natural = sum(self.width_of(p) + self.GAP for p in self.pills) + (self.TYPED if self.text.get_visible() else 0)
            return least, max(least, natural), -1, -1
        lines = self.place(for_size if for_size >= 0 else 100_000)[1]
        h = lines * self.line_height() + (lines - 1) * self.GAP
        return h, h, -1, -1

    def do_size_allocate(self, width, height, _baseline):
        placed, _lines, hidden = self.place(width)
        if hidden:
            said = wording.say("access.n_more", n=hidden)
            if self.more.get_child().get_label() != said:
                self.more.get_child().set_label(said)
        shown = {w for w, *_ in placed}
        for w in self.pills + [self.more]:
            w.set_child_visible(w in shown)
        lh = self.line_height()
        for w, x, line, ww in placed:
            h = w.measure(Gtk.Orientation.VERTICAL, ww)[1]
            y = line * (lh + self.GAP) + (lh - h) // 2
            w.allocate(ww, h, -1, Gsk.Transform().translate(Graphene.Point().init(x, y)))

    def do_dispose(self):
        for w in self.pills + [self.text, self.more]:
            w.unparent()
        self.pills = []
        Gtk.Widget.do_dispose(self)


class RuleField(Gtk.Box):
    """One rule's field (name: access.RULES'), as an email's To: who it lets in as pills, each with × (Everybody's
    one pill; none, Nobody, said where the typing goes), and typing lists who may be added, Everybody first, the
    members the host knows of after it, matched as they're typed: Enter or a click adds one (Everybody, or a chat's Same
    as Host, in place of the rest; a member in place of either). Past its width it shows what fits and "<n> more",
    which opens it out to every pill. As the host has it (show), set through send(rule, done) as it's changed: done(reply)
    with what the host answered. chat: a chat's own rule, Same as Host (rule None) a pill too."""

    def __init__(self, name, send, chat=False):
        super().__init__(css_classes=["access-field"], hexpand=True, valign=Gtk.Align.CENTER, sensitive=False)
        self.rule_name, self.send, self.chat, self.done = name, send, chat, None
        self.rule, self.host_rule, self.members, self.editable = None, None, [], False
        self.keys, self.names, self.sent = [], {}, 0
        self.text = Gtk.Text(hexpand=True, valign=Gtk.Align.CENTER)
        self.text.connect("changed", lambda _t: self.listing())
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.key)
        self.text.add_controller(keys)
        more = Gtk.Button(child=Gtk.Label(), css_classes=["access-more"], can_focus=False, valign=Gtk.Align.CENTER)
        more.connect("clicked", lambda _b: (self.pills.set_open(True), self.text.grab_focus()))
        self.pills = Pills(self.text, more)
        self.append(self.pills)
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", lambda _c: self.listing())
        focus.connect("leave", lambda _c: self.left())
        self.add_controller(focus)
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: self.editable and self.text.grab_focus())
        self.add_controller(click)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, activate_on_single_click=True,
                                can_focus=False, css_classes=["access-list"])
        self.list.connect("row-activated", lambda _l, row: self.add(row.key))
        self.pop = Gtk.Popover(autohide=False, has_arrow=False, can_focus=False, position=Gtk.PositionType.BOTTOM,
                               halign=Gtk.Align.START, css_classes=["access-pop"],
                               child=Gtk.ScrolledWindow(child=self.list, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                                        propagate_natural_height=True, propagate_natural_width=True,
                                                        max_content_height=320))
        from widgets import set_popover
        set_popover(self.pop, self)

    def show(self, rule, members, editable, host_rule=None):
        """As the host has it: rule (None: a chat's, the host's, host_rule), the members it can name, and whether it
        may be changed from here. While a change of its own is on its way, what it shows stays as changed."""
        self.rule, self.host_rule, self.members, self.editable = rule, host_rule, members, editable
        self.names = {m["id"]: m["name"] for m in members or ()}
        if not self.sent:
            self.keys = keys_of(rule, self.chat)
            self.drawn()
        self.set_sensitive(editable)

    def again(self):
        """As the host last had it."""
        self.sent = 0
        self.show(self.rule, self.members, self.editable, self.host_rule)

    def named(self, key):
        return wording.say("access.everybody") if key == EVERYBODY else wording.say("access.same_as_host") \
            if key == SAME else self.names.get(key, key[:8])

    def drawn(self):
        self.pills.fill([Pill(k, self.named(k), self.remove if self.editable else None) for k in self.keys])
        self.text.set_placeholder_text("" if self.keys else wording.say("access.nobody"))
        self.text.set_visible(self.editable or not self.keys)
        if self.pop.get_visible():
            self.listing()

    def offered(self):
        """Who may be added, key -> name, in order: Same as Host (a chat's), Everybody, then the members, but those
        it has."""
        out = {}
        if self.chat:
            out[SAME] = self.named(SAME)
        out[EVERYBODY] = self.named(EVERYBODY)
        out |= {k: n for k, n in self.names.items()}
        return {k: n for k, n in out.items() if k not in self.keys}

    def listing(self):
        """The list of who may be added, for what's typed, under the field (up while it has the focus)."""
        if not self.editable or not self.text.has_focus():
            return
        while (row := self.list.get_row_at_index(0)) is not None:
            self.list.remove(row)
        found = matches(self.text.get_text(), self.offered())
        for key in found:
            line = Gtk.Box(spacing=8, margin_start=4, margin_end=8, margin_top=3, margin_bottom=3)
            if key in (EVERYBODY, SAME):
                line.append(Gtk.Image(icon_name="system-users-symbolic" if key == EVERYBODY else
                                      "network-server-symbolic", pixel_size=16, width_request=ROW_AVATAR,
                                      height_request=ROW_AVATAR))
            else:
                line.append(Adw.Avatar(size=ROW_AVATAR, text=self.named(key), show_initials=True))
            line.append(Gtk.Label(label=self.named(key), xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
            row = Gtk.ListBoxRow(child=line, can_focus=False)
            row.key = key
            self.list.append(row)
        if not found and not self.names and not self.text.get_text():
            none = Gtk.ListBoxRow(child=Gtk.Label(label=wording.say("access.no_members"), xalign=0, margin_start=8,
                                                  margin_end=8, margin_top=4, margin_bottom=4,
                                                  css_classes=["dim-label"]), activatable=False, selectable=False)
            none.key = None
            self.list.append(none)
        self.list.select_row(self.list.get_row_at_index(0) if found and self.text.get_text().strip() else None)
        self.pop.get_child().set_size_request(max(240, min(self.get_width(), 360)), -1)
        if found or not self.names:
            self.pop.popup()
        else:
            self.pop.popdown()

    def key(self, _c, keyval, _code, _state):
        if keyval in (Gdk.KEY_Up, Gdk.KEY_Down) and self.pop.get_visible():
            row = self.list.get_selected_row()
            i = (row.get_index() if row else -1) + (1 if keyval == Gdk.KEY_Down else -1)
            if (to := self.list.get_row_at_index(max(0, i))) is not None and getattr(to, "key", None):
                self.list.select_row(to)
                ok, at = to.compute_bounds(self.list)
                if ok:  # (kept in sight as it's moved through)
                    adj = self.pop.get_child().get_vadjustment()
                    y, h = at.get_y(), at.get_height()
                    adj.set_value(min(max(adj.get_value(), y + h - adj.get_page_size()), y))
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_Tab) and self.pop.get_visible():
            row = self.list.get_selected_row()
            if row is not None and row.key:
                self.add(row.key)
                return True
            return keyval != Gdk.KEY_Tab
        if keyval == Gdk.KEY_Escape and self.pop.get_visible():
            self.pop.popdown()
            return True
        if keyval == Gdk.KEY_BackSpace and not self.text.get_text() and self.keys:
            self.remove(self.keys[-1])
            return True
        return False

    def left(self):
        if not self.text.has_focus():
            self.pop.popdown()
            self.pills.set_open(False)

    def add(self, key):
        """key (a member's id, EVERYBODY or SAME) let in: Everybody and Same as Host in place of all, a member in
        place of either."""
        if key is None:
            return
        self.text.set_text("")
        self.changed([key] if key in (EVERYBODY, SAME) else
                     [k for k in self.keys if k not in (EVERYBODY, SAME)] + [key])

    def remove(self, key):
        self.changed([k for k in self.keys if k != key])

    def changed(self, keys):
        self.keys = keys
        self.drawn()
        self.sent += 1

        def answered(reply):
            self.sent = max(0, self.sent - 1)
            return self.done(reply)
        self.send(rule_of(keys), answered)


class _Rules:
    """A capability's row: its rules' fields (buttons, by rule name), filled from the host's answer (answered)."""

    def made(self, cap, send, chat):
        self.cap, self.chat = cap, chat
        self.buttons = {}
        for name in access.rules_of(cap):
            b = self.buttons[name] = RuleField(name, lambda rule, done, name=name: send(name, rule, done), chat)
            b.done = self.answered
        return self.buttons

    def answered(self, reply):
        """The host's answer to access or access_set: each rule as it has it (all of a capability's rules or the row
        hidden: a host without them); an error said in a toast, the fields as they were."""
        if reply.get("op") == "error":
            from widgets import toast
            from baudelib import wire
            toast(self, wording.say("access.failed", why=wire.said(reply)))
            for b in self.buttons.values():
                b.again()
            return False
        state = reply["state"]
        has = all(state["rules"].get(name) is not None for name in self.buttons)
        self.set_visible(has)
        if has:
            own = reply.get("chat") or {}
            checked = access.chat_rules(own, state["rules"]) if self.chat else state["rules"]  # (a chat's: as it's
            for name, b in self.buttons.items():  # checked, its write within its read)
                b.show((checked[name] if name in own else None) if self.chat else checked[name], state["members"],
                       state["editable"], checked[name])
        return False


class RuleRow(_Rules, Adw.PreferencesRow):
    """A capability's row (cap): its name, then its rule's field beside it (a chat's: under it), or, split into read
    and write, a line for each, Read and Write, with its rule's field. send(name, rule, done), as RuleField's. columns: the size groups
    (the names', the Read and Write labels') lining the fields up under each other, a page's rows'."""

    NAME_CHARS = 24  # (its name kept whole up to this long, however much its fields want)

    def __init__(self, cap, send, chat=False, columns=None):
        Adw.PreferencesRow.__init__(self, title=access.named(cap), activatable=False)
        box = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6, margin_start=12, margin_end=8,
                      orientation=Gtk.Orientation.VERTICAL if chat else Gtk.Orientation.HORIZONTAL)
        self.heading = Gtk.Label(label=GLib.markup_escape_text(access.named(cap)), use_markup=True, xalign=0,
                                 valign=Gtk.Align.START, ellipsize=Pango.EllipsizeMode.END, margin_top=0 if chat else 8,
                                 width_chars=min(len(access.named(cap)), self.NAME_CHARS))
        box.append(self.heading)
        lines = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        split = cap in access.SPLIT
        for name, field in self.made(cap, send, chat).items():
            line = Gtk.Box(spacing=8)
            side = Gtk.Label(label=wording.say("access.read" if name.endswith("_read") else "access.write")
                             if split else "", xalign=0, css_classes=["dim-label"])
            if split or columns:
                line.append(side)
            if columns:
                columns[1].add_widget(side)
            line.append(field)
            lines.append(line)
        box.append(lines)
        if columns:
            columns[0].add_widget(self.heading)
        self.set_child(box)

    def set_title(self, title):
        """title (markup, as an ActionRow's) as its name."""
        Adw.PreferencesRow.set_title(self, title)
        self.heading.set_label(title)
        self.heading.set_width_chars(min(len(self.heading.get_text()), self.NAME_CHARS))


def row(cap, send, chat=False, columns=None):
    """cap's row (RuleRow)."""
    return RuleRow(cap, send, chat, columns)


def ask(where, msg, done):
    """A request to the host where is: done(reply) on the main thread."""
    import depsview
    depsview.ask(where, done, msg)


class Page(Adw.PreferencesPage):
    """Settings → Access: the host picked beside New session (as Telemetry has it), then a row for each of its
    capabilities, their names in one column, Read and Write in the next, the fields filling the rest."""

    def __init__(self, win):
        super().__init__(title=wording.say("access.title"), icon_name="dialog-password-symbolic")
        from widgets import walk
        for clamp in walk(self):  # (as widgets.settings_page: rows as wide as the dialog)
            if isinstance(clamp, Adw.Clamp):
                clamp.set_maximum_size(100_000)
                clamp.set_tightening_threshold(100_000)
        self.where, host = win.terminal_host()
        top = Adw.PreferencesGroup()
        name = Adw.ActionRow(title=wording.say("new_session.host"))
        name.add_suffix(Gtk.Label(label=host, css_classes=["dim-label"]))
        top.add(name)
        self.add(top)
        self.group = Adw.PreferencesGroup()
        columns = (Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL), Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL))
        self.rows = {cap: row(cap, lambda name, rule, done: ask(
            self.where, {"op": "access_set", "capability": name, "rule": rule}, done), columns=columns)
            for cap in access.CAPABILITIES}
        for shown in self.rows.values():
            self.group.add(shown)
        self.add(self.group)
        self.error = None
        ask(self.where, {"op": "access"}, self.took)

    def took(self, reply):
        if "op" not in reply or reply["op"] == "error":
            from baudelib import wire
            text = wire.said(reply) if "op" in reply else reply.get("error") or ""
            self.error = Adw.ActionRow(title=GLib.markup_escape_text(text), sensitive=False)
            self.group.add(self.error)
            return False
        for shown in self.rows.values():
            shown.answered(reply)
        return False


def chat_dialog(parent, remote, session, title):
    """A chat's Access…: its own rules for managing it, read and write (Same as Host: the host's), on remote (its
    host's client.Remote), changed as they're picked."""
    group = Adw.PreferencesGroup()
    rules = row("manage", lambda name, rule, done: remote.request(
        {"op": "access_set", "session": session, "capability": name, "rule": rule}, done), chat=True)
    group.add(rules)
    dlg = Adw.AlertDialog(heading=wording.say("access.chat_access"), body=GLib.markup_escape_text(title or ""),
                          extra_child=group, close_response="close")
    dlg.add_response("close", wording.say("common.close"))
    dlg.set_size_request(420, -1)
    remote.request({"op": "access", "session": session}, rules.answered)
    dlg.present(parent)
    return dlg


# ---- Meta's rows: the rules of each host of the mesh, which Meta reads, narrows, and shows for the user to widen

_KNOWN = {}  # (host id -> {"name", "where", "state"}: each one's rules as it last said them)
_LOOK = {"at": -1e9, "going": False}
_KNOWN_LOCK = threading.Lock()
KNOWN_S = 5
ORDER = {"nobody": 0, "chosen": 1, "everybody": 2}


def hosts():
    """[(id, name, where)]: this machine's host and the members it reaches, as last looked (look)."""
    with _KNOWN_LOCK:
        return [(k, v["name"], v["where"]) for k, v in _KNOWN.items()]


def look(force=False):
    """The hosts' rules asked for again, off the main loop, if they're KNOWN_S old (or force): never waited for."""
    with _KNOWN_LOCK:
        if _LOOK["going"] or not force and time.monotonic() - _LOOK["at"] < KNOWN_S:
            return
        _LOOK["going"] = True

    def run():
        from baudelib import wire
        try:
            ch, info = wire.reach({"via": "local"}, timeout=2)
            ch.close()
            found = [(info.get("id"), info.get("name"), {"via": "local"})] + [
                (m.get("id"), m.get("name"), {"via": "mesh", "host": m.get("id")})
                for m in (info.get("mesh") or {}).get("members") or [] if wire.is_id(m.get("id") or "")]
        except (OSError, ValueError):
            found = []
        got = {}
        for hid, name, where in found:
            try:
                ch, _ = wire.reach(where, timeout=4)
                try:
                    ch.send({"op": "access", "req": 1})
                    while (r := ch.reply()) is not None and r.get("req") != 1:
                        pass
                finally:
                    ch.close()
            except OSError:
                continue
            if r and r.get("op") == "access":
                got[hid] = {"name": r["state"]["host"] or name, "where": where, "state": r["state"]}
        with _KNOWN_LOCK:
            _KNOWN.clear()
            _KNOWN.update(got)
            _LOOK.update(at=time.monotonic(), going=False)
    threading.Thread(target=run, daemon=True, name="access-look").start()


def rule_said(state, cap):
    """Rule cap's (access.RULES') in a host's state, as Meta reads it: everybody, nobody, or chosen: the members' names."""
    rule = state["rules"].get(cap)
    if rule is None:
        return "unknown"
    if rule["who"] != "chosen":
        return rule["who"]
    names = {m["id"]: m["name"] for m in state["members"]}
    return "chosen: " + ", ".join(names.get(m, m[:8]) for m in rule.get("members") or ())


def said(cap):
    """cap's rule on each host, as last looked: "<host>: <rule>; ...", or unknown (looked for now)."""
    look()
    with _KNOWN_LOCK:
        known = [(v["name"], v["state"]) for v in _KNOWN.values()]
    return "; ".join(f"{name}: {rule_said(st, cap)}" for name, st in known) or "unknown"


def narrower(new, old):
    """Does new let in no one old doesn't?"""
    if ORDER[new["who"]] != ORDER[old["who"]]:
        return ORDER[new["who"]] < ORDER[old["who"]]
    return new["who"] != "chosen" or set(new.get("members") or ()) <= set(old.get("members") or ())


def parse(value, cap):
    """(host's (id, name, where) or None for this machine's, rule) of what Meta gave: "[<host>:] <who>[: names]"
    (unknown: rule None, that host's left as it is)."""
    parts = [p.strip() for p in str(value).split(":")]
    known = hosts()
    host = None
    if parts and parts[0].lower() not in WHO + ("unknown",):
        host = next((h for h in known if h[1].lower() == parts[0].lower() or h[0] == parts[0]), None)
        if host is None:
            raise ValueError(f"No host {parts[0]!r}: they're {', '.join(h[1] for h in known) or 'not known yet'}.")
        parts = parts[1:]
    who = (parts[0] if parts else "").lower()
    if who == "unknown":
        return host, None
    if who not in WHO:
        raise ValueError(f"access_{cap} is [<host>:] everybody, nobody, or chosen: <members>.")
    rule = {"who": who}
    if who == "chosen":
        state = next((v["state"] for k, v in _KNOWN.items() if host and k == host[0] or not host and
                      v["where"].get("via") == "local"), None) or {"members": []}
        ids = {m["name"].lower(): m["id"] for m in state["members"]}
        names = [n.strip() for n in ":".join(parts[1:]).split(",") if n.strip()]
        if any(n.lower() not in ids for n in names):
            raise ValueError(f"Members are named as {', '.join(m['name'] for m in state['members']) or 'none here'}.")
        rule["members"] = [ids[n.lower()] for n in names]
    return host, rule


def narrow(cap, value):
    """(off the main loop) cap's rule on a host made narrower, as Meta asked: ValueError for one that'd let more in."""
    from baudelib import wire
    host, rule = parse(value, cap)
    where = host[2] if host else {"via": "local"}

    def ask(msg):
        ch, _ = wire.reach(where, timeout=4)
        try:
            ch.send({**msg, "req": 1})
            while (r := ch.reply()) is not None and r.get("req") != 1:
                pass
        finally:
            ch.close()
        if not r or r.get("op") == "error":
            raise ValueError(wire.said(r) or "the host didn't answer")
        return r
    state = ask({"op": "access"})["state"]
    old = state["rules"].get(cap)
    if rule is None or old is None:
        return f"{state['host']}: {rule_said(state, cap)}"
    if not narrower(rule, old):
        raise ValueError(f"Only the user widens a rule: show setting:access_{cap} for them to.")
    state = ask({"op": "access_set", "capability": cap, "rule": rule})["state"]
    look(force=True)
    return f"{state['host']}: {rule_said(state, cap)}"


def host_row(name, hid, where):
    """Rule name's row for a host of the mesh (its capability's, both rules of a split one), filled as it answers;
    what's set there read again by Meta's next look."""
    cap = access.capability(name)

    def send(name, rule, done):
        def answered(reply):
            look(force=True)
            return done(reply)
        ask(where, {"op": "access_set", "capability": name, "rule": rule}, answered)
    shown = row(cap, send)

    def took(reply):
        if reply.get("state"):
            shown.set_title(GLib.markup_escape_text(wording.say("access.setting", host=reply["state"]["host"],
                                                                capability=access.named(cap))))
            shown.answered(reply)
        return False
    ask(where, {"op": "access"}, took)
    return shown


def meta_settings():
    """Meta's settings for the mesh's hosts' rules (access_<rule>: each of access.RULES, a split capability's read
    and write each a setting): read on every host, narrowed by Meta (narrow), widened only by the user, on the rows
    shown (one a host: its capability's, both rules of a split one)."""
    import meta

    class AccessSetting(meta.Setting):
        later = True  # (set off the main loop: metatools' set_setting)

        @property
        def title(self):
            return access.named(self.cap)

        @title.setter
        def title(self, _value):
            pass

        def coerce(self, value):
            parse(value, self.cap)
            return str(value).strip()

        def where(self, value):
            """The host value sets a rule on, as wire reaches it."""
            host, _rule = parse(value, self.cap)
            return host[2] if host else {"via": "local"}

        def host_row(self, where):
            """The row of the host a change was on, alone (set_setting's)."""
            return [host_row(self.cap, None, where)]

    out = []
    for cap in access.RULES:
        s = AccessSetting(f"access_{cap}", None, lambda cap=cap: said(cap), lambda v, cap=cap: narrow(cap, v),
                          lambda cap=cap: [host_row(cap, hid, where) for hid, _name, where in hosts()] or
                          [host_row(cap, None, {"via": "local"})], list(WHO),
                          note="who may use it on each host of the mesh, as [<host>:] everybody, nobody or chosen: "
                          "<members>; you may narrow a rule, the user widens one on its row" +
                          ("; who may write may read too" if cap != access.capability(cap) else ""))
        s.cap = cap
        out.append(s)
    return {s.name: s for s in out}
