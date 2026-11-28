"""Settings → Dependencies' Deciders group (Baude's and Record's; the phone's Deciders.kt the same): the decision models'
servers the host picked beside New session knows (baudelib/deciders.py), each with whether it answers, Test and Remove,
then a name and a URL to add one (its deciders op)."""
from gi.repository import Adw, GLib, Gtk, Pango

from baudelib import wording


def status(d):
    """What a decider's row says of it: Running at its URL, or why it isn't answering."""
    if d.get("reachable"):
        return wording.say("deciders.running", url=d.get("url") or "")
    why = d.get("error") or ""
    return why[:1].upper() + why[1:]


class Group(Adw.PreferencesGroup):
    """The host's deciders (where: wire.reach's), asked for as it's made."""

    def __init__(self, where):
        super().__init__(title=wording.say("deciders.title"))
        self.where, self.rows, self.loaded = where, [], False
        self.name = Adw.EntryRow(title=wording.say("common.name"))
        self.url = Adw.EntryRow(title=wording.say("deciders.url"), text="http://127.0.0.1:8000")
        self.url.connect("entry-activated", lambda *_: self.add_one())
        self.adding = Adw.ButtonRow(title=wording.say("deciders.add"), start_icon_name="list-add-symbolic")
        self.adding.connect("activated", lambda *_: self.add_one())
        for r in (self.name, self.url, self.adding):
            self.add(r)
        self.ask({"op": "deciders"})

    def ask(self, msg):
        import depsview
        depsview.ask(self.where, self.took, msg)

    def took(self, reply):
        if reply.get("op") != "deciders" and self.loaded:  # (a change refused: why, the list kept)
            return self.toast(reply.get("error") or "")
        self.loaded = reply.get("op") == "deciders"
        for r in self.rows:
            self.remove(r)
        self.rows = []
        if reply.get("op") != "deciders":  # (the host couldn't be asked)
            self.rows.append(Adw.ActionRow(title=GLib.markup_escape_text(reply.get("error") or ""), sensitive=False))
        elif reply.get("error"):  # (an add that wouldn't do: why, over Settings)
            self.toast(reply["error"])
        elif reply.get("what") == "add":
            self.name.set_text("")
        self.rows += [self.row(d) for d in reply.get("deciders") or [] if d.get("host") is None]
        for i, r in enumerate(self.rows):  # (put in over Name, which with URL stays where it is: what's being typed
            # there keeps its place, and the page where it's scrolled to)
            self.name.get_parent().insert(r, i)
        return False

    def toast(self, text):
        dialog = self.get_ancestor(Adw.PreferencesDialog)
        if dialog is not None:
            dialog.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=3))
        return False

    def row(self, d):
        out = Adw.ActionRow(title=GLib.markup_escape_text(d.get("name") or d["id"]))
        out.add_suffix(Gtk.Label(label=status(d), css_classes=["dim-label"], ellipsize=Pango.EllipsizeMode.MIDDLE,
                                 max_width_chars=48, tooltip_text=d.get("model") or None))
        test = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text=wording.say("deciders.test"),
                          valign=Gtk.Align.CENTER, css_classes=["flat"])
        test.connect("clicked", lambda *_: self.ask({"op": "deciders", "what": "test", "id": d["id"]}))
        out.add_suffix(test)
        remove = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text=wording.say("common.remove"),
                            valign=Gtk.Align.CENTER, css_classes=["flat"])
        remove.connect("clicked", lambda *_: self.ask({"op": "deciders", "what": "remove", "id": d["id"]}))
        out.add_suffix(remove)
        return out

    def add_one(self):
        self.ask({"op": "deciders", "what": "add", "name": self.name.get_text().strip(),
                  "url": self.url.get_text().strip()})
