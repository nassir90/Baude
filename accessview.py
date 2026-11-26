"""Who in the mesh may use what of a host's (baudelib.access; the access and access_set ops), as every app shows it:
Settings → Access (Baude's and Record's, for the host picked beside New session), a chat's Access… (Baude's session
row menu: its own rule for managing it), and Meta's rows (access_<capability>, each host's of the mesh), which Meta
reads and narrows and the user widens. Each capability is a row: its name, a dropdown (Everybody, Nobody, Chosen
Members; a chat's has Same as Host first), and, for Chosen Members, a switch for each member the host knows of. A
change is sent at once, and the row shows what the host answers. The phone's Access.kt is the same."""
import threading
import time

from gi.repository import Adw, GLib, Gtk

from baudelib import access, wording

WHO = access.WHO
SAME = "same"  # (a chat's: the host's rule)


def who_said(who):
    return wording.say("access.same_as_host") if who == SAME else wording.say(f"access.{who}")


class AccessRow(Adw.ExpanderRow):
    """One capability's rule (cap), shown as the host has it (show) and set through send(rule, done) as it's changed:
    done(reply) with what the host answered. chat: a chat's own rule, Same as Host first (rule None)."""

    def __init__(self, cap, send, chat=False):
        super().__init__(title=access.named(cap), enable_expansion=False)
        self.cap, self.send, self.chat = cap, send, chat
        self.choices = ([SAME] if chat else []) + list(WHO)
        self.drop = Gtk.DropDown.new_from_strings([who_said(w) for w in self.choices])
        self.drop.set_valign(Gtk.Align.CENTER)
        self.drop.set_sensitive(False)
        self.drop.connect("notify::selected", lambda *_: self.picked())
        self.add_suffix(self.drop)
        self.switches, self.taking, self.rule, self.members = {}, False, None, []

    def show(self, rule, members, editable):
        """As the host has it: rule (None: a chat's, the host's), the members it can name, and whether it may be
        changed from here."""
        self.taking = True
        self.rule, self.members = rule, members
        who = (rule or {}).get("who") or (SAME if self.chat else "everybody")
        self.drop.set_selected(self.choices.index(who))
        self.drop.set_sensitive(editable)
        chosen = set((rule or {}).get("members") or ())
        for row in self.switches.values():
            self.remove(row)
        self.switches = {}
        for m in members or [{"id": None, "name": wording.say("access.no_members")}]:
            row = Adw.SwitchRow(title=GLib.markup_escape_text(m["name"]), active=m["id"] in chosen,
                                sensitive=editable and m["id"] is not None)
            row.connect("notify::active", lambda *_: self.picked())
            self.add_row(row)
            self.switches[m["id"]] = row
        self.set_enable_expansion(who == "chosen")
        self.taking = False

    def picked(self):
        if self.taking:
            return
        who = self.choices[self.drop.get_selected()]
        if who == "chosen" and not self.get_expanded():
            self.set_enable_expansion(True)
            self.set_expanded(True)
        rule = None if who == SAME else {"who": who, **({"members": [k for k, r in self.switches.items()
                                                                     if k and r.get_active()]}
                                                        if who == "chosen" else {})}
        self.drop.set_sensitive(False)
        self.send(rule, self.answered)

    def answered(self, reply):
        if reply.get("op") == "error":
            from widgets import toast
            from baudelib import wire
            toast(self, wording.say("access.failed", why=wire.said(reply)))
            self.show(self.rule, self.members, True)
            return False
        state = reply["state"]
        self.show(reply.get("chat") if self.chat else state["rules"][self.cap], state["members"], state["editable"])
        return False


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
        self.rows = {cap: AccessRow(cap, lambda rule, done, cap=cap: ask(
            self.where, {"op": "access_set", "capability": cap, "rule": rule}, done)) for cap in access.CAPABILITIES}
        for row in self.rows.values():
            self.group.add(row)
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
        for row in self.rows.values():
            row.answered(reply)
        return False


def chat_dialog(parent, remote, session, title):
    """A chat's Access…: its own rule for managing it (Same as Host: the host's), on remote (its host's
    client.Remote), changed as it's picked."""
    group = Adw.PreferencesGroup()
    row = AccessRow("manage", lambda rule, done: remote.request(
        {"op": "access_set", "session": session, "rule": rule}, done), chat=True)
    group.add(row)
    dlg = Adw.AlertDialog(heading=wording.say("access.chat_access"), body=GLib.markup_escape_text(title or ""),
                          extra_child=group, close_response="close")
    dlg.add_response("close", wording.say("common.close"))
    dlg.set_size_request(420, -1)
    remote.request({"op": "access", "session": session}, row.answered)
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
    """cap's rule in a host's state, as Meta reads it: everybody, nobody, or chosen: the members' names."""
    rule = state["rules"][cap]
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
    """(host's (id, name, where) or None for this machine's, rule) of what Meta gave: "[<host>:] <who>[: names]"."""
    parts = [p.strip() for p in str(value).split(":")]
    known = hosts()
    host = None
    if parts and parts[0].lower() not in WHO:
        host = next((h for h in known if h[1].lower() == parts[0].lower() or h[0] == parts[0]), None)
        if host is None:
            raise ValueError(f"No host {parts[0]!r}: they're {', '.join(h[1] for h in known) or 'not known yet'}.")
        parts = parts[1:]
    who = (parts[0] if parts else "").lower()
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
    old = ask({"op": "access"})["state"]["rules"][cap]
    if not narrower(rule, old):
        raise ValueError(f"Only the user widens a rule: show setting:access_{cap} for them to.")
    state = ask({"op": "access_set", "capability": cap, "rule": rule})["state"]
    look(force=True)
    return f"{state['host']}: {rule_said(state, cap)}"


def host_row(cap, hid, where):
    """cap's row for a host of the mesh, filled as it answers; what's set there read again by Meta's next look."""
    def send(rule, done):
        def answered(reply):
            look(force=True)
            return done(reply)
        ask(where, {"op": "access_set", "capability": cap, "rule": rule}, answered)
    row = AccessRow(cap, send)

    def took(reply):
        if reply.get("state"):
            row.set_title(GLib.markup_escape_text(wording.say("access.setting", host=reply["state"]["host"],
                                                              capability=access.named(cap))))
            row.answered(reply)
        return False
    ask(where, {"op": "access"}, took)
    return row


def meta_settings():
    """Meta's settings for the mesh's hosts' rules (access_<capability>): read on every host, narrowed by Meta
    (narrow), widened only by the user, on the rows shown (one a host)."""
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

    out = []
    for cap in access.CAPABILITIES:
        s = AccessSetting(f"access_{cap}", None, lambda cap=cap: said(cap), lambda v, cap=cap: narrow(cap, v),
                          lambda cap=cap: [host_row(cap, hid, where) for hid, _name, where in hosts()] or
                          [host_row(cap, None, {"via": "local"})], list(WHO),
                          note="who may use it on each host of the mesh, as [<host>:] everybody, nobody or chosen: "
                          "<members>; you may narrow a rule, the user widens one on its row")
        s.cap = cap
        out.append(s)
    return {s.name: s for s in out}
