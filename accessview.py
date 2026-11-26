"""Who in the mesh may use what of a host's (baudelib.access; the access and access_set ops), as every app shows it:
Settings → Access (Baude's and Record's, for the host picked beside New session), a chat's Access… (Baude's session
row menu: its own rule for managing it), and Meta's rows (access_<capability>, this machine's host's), which Meta shows
and the user changes. Each capability is a row: its name, a dropdown (Everybody, Nobody, Chosen Members; a chat's has
Same as Host first), and, for Chosen Members, a switch for each member the host knows of. A change is sent at once,
and the row shows what the host answers. The phone's Access.kt is the same."""
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


# ---- Meta's rows: this machine's host's rules, which Meta shows and the user changes

_KNOWN = {"at": 0.0, "state": None}
_KNOWN_LOCK = threading.Lock()
KNOWN_S = 5


def known():
    """This machine's host's rules as it last said them (asked again once they're KNOWN_S old; None if it isn't
    running or can't say)."""
    with _KNOWN_LOCK:
        if time.monotonic() - _KNOWN["at"] > KNOWN_S:
            import themelook
            reply = themelook.ask_host({"op": "access"}, timeout=2, start=False)
            _KNOWN.update(at=time.monotonic(), state=reply.get("state"))
        return _KNOWN["state"]


def said(cap):
    """cap's rule here, as Meta reads it: everybody, nobody, or chosen: the members' names."""
    state = known()
    if state is None:
        return "unknown"
    rule = state["rules"][cap]
    if rule["who"] != "chosen":
        return rule["who"]
    names = {m["id"]: m["name"] for m in state["members"]}
    return "chosen: " + ", ".join(names.get(m, m[:8]) for m in rule.get("members") or ())


def local_row(cap):
    """cap's row for this machine's host, filled as it answers; what's set there read again by Meta's next look."""
    where = {"via": "local"}

    def send(rule, done):
        _KNOWN["at"] = 0.0
        ask(where, {"op": "access_set", "capability": cap, "rule": rule}, done)
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
    """Meta's settings for this machine's host's rules (access_<capability>): shown, the user's to change."""
    import meta

    class AccessSetting(meta.Setting):
        @property
        def title(self):
            name = (known() or {}).get("host") or wording.say("new_session.host")
            return wording.say("access.setting", host=name, capability=access.named(self.cap))

        @title.setter
        def title(self, _value):
            pass

    out = []
    for cap in access.CAPABILITIES:
        def put(_v):
            raise ValueError("the user's to change: show its row")
        s = AccessSetting(f"access_{cap}", None, lambda cap=cap: said(cap), put, lambda cap=cap: local_row(cap),
                          list(WHO), note="who in the mesh may use it on this machine's host; the user's to change",
                          meta_sets=False)
        s.cap = cap
        out.append(s)
    return {s.name: s for s in out}
