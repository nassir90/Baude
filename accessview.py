"""Who in the mesh may use what of a host's (baudelib.access; the access and access_set ops), as every app shows it:
Settings → Access (Baude's and Record's, for the host picked beside New session), a chat's Access… (Baude's session
row menu: its own rules for managing it), and Meta's rows (access_<rule>, each host's of the mesh), which Meta reads
and narrows and the user widens. Each capability is a row: its name, and a button naming who may (Everybody, Nobody,
the members) that opens a check for each member the host knows of and one over them all, as the sidebar's Groups have
them: every one checked is Everybody, none Nobody, some those members (a chat's has Same as Host above them). One split
into read and write has its name over two lines, Read and Write, each with its rule's button. A change is sent at once,
and the row shows what the host answers (both of a split one's rules: the host keeps read letting in whoever write
does). The phone's Access.kt is the same."""
import threading
import time

from gi.repository import Adw, GLib, Gtk, Pango

from baudelib import access, wording

WHO = access.WHO


def shown_of(rule, members=()):
    """The members a rule lets in, as a CheckList has them: None for everybody (or a rule naming every one of
    members), [] for nobody."""
    who = (rule or {}).get("who") or "everybody"
    chosen = list((rule or {}).get("members") or ()) if who == "chosen" else []
    return None if who == "everybody" or chosen and members and set(members) <= set(chosen) else chosen


def rule_of(shown):
    """The rule for the members checked (shown_of's)."""
    return {"who": "everybody"} if shown is None else {"who": "chosen", "members": list(shown)} if shown else \
        {"who": "nobody"}


def summary(shown, names):
    """Who a rule lets in, in a few words: Everybody; Nobody; studio; studio, pixel-9; 3 members."""
    if shown is None:
        return wording.say("access.everybody")
    if not shown:
        return wording.say("access.nobody")
    return ", ".join(names.get(m, m[:8]) for m in [m for m in names if m in shown] +
                     [m for m in shown if m not in names]) if len(shown) <= 2 else \
        wording.say("access.n_members", n=len(shown))


class RuleButton(Gtk.MenuButton):
    """One rule's button (name: access.RULES'), naming who it lets in and opening the checklist, as the host has it
    (show) and set through send(rule, done) as it's changed: done(reply) with what the host answered. chat: a chat's
    own rule, Same as Host above the members (rule None)."""

    def __init__(self, name, send, chat=False):
        from pickers import CheckList
        self.label = Gtk.Label(ellipsize=Pango.EllipsizeMode.END, max_width_chars=24)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        super().__init__(child=self.label, always_show_arrow=True, valign=Gtk.Align.CENTER,
                         popover=Gtk.Popover(child=box), sensitive=False)
        self.rule_name, self.send, self.chat, self.done = name, send, chat, None
        self.same = None
        if chat:
            self.same = Gtk.CheckButton(label=wording.say("access.same_as_host"))
            self.same.connect("toggled", lambda b: self.taking or self.picked_same(b.get_active()))
            box.append(self.same)
            box.append(Gtk.Separator())
        self.pick = CheckList(wording.say("access.everybody"), lambda shown: self.taking or self.picked(shown))
        box.append(self.pick)
        self.none = Gtk.Label(label=wording.say("access.no_members"), xalign=0, css_classes=["dim-label"],
                              visible=False)
        box.append(self.none)
        self.taking, self.rule, self.host_rule, self.members, self.editable = False, None, None, [], False

    def show(self, rule, members, editable, host_rule=None):
        """As the host has it: rule (None: a chat's, the host's, host_rule), the members it can name, and whether it
        may be changed from here."""
        self.taking = True
        self.rule, self.host_rule, self.members, self.editable = rule, host_rule, members, editable
        same = self.chat and rule is None
        names = {m["id"]: m["name"] for m in members or ()}
        shown = shown_of(host_rule if same else rule, names)
        self.pick.fill([(m["id"], m["name"]) for m in members or ()] +
                       [(k, k[:8]) for k in shown or () if k not in names], shown)
        self.none.set_visible(not members)
        if self.same is not None:
            self.same.set_active(same)
        self.label.set_label(wording.say("access.same_as_host") if same else summary(shown, names))
        self.pick.set_sensitive(True)
        if self.same is not None:
            self.same.set_sensitive(True)
        self.set_sensitive(editable)
        self.taking = False

    def again(self):
        """As the host last had it."""
        self.show(self.rule, self.members, self.editable, self.host_rule)

    def picked(self, shown):
        self.sending(rule_of(shown))

    def picked_same(self, on):
        self.sending(None if on else dict(self.host_rule or {"who": "everybody"}))

    def sending(self, rule):
        self.pick.set_sensitive(False)
        if self.same is not None:
            self.same.set_sensitive(False)
        self.send(rule, self.done)


class _Rules:
    """A capability's row: its rules' buttons (buttons, by rule name), filled from the host's answer (answered)."""

    def made(self, cap, send, chat):
        self.cap, self.chat = cap, chat
        self.buttons = {}
        for name in access.rules_of(cap):
            b = self.buttons[name] = RuleButton(name, lambda rule, done, name=name: send(name, rule, done), chat)
            b.done = self.answered
        return self.buttons

    def answered(self, reply):
        """The host's answer to access or access_set: each rule as it has it (all of a capability's rules or the row
        hidden: a host without them); an error said in a toast, the buttons as they were."""
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
            for name, b in self.buttons.items():
                host_rule = state["rules"][name]
                b.show((reply.get("chat") or {}).get(name) if self.chat else host_rule, state["members"],
                       state["editable"], host_rule)
        return False


class AccessRow(_Rules, Adw.ActionRow):
    """A capability with one rule (cap): its name and the rule's button. send(name, rule, done), as RuleButton's."""

    def __init__(self, cap, send, chat=False):
        Adw.ActionRow.__init__(self, title=access.named(cap))
        button = next(iter(self.made(cap, send, chat).values()))
        self.add_suffix(button)
        self.set_activatable_widget(button)


class SplitRow(_Rules, Adw.PreferencesRow):
    """A capability split into read and write (cap): its name, then a line for each, Read and Write, with its rule's
    button. send(name, rule, done), as RuleButton's."""

    def __init__(self, cap, send, chat=False):
        Adw.PreferencesRow.__init__(self, title=access.named(cap), activatable=False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=8, margin_bottom=8, margin_start=12,
                      margin_end=12)
        self.heading = Gtk.Label(label=GLib.markup_escape_text(access.named(cap)), use_markup=True, xalign=0,
                                 ellipsize=Pango.EllipsizeMode.END, margin_bottom=2)
        box.append(self.heading)
        for name, button in self.made(cap, send, chat).items():
            line = Gtk.Box(spacing=6)
            line.append(Gtk.Label(label=wording.say("access.read" if name.endswith("_read") else "access.write"),
                                  xalign=0, hexpand=True, css_classes=["dim-label"], margin_start=12))
            line.append(button)
            box.append(line)
        self.set_child(box)

    def set_title(self, title):
        """title (markup, as an ActionRow's) as its name."""
        Adw.PreferencesRow.set_title(self, title)
        self.heading.set_label(title)


def row(cap, send, chat=False):
    """cap's row: SplitRow for one split into read and write, else AccessRow."""
    return (SplitRow if cap in access.SPLIT else AccessRow)(cap, send, chat)


def ask(where, msg, done):
    """A request to the host where is: done(reply) on the main thread."""
    import depsview
    depsview.ask(where, done, msg)


class Page(Adw.PreferencesPage):
    """Settings → Access: the host picked beside New session (as Telemetry has it), then a row for each of its
    capabilities."""

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
        self.rows = {cap: row(cap, lambda name, rule, done: ask(
            self.where, {"op": "access_set", "capability": name, "rule": rule}, done)) for cap in access.CAPABILITIES}
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
