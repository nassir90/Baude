"""Settings → Subscriptions (Baude's and Record's; the phone's SubscriptionsScreen the same): the Claude and Codex
subscription accounts kept on the host picked beside New session (win.terminal_host, as ☰ → Terminal, Kubernetes and
Database Schemas are), changed there through its accounts op (hostaccounts): each provider's Account picker (the
default a new chat takes), its accounts with Rename…, Replace Token… (Claude) or Sign In Again… (Codex) and Remove…,
Subscription usage (Claude) and Add account. Also the chat's Sign in card's token box for a chat under a Claude
account (replace_token)."""
import os
import threading

from gi.repository import Adw, Gio, GLib, Gtk

from baudelib import icons, wire, wording

PROVIDERS = (("claude", "report.claude", "claude"), ("codex", "programs.codex", "openai"))


class Ask:
    """One accounts request to a host (where), on a connection of its own (through the mesh, if that's how it's
    reached): answered(reply) once its answer comes (an error's too, as {"error": …}); heard(line) for each line a
    Codex sign-in writes. stop() lets go of the connection, which stops a sign-in there. On the main thread."""

    def __init__(self, where, msg, answered, heard=None):
        self.ch, self.stopped = None, False
        threading.Thread(target=self._run, args=(where, {"op": "accounts", "req": 1, **msg}, answered, heard),
                         daemon=True, name="accounts").start()

    def _run(self, where, msg, answered, heard):
        from baudelib import hosts
        try:
            if where.get("via") == "local" and not hosts.start_here():  # (this machine's: started if need be)
                raise OSError(wording.say("host_status.none_started", log=hosts.log_path()))
            ch, _info = wire.reach(where, timeout=10)
        except (OSError, ValueError) as e:
            return self._end(answered, {"error": str(e) or wording.say("host_status.no_answer")})
        self.ch = ch
        if self.stopped:
            return ch.close()
        ch.sock.settimeout(wire.SILENT_S)
        ch.keep_alive()
        ch.send(msg)
        end = None
        for m in ch:
            if m.get("req") != 1:
                continue
            if m.get("op") == "accounts" and "line" in m and "accounts" not in m:
                if heard is not None and not self.stopped:
                    GLib.idle_add(heard, m["line"])
                continue
            end = m if m.get("op") == "accounts" else {"error": wire.said(m)}
            break
        ch.close()
        self._end(answered, end or {"error": wording.say("host_status.connection_dropped")})

    def _end(self, answered, reply):
        if not self.stopped:
            GLib.idle_add(answered, reply)

    def stop(self):
        self.stopped = True
        if self.ch is not None:
            self.ch.close()


class Page(Adw.PreferencesPage):
    """The page: its host (where, name), its accounts as the host last said them."""

    def __init__(self, win):
        super().__init__(title=wording.say("subscriptions.title"), icon_name="system-users-symbolic")
        from widgets import walk
        for clamp in walk(self):  # (as widgets.settings_page: rows as wide as the dialog)
            if isinstance(clamp, Adw.Clamp):
                clamp.set_maximum_size(100_000)
                clamp.set_tightening_threshold(100_000)
        self.where, self.host = win.terminal_host()
        self.accounts, self.defaults, self.lists, self.filling = [], {}, {}, False
        top = Adw.PreferencesGroup()
        host = Adw.ActionRow(title=wording.say("new_session.host"))
        host.add_suffix(Gtk.Label(label=self.host, css_classes=["dim-label"]))
        top.add(host)
        self.add(top)
        for provider, title, logo in PROVIDERS:
            group = Adw.PreferencesGroup()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            heading = Gtk.Box(spacing=8)
            heading.append(Gtk.Image.new_from_file(os.path.join(icons.folder("models"),
                                                                f"baude-model-{logo}-symbolic.svg")))
            label = Gtk.Label(label=wording.say(title))
            label.add_css_class("heading")
            heading.append(label)
            box.append(heading)
            rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
            box.append(rows)
            group.add(box)
            group.update_property([Gtk.AccessibleProperty.LABEL], [wording.say(title)])
            self.add(group)
            self.lists[provider] = rows
        self.usage_said = None
        self.fill()
        self.ask({"what": "list"})

    def ask(self, msg, then=None):
        """msg to the host; the page filled again from what it answers, then(reply) after, and its error said."""
        def answered(reply):
            if "accounts" in reply:
                self.accounts, self.defaults = reply["accounts"], reply.get("defaults") or {}
                self.fill()
            elif msg.get("what") == "list":
                self.fill(reply.get("error"))
            if then is not None:
                then(reply)
            elif reply.get("error") and msg.get("what") != "list":
                self.toast(reply["error"])
            return False
        return Ask(self.where, msg, answered)

    def toast(self, text):
        dialog = self.get_ancestor(Adw.PreferencesDialog)
        if dialog is not None:
            dialog.add_toast(Adw.Toast(title=text, timeout=3))

    def fill(self, error=None):
        for provider, rows in self.lists.items():
            rows.remove_all()
            if error:
                rows.append(Adw.ActionRow(title=GLib.markup_escape_text(error), sensitive=False))
                continue
            mine = [a for a in self.accounts if a["provider"] == provider]
            ids = ["", *(a["id"] for a in mine)]
            chooser = Adw.ComboRow(title=wording.say("subscriptions.account"),
                                   model=Gtk.StringList.new([wording.say("subscriptions.cli_default"),
                                                             *(a["name"] for a in mine)]))
            active = self.defaults.get(provider, "")
            chooser.set_selected(ids.index(active) if active in ids else 0)
            chooser.connect("notify::selected", self.picked, provider, ids)
            rows.append(chooser)
            for account in mine:
                rows.append(self.account_row(account))
            if provider == "claude":
                usage = Adw.ActionRow(title=wording.say("subscriptions.usage"), activatable=True)
                said = Gtk.Label(label=self.usage_said or "", css_classes=["dim-label"], ellipsize=3)
                usage.add_suffix(said)
                usage.add_suffix(Gtk.Image(icon_name="view-refresh-symbolic"))
                usage.connect("activated", self.read_usage, said)
                rows.append(usage)
            add = Adw.ButtonRow(title=wording.say("subscriptions.add_account"), end_icon_name="list-add-symbolic")
            add.connect("activated", lambda _row, p=provider: (add_claude if p == "claude" else add_codex)(self))
            rows.append(add)

    def account_row(self, account):
        row = Adw.ActionRow(title=GLib.markup_escape_text(account["name"]))
        actions = Gio.SimpleActionGroup()
        menu = Gio.Menu()
        items = [("rename", wording.say("common.rename_ellipsis"), rename),
                 ("again", wording.say("subscriptions.replace_token") if account["kind"] == "token" else
                  wording.say("subscriptions.sign_in_again"), sign_in_again),
                 ("remove", wording.say("common.remove_ellipsis"), remove)]
        for name, label, action in items:
            act = Gio.SimpleAction(name=name)
            act.connect("activate", lambda *_a, f=action: f(self, account))
            actions.add_action(act)
            menu.append(label, f"account.{name}")
        row.insert_action_group("account", actions)
        more = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu, valign=Gtk.Align.CENTER,
                              tooltip_text=wording.say("subscriptions.more_tip"), css_classes=["flat"])
        row.add_suffix(more)
        return row

    def picked(self, chooser, _pspec, provider, ids):
        index = chooser.get_selected()
        if index < len(ids) and ids[index] != self.defaults.get(provider, ""):
            self.usage_said = None
            self.ask({"what": "select", "provider": provider, "id": ids[index]})

    def read_usage(self, row, said):
        row.set_sensitive(False)
        said.set_label(wording.say("subscriptions.checking"))

        def done(reply):
            row.set_sensitive(True)
            if reply.get("error"):
                self.usage_said = reply["error"]
            elif reply.get("usage"):
                self.usage_said = " · ".join(wording.say("subscriptions.usage_limit", name=u["name"],
                                                         percent=f"{u['percent']:g}") for u in reply["usage"])
            else:
                self.usage_said = wording.say("subscriptions.usage_unavailable")
            said.set_label(self.usage_said)
            if "accounts" in reply:
                self.fill()
        self.ask({"what": "usage", "id": self.defaults.get("claude", "")}, done)


def _form(page, heading, submit_label):
    """An AlertDialog over page whose form (returned with its status line and submit button) stays open till what's
    sent is done: Close is its only response."""
    dialog = Adw.AlertDialog(heading=heading, close_response="cancel")
    form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, width_request=360)  # (room for the token's hint)
    status = Gtk.Label(xalign=0, wrap=True, selectable=True, visible=False)
    submit = Gtk.Button(label=submit_label, css_classes=["suggested-action"])
    dialog.set_extra_child(form)
    dialog.add_response("cancel", wording.say("common.close"))

    def say(text):
        status.set_text(text or "")
        status.set_visible(bool(text))
    return dialog, form, status, submit, say


def _send(page, dialog, submit, say, msg, inputs=()):
    """msg sent from a dialog: closed once it's done, else why not said in it."""
    submit.set_sensitive(False)
    for w in inputs:
        w.set_sensitive(False)

    def then(reply):
        if reply.get("error"):
            say(reply["error"])
            submit.set_sensitive(True)
            for w in inputs:
                w.set_sensitive(True)
        else:
            dialog.close()
    page.ask(msg, then)


def add_claude(page):
    dialog, form, status, submit, say = _form(page, wording.say("subscriptions.add_claude"),
                                              wording.say("subscriptions.save_account"))
    name = Gtk.Entry(placeholder_text=wording.say("subscriptions.name_hint"))
    token = Gtk.PasswordEntry(placeholder_text=wording.say("subscriptions.token_hint"), show_peek_icon=True)
    for w in (name, token, status, submit):
        form.append(w)

    def save(*_):
        if not name.get_text().strip():
            return name.grab_focus()
        _send(page, dialog, submit, say, {"what": "add", "provider": "claude", "name": name.get_text().strip(),
                                          "token": token.get_text().strip()}, (name, token))
    submit.connect("clicked", save)
    token.connect("activate", save)
    dialog.present(page)
    return dialog


def add_codex(page):
    dialog, form, status, submit, say = _form(page, wording.say("subscriptions.add_codex"),
                                              wording.say("subscriptions.device_code"))
    name = Gtk.Entry(placeholder_text=wording.say("subscriptions.name_hint"))
    home = Gtk.Entry(placeholder_text=wording.say("subscriptions.codex_home_hint"))
    home.connect("changed", lambda e: submit.set_label(wording.say("subscriptions.use_profile") if e.get_text().strip()
                                                       else wording.say("subscriptions.device_code")))
    for w in (name, home, status, submit):
        form.append(w)
    running = []

    def save(*_):
        label = name.get_text().strip()
        if not label:
            return name.grab_focus()
        if home.get_text().strip():
            return _send(page, dialog, submit, say, {"what": "add", "provider": "codex", "name": label,
                                                     "home": home.get_text().strip()}, (name, home))
        running[:] = [_device_sign_in(page, dialog, submit, say, {"what": "sign_in", "name": label}, (name, home))]
    submit.connect("clicked", save)
    dialog.connect("response", lambda *_: running and running[0].stop())
    dialog.present(page)
    return dialog


def _device_sign_in(page, dialog, submit, say, msg, inputs):
    """Codex's device-code sign-in run on the host: what it writes (the code, and where to enter it) said in the dialog
    as it's written; closed once it's signed in."""
    submit.set_sensitive(False)
    for w in inputs:
        w.set_sensitive(False)
    lines = []
    say(wording.say("subscriptions.requesting_code"))

    def heard(line):
        lines.append(line)
        say("\n".join(lines[-14:]))
        return False

    def answered(reply):
        if "accounts" in reply:
            page.accounts, page.defaults = reply["accounts"], reply.get("defaults") or {}
            page.fill()
        if reply.get("error"):
            say(reply["error"])
            submit.set_sensitive(True)
            for w in inputs:
                w.set_sensitive(True)
        else:
            dialog.close()
        return False
    return Ask(page.where, msg, answered, heard)


def rename(page, account):
    dialog, form, status, submit, say = _form(page, wording.say("subscriptions.rename_heading", name=account["name"]),
                                              wording.say("common.rename"))
    name = Gtk.Entry(text=account["name"], placeholder_text=wording.say("subscriptions.name_hint"))
    for w in (name, status, submit):
        form.append(w)

    def save(*_):
        if name.get_text().strip():
            _send(page, dialog, submit, say, {"what": "rename", "id": account["id"], "name": name.get_text().strip()},
                  (name,))
    submit.connect("clicked", save)
    name.connect("activate", save)
    dialog.present(page)
    return dialog


def sign_in_again(page, account):
    if account["kind"] == "token":
        dialog, form, status, submit, say = _form(page, wording.say("subscriptions.replace_heading",
                                                                    name=account["name"]), wording.say("common.save"))
        token = Gtk.PasswordEntry(placeholder_text=wording.say("subscriptions.token_hint"), show_peek_icon=True)
        for w in (token, status, submit):
            form.append(w)

        def save(*_):
            _send(page, dialog, submit, say, {"what": "token", "id": account["id"], "token": token.get_text().strip()},
                  (token,))
        submit.connect("clicked", save)
        token.connect("activate", save)
        dialog.present(page)
        return dialog
    dialog, form, status, submit, say = _form(page, wording.say("subscriptions.sign_in_heading", name=account["name"]),
                                              wording.say("subscriptions.device_code"))
    for w in (status, submit):
        form.append(w)
    running = []
    submit.connect("clicked", lambda *_: running.__setitem__(slice(None), [_device_sign_in(
        page, dialog, submit, say, {"what": "sign_in", "id": account["id"]}, ())]))
    dialog.connect("response", lambda *_: running and running[0].stop())
    dialog.present(page)
    return dialog


def remove(page, account):
    dialog = Adw.AlertDialog(heading=wording.say("subscriptions.remove_heading", name=account["name"]),
                             close_response="cancel")
    dialog.add_response("cancel", wording.say("common.cancel"))
    dialog.add_response("remove", wording.say("common.remove"))
    dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.connect("response", lambda _d, r: r == "remove" and page.ask({"what": "remove", "id": account["id"]}))
    dialog.present(page)
    return dialog


def replace_token(where, profile, token, done):
    """A chat's Claude account's token replaced on its host (where): done(error or None)."""
    return Ask(where, {"what": "token", "id": profile, "token": token}, lambda r: done(r.get("error")) and False)
