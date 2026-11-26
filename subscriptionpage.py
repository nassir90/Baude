"""Settings → Subscriptions (Baude's and Record's; the phone's SubscriptionsScreen the same): the Claude and Codex
subscription accounts kept on the host picked beside New session (win.terminal_host, as ☰ → Terminal, Kubernetes and
Databases are), changed there through its accounts op (hostaccounts): for each provider, a Current Subscription row
picking the subscription a new chat takes, a card per subscription (an account's with ⋮: Rename…, Sign In Again… or Replace
Token…, Remove…; a Claude card with its usage as a bar per limit, read every USAGE_S while the page is shown in the window in front), and Add
account (where the host lacks the provider's program, what Settings → Dependencies says of it instead: depsview.row).
A Claude account is signed in to with claude auth login run on
the host with the account's own folder: the page to open shown as a link, and a box for the code it shows. Where the
host has no claude, it's a token from claude setup-token. A Codex account is signed in to with codex login
--device-auth run on the host, as a card (CodexLogin): its page's link and the code to enter there, as the host says
them. Also the chat's Sign in card's token box for a chat under a
token account (replace_token)."""
import html
import os
import re
import threading

from gi.repository import Adw, Gio, GLib, Gtk

from baudelib import deps, icons, tokens, wire, wording

PROVIDERS = (("claude", "report.claude", "claude"), ("codex", "programs.codex", "openai"))
USAGE_S = 30  # (how often a shown page reads its subscriptions' usage again)
STALE_OPACITY = tokens.number("subscriptions.stale-opacity")  # (a card's usage as the host last read it, till the page's own read answers)


def title_of(provider):
    """A provider's copy key (PROVIDERS'): report.claude, programs.codex."""
    return next(title for p, title, _logo in PROVIDERS if p == provider)


def clock_24h():
    """Whether the desktop shows the time of day on a 24-hour clock: its own setting where it has one, else its locale's."""
    source = Gio.SettingsSchemaSource.get_default()
    if source is not None and source.lookup("org.gnome.desktop.interface", True) is not None:
        setting = Gio.Settings.new("org.gnome.desktop.interface").get_string("clock-format")
        if setting in ("12h", "24h"):
            return setting == "24h"
    return not GLib.DateTime.new_now_local().format("%p")


def limit_label(key, name, resets):
    """A limit's name over its bar, with when it resets in the viewer's local time and clock: Five Hour Limit (Resets
    3:00 PM), Weekly Limit (Resets Friday 3:00 PM), or just the time where it resets today."""
    if resets is None:
        return name
    when = GLib.DateTime.new_from_unix_local(int(resets))
    time = when.format("%H:%M" if clock_24h() else "%-I:%M %p")
    today = when.format("%Y-%m-%d") == GLib.DateTime.new_now_local().format("%Y-%m-%d")
    day = when.format("%A")
    if key == "five_hour":
        return wording.say("subscriptions.five_hour_limit", time=time)
    if key == "seven_day":
        return wording.say("subscriptions.weekly_limit_today", time=time) if today else \
            wording.say("subscriptions.weekly_limit", day=day, time=time)
    return wording.say("subscriptions.other_limit_today", name=name, time=time) if today else \
        wording.say("subscriptions.other_limit", name=name, day=day, time=time)


class Ask:
    """One accounts request to a host (where), on a connection of its own (through the mesh, if that's how it's
    reached): answered(reply) once its answer comes (an error's too, as {"error": …}); heard(line) for each line a
    sign-in writes, state(login) for each state a Codex sign-in says (ChatLogin); send(msg) another accounts message
    on it (a sign-in's code). stop() lets go of the connection, which stops a sign-in there. On the main thread."""

    def __init__(self, where, msg, answered, heard=None, state=None):
        self.ch, self.stopped, self.state = None, False, state
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
            if m.get("op") == "accounts" and "login" in m and "accounts" not in m:
                if self.state is not None and not self.stopped:
                    GLib.idle_add(self.state, m["login"])
                continue
            end = m if m.get("op") == "accounts" else {"error": wire.said(m)}
            break
        ch.close()
        self._end(answered, end or {"error": wording.say("host_status.connection_dropped")})

    def _end(self, answered, reply):
        if not self.stopped:
            GLib.idle_add(answered, reply)

    def send(self, msg):
        return self.ch is not None and not self.stopped and self.ch.send({"op": "accounts", "req": 1, **msg})

    def stop(self):
        self.stopped = True
        if self.ch is not None:
            self.ch.close()


class Page(Adw.PreferencesPage):
    """The page: its host (where, name), its accounts as the host last said them, and each Claude subscription's usage
    (usage: id → its limits [(name, percent)], or what to say instead), read as the page is shown and every
    USAGE_S while it is; till its first read answers, what the host last read (last: id → its limits), greyed."""

    def __init__(self, win):
        super().__init__(title=wording.say("subscriptions.title"), icon_name="system-users-symbolic")
        from widgets import walk
        for clamp in walk(self):  # (as widgets.settings_page: rows as wide as the dialog)
            if isinstance(clamp, Adw.Clamp):
                clamp.set_maximum_size(100_000)
                clamp.set_tightening_threshold(100_000)
        self.where, self.host = win.terminal_host()
        self.here = wire.is_local(self.where)  # (the host's this machine's: a sign-in's page opened here at once)
        self.accounts, self.defaults, self.filling, self.claude_cli, self.missing = [], {}, False, True, {}
        self.choices = {}
        self.lists, self.pills, self.usage, self.bars, self.reading, self.last = {}, {}, {}, {}, set(), {}
        self.shown, self.timer, self.window_watch = None, None, None
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
            label = Gtk.Label(label=wording.say(title), hexpand=True, xalign=0)
            label.add_css_class("heading")
            heading.append(label)
            box.append(heading)
            current = Adw.ComboRow(title=wording.say("subscriptions.current"), model=Gtk.StringList.new([]))
            current.connect("notify::selected", self.picked, provider)
            current_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
            current_list.append(current)
            box.append(current_list)
            cards = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.append(cards)
            group.add(box)
            group.update_property([Gtk.AccessibleProperty.LABEL], [wording.say(title)])
            self.add(group)
            self.lists[provider], self.pills[provider] = cards, (current_list, current)
        self.fill()
        self.ask({"what": "list"})
        self.connect("map", self.shown_now)
        self.connect("unmap", self.hidden)

    def ask(self, msg, then=None):
        """msg to the host; the page filled again from what it answers, then(reply) after, and its error said."""
        def answered(reply):
            if "accounts" in reply:
                self.took(reply)
            elif msg.get("what") == "list":
                self.fill(reply.get("error"))
            if then is not None:
                then(reply)
            elif reply.get("error") and msg.get("what") != "list":
                self.toast(reply["error"])
            return False
        return Ask(self.where, msg, answered)

    def took(self, reply):
        """An answer's accounts, defaults, whether the host has claude and what it lacks, kept and shown (drawn again
        only if they changed), and the usage the host last read, shown greyed where this page hasn't read it yet; a
        Claude subscription not read yet, read."""
        self.accounts, self.defaults = reply["accounts"], reply.get("defaults") or {}
        last = {p: _limits(r["usage"]) for p, r in (reply.get("last_usage") or {}).items()}
        if last != self.last:
            self.last = last
            for profile in self.bars:
                if profile not in self.usage:
                    self.fill_bars(profile)
        self.claude_cli = reply.get("claude_cli", self.claude_cli)
        self.missing = reply.get("missing") or {}
        if (self.accounts, self.defaults, self.claude_cli, self.missing) != self.shown:
            self.fill()
        if self.get_mapped():
            self.read_usage([p for p in self.claude_ids() if p not in self.usage])

    def toast(self, text):
        dialog = self.get_ancestor(Adw.PreferencesDialog)
        if dialog is not None:
            dialog.add_toast(Adw.Toast(title=text, timeout=3))

    def claude_ids(self):
        """The Claude subscriptions whose usage is read: the CLI's own sign-in (where the host has claude), then each
        account."""
        return [*([""] if self.claude_cli else []), *(a["id"] for a in self.accounts if a["provider"] == "claude")]

    def fill(self, error=None):
        self.shown = None if error else (self.accounts, self.defaults, self.claude_cli, self.missing)
        self.bars = {}
        for provider, cards in self.lists.items():
            while (child := cards.get_first_child()) is not None:
                cards.remove(child)
            current_list, current = self.pills[provider]
            current_list.set_visible(not error)
            if error:
                rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
                rows.append(Adw.ActionRow(title=GLib.markup_escape_text(error), sensitive=False))
                cards.append(rows)
                continue
            mine = [a for a in self.accounts if a["provider"] == provider]
            self.fill_pill(provider, current, mine)
            if provider == "claude" and self.claude_cli:
                cards.append(self.card(provider, None))
            for account in mine:
                cards.append(self.card(provider, account))
            rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
            if (dep := self.missing.get(provider)) is not None:  # (its program isn't on the host: no account to add)
                from depsview import row
                rows.append(row(dep, deps.title(dep, title_of(provider))))
            else:
                add = Adw.ButtonRow(title=wording.say("subscriptions.add_account"), end_icon_name="list-add-symbolic")
                add.connect("activated", lambda _row, p=provider: (add_claude if p == "claude" else add_codex)(self))
                rows.append(add)
            cards.append(rows)

    def fill_pill(self, provider, row, mine):
        """The Current Subscription row over a provider's cards: the subscription a new chat on this host takes, and the
        others to pick from; set from what the host says its default is."""
        names = {"": wording.say("subscriptions.cli_default"), **{a["id"]: a["name"] for a in mine}}
        active = self.defaults.get(provider, "")
        active = active if active in names else ""
        self.choices[provider] = list(names)
        self.filling = True
        row.set_model(Gtk.StringList.new(list(names.values())))
        row.set_selected(list(names).index(active))
        self.filling = False

    def card(self, provider, account):
        """A subscription's card: its name and ⋮ (an account's: Rename…, Sign In Again… or Replace Token…, Remove…),
        then, for Claude's, its usage: a bar for each limit with its percentage."""
        card = Gtk.Box(css_classes=["card"])
        inside = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, hexpand=True, margin_top=6, margin_bottom=12,
                         margin_start=12, margin_end=6)
        card.append(inside)
        top = Gtk.Box(spacing=8, height_request=34)  # (as high with ⋮ as without)
        name = Gtk.Label(label=account["name"] if account else wording.say("subscriptions.cli_default"),
                         xalign=0, hexpand=True, ellipsize=3)
        top.append(name)
        if account is not None:
            top.append(self.account_menu(account))
        inside.append(top)
        if provider == "claude":
            bars = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_end=6)
            inside.append(bars)
            profile = account["id"] if account else ""
            self.bars[profile] = bars
            self.fill_bars(profile)
        return card

    def fill_bars(self, profile):
        """A Claude card's usage as last read: a bar for each limit, its name and percentage over it; else what was
        said instead. Till it's first read here, the host's last reading greyed (Checking… where it has none)."""
        bars = self.bars.get(profile)
        if bars is None:
            return
        while (child := bars.get_first_child()) is not None:
            bars.remove(child)
        read = self.usage.get(profile)
        stale = read is None and profile in self.last
        bars.set_opacity(STALE_OPACITY if stale else 1)
        if stale:
            read = self.last[profile]
        if not isinstance(read, list):
            bars.append(Gtk.Label(label=read or wording.say("subscriptions.checking"), xalign=0, wrap=True,
                                  css_classes=["dim-label"]))
            return
        for key, name, percent, resets in read:
            line = Gtk.Box(spacing=8)
            line.append(Gtk.Label(label=limit_label(key, name, resets), xalign=0, hexpand=True, wrap=True))
            line.append(Gtk.Label(label=wording.say("subscriptions.usage_percent", percent=f"{percent:g}"),
                                  css_classes=["dim-label", "numeric"]))
            bars.append(line)
            bars.append(Gtk.ProgressBar(fraction=max(0.0, min(1.0, percent / 100))))

    def account_menu(self, account):
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
        more = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu, valign=Gtk.Align.CENTER,
                              tooltip_text=wording.say("subscriptions.more_tip"), css_classes=["flat"])
        more.insert_action_group("account", actions)
        return more

    def picked(self, row, _pspec, provider):
        if self.filling:
            return
        choices = self.choices.get(provider, [])
        if row.get_selected() >= len(choices):
            return
        profile = choices[row.get_selected()]
        if profile != self.defaults.get(provider, ""):
            def answered(reply):  # (a refusal leaves the row on the host's own default)
                if reply.get("error"):
                    self.toast(reply["error"])
                    self.fill_pill(provider, row, [a for a in self.accounts if a["provider"] == provider])
            self.ask({"what": "select", "provider": provider, "id": profile}, answered)

    def shown_now(self, *_):
        """The page in front: its Claude subscriptions' usage read now and every USAGE_S till it isn't."""
        self.read_usage(self.claude_ids())
        window = self.get_root()
        if window is not None and self.window_watch is None:
            self.window_watch = (window, window.connect("notify::is-active", self.window_changed))
        self.window_changed()

    def window_changed(self, *_):
        """The timer runs only while the page's window is in front (not minimised or hidden behind others)."""
        window = self.get_root()
        if window is not None and window.is_active() and self.get_mapped():
            if self.timer is None:
                self.timer = GLib.timeout_add_seconds(USAGE_S, self.tick)
        else:
            self.stop_timer()

    def stop_timer(self):
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None

    def hidden(self, *_):
        self.stop_timer()
        if self.window_watch is not None:
            window, handler = self.window_watch
            window.disconnect(handler)
            self.window_watch = None

    def tick(self):
        self.read_usage(self.claude_ids())
        return True

    def read_usage(self, profiles):
        """Each subscription's (profile's) usage asked of the host, one ask at a time each; its card's bars drawn
        again from the answer."""
        for profile in profiles:
            if profile in self.reading:
                continue
            self.reading.add(profile)

            def done(reply, profile=profile):
                self.reading.discard(profile)
                if reply.get("error"):
                    self.usage[profile] = reply["error"]
                elif reply.get("usage"):
                    self.usage[profile] = _limits(reply["usage"])
                else:
                    self.usage[profile] = wording.say("subscriptions.usage_unavailable")
                self.fill_bars(profile)
            self.ask({"what": "usage", "id": profile}, done)


def _limits(usage):
    """The wire's UsageLimits as a card draws them: [(key, name, percent, resets)]."""
    return [(u.get("key", ""), u["name"], u["percent"], u.get("resets")) for u in usage]


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
    """Add account (Claude): signed in with claude auth login run on the host with a folder of the account's own (or
    an existing signed-in folder there); a token from claude setup-token where the host has no claude."""
    if not page.claude_cli:
        return add_claude_token(page)
    dialog, form, status, submit, say = _form(page, wording.say("subscriptions.add_claude"),
                                              wording.say("sign_in.sign_in"))
    name = Gtk.Entry(placeholder_text=wording.say("subscriptions.name_hint"))
    folder = Gtk.Entry(placeholder_text=wording.say("subscriptions.claude_folder_hint"))
    folder.connect("changed", lambda e: submit.set_label(wording.say("subscriptions.use_folder") if e.get_text().strip()
                                                         else wording.say("sign_in.sign_in")))
    for w in (name, folder):
        form.append(w)
    login = _ClaudeLogin(page, dialog, form, status, submit, say, (name, folder))

    def save(*_):
        label = name.get_text().strip()
        if not label:
            return name.grab_focus()
        if folder.get_text().strip():
            return _send(page, dialog, submit, say, {"what": "add", "provider": "claude", "name": label,
                                                     "folder": folder.get_text().strip()}, (name, folder))
        login.start({"what": "sign_in", "provider": "claude", "name": label})
    submit.connect("clicked", save)
    name.connect("activate", save)
    dialog.present(page)
    return dialog


class _ClaudeLogin:
    """claude auth login run on the host with an account's folder, in a dialog's form: the page to open shown as a
    link, a box for the code the page may show (sent to it on the same connection), what went wrong if it didn't
    finish; the dialog closed once it's signed in, and Close stops it."""

    def __init__(self, page, dialog, form, status, submit, say, inputs):
        self.page, self.dialog, self.submit, self.say, self.inputs = page, dialog, submit, say, inputs
        self.ask, self.lines, self.checking = None, [], False
        self.link = Gtk.Label(xalign=0, wrap=True, visible=False)
        self.row = Gtk.Box(spacing=6, visible=False)
        self.code = Gtk.Entry(hexpand=True, placeholder_text=wording.say("sign_in.code_hint"))
        send = Gtk.Button(label=wording.say("common.send"))
        self.code.connect("activate", lambda *_: self.paste())
        send.connect("clicked", lambda *_: self.paste())
        self.row.append(self.code)
        self.row.append(send)
        form.append(self.link)
        form.append(self.row)
        form.append(status)
        form.append(submit)
        dialog.connect("response", lambda *_: self.ask is not None and self.ask.stop())

    def start(self, msg):
        self.submit.set_sensitive(False)
        for w in self.inputs:
            w.set_sensitive(False)
        self.lines = []
        self.say(wording.say("sign_in.starting"))
        self.ask = Ask(self.page.where, msg, self.answered, self.heard)

    def heard(self, line):
        if m := re.search(r"https://\S+", line):
            url = html.escape(m.group(0).rstrip(".,)"))
            link = f"<a href=\"{url}\">{html.escape(wording.say('sign_in.open_page_there'))}</a>"
            self.link.set_markup(html.escape(wording.say("sign_in.page_there", link="\x00")).replace("\x00", link))
            self.link.set_visible(True)
            self.row.set_visible(True)
            self.say("")
            self.code.grab_focus()
        elif not line.startswith("Opening browser"):
            self.lines.append(line)
            if self.checking:  # (what claude says of a pasted code: "Invalid code…", and it asks again)
                self.checking = False
                self.say(line)
        return False

    def paste(self):
        code = self.code.get_text().strip()
        if code and self.ask is not None and self.ask.send({"what": "code", "code": code}):
            self.code.set_text("")
            self.checking = True
            self.say(wording.say("sign_in.checking_code"))

    def answered(self, reply):
        self.ask = None
        if "accounts" in reply:
            self.page.took(reply)
        if reply.get("error"):
            self.link.set_visible(False)
            self.row.set_visible(False)
            self.say(reply["error"])
            self.submit.set_sensitive(True)
            for w in self.inputs:
                w.set_sensitive(True)
        else:
            self.dialog.close()
        return False


def add_claude_token(page):
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

    def save(*_):
        label = name.get_text().strip()
        if not label:
            return name.grab_focus()
        if home.get_text().strip():
            card.cancelled()
            return _send(page, dialog, submit, say, {"what": "add", "provider": "codex", "name": label,
                                                     "home": home.get_text().strip()}, (name, home))
        say("")
        card.start({"what": "sign_in", "name": label}, label)
    card = CodexLogin(page, submit, (name, home), save)
    for w in (name, home, card, status, submit):
        form.append(w)
    submit.connect("clicked", save)
    dialog.connect("response", lambda *_: card.stop())
    dialog.present(page)
    return dialog


def open_page(widget, url):
    """A sign-in's page opened in this machine's browser."""
    Gtk.UriLauncher(uri=url).launch(widget.get_root(), None, None)


class CodexLogin(Gtk.Box):
    """Codex's device-code sign-in run on the host (codex login --device-auth, by hostaccounts), in a dialog's form, as
    a card the way a chat's Sign in card is (LoginCard's look: the warning-bordered box, a bold title, a line, then
    what can be done now), drawn from its state as the host says it (ChatLogin): signing in, and once codex has said
    them, its page's link (opened at once where the host is this machine's, whose browser is here) and the code to
    enter there, large, with Copy; signed in, as who; or failed, with why and Try Again (again: the dialog's own
    button's). Cancel lets go of its connection, which stops it there, and gives the dialog back its fields and
    button (submit, inputs)."""

    def __init__(self, page, submit, inputs, again):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, visible=False)
        self.add_css_class("perm-card")
        self.page, self.submit, self.inputs = page, submit, inputs
        self.ask, self.name, self.url, self.opened = None, "", None, None
        self.title = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.body = Gtk.Label(xalign=0, wrap=True, selectable=True, visible=False)
        self.code = Gtk.Label(xalign=0, selectable=True, hexpand=True, css_classes=["device-code"])
        self.copy = Gtk.Button(label=wording.say("common.copy"), valign=Gtk.Align.CENTER)
        self.copy.connect("clicked", lambda *_: self.copied())
        self.code_row = Gtk.Box(spacing=12, visible=False)
        self.code_row.append(self.code)
        self.code_row.append(self.copy)
        self.cancel = Gtk.Button(label=wording.say("common.cancel"), halign=Gtk.Align.START)
        self.cancel.connect("clicked", lambda *_: self.cancelled())
        self.again = Gtk.Button(label=wording.say("common.try_again"), halign=Gtk.Align.START, visible=False,
                                css_classes=["suggested-action"])
        self.again.connect("clicked", again)
        for w in (self.title, self.body, self.code_row, self.cancel, self.again):
            self.append(w)

    def say(self, title, body="", dim=False, markup=False):
        self.title.set_markup(f"<b>{html.escape(title)}</b>")
        text = body if markup else html.escape(body or "")
        self.body.set_markup(f"<span alpha='60%'>{text}</span>" if dim and text else text)
        self.body.set_visible(bool(body))

    def start(self, msg, name):
        """Signing in (msg: the accounts sign_in to send), for the account named name."""
        self.stop()
        self.name, self.url = name, None
        self.submit.set_visible(False)
        for w in self.inputs:
            w.set_sensitive(False)
        self.set_visible(True)
        self.show({"state": "signing_in"})
        self.ask = Ask(self.page.where, msg, self.answered, state=self.show)

    def show(self, login):
        """The sign-in as the host says it now (ChatLogin)."""
        state = login.get("state")
        self.code_row.set_visible(False)
        self.cancel.set_visible(state == "signing_in")
        self.again.set_visible(state == "failed")
        if state == "signing_in" and not login.get("url"):
            self.say(wording.say("subscriptions.codex_signing_in"), wording.say("subscriptions.requesting_code"),
                     dim=True)
        elif state == "signing_in":
            url = login["url"]
            if self.page.here and self.opened != url:
                self.opened = url
                open_page(self, url)
            if self.page.here:
                link = f"<a href=\"{html.escape(url)}\">{html.escape(wording.say('sign_in.open_page'))}</a>"
                said = html.escape(wording.say("sign_in.browser_opened", link="\x00")).replace("\x00", link)
            else:  # (another machine's: its browser isn't here, nor maybe anywhere: the page opened here)
                link = f"<a href=\"{html.escape(url)}\">{html.escape(wording.say('sign_in.open_page_there'))}</a>"
                said = html.escape(wording.say("sign_in.page_there", link="\x00")).replace("\x00", link)
            self.say(wording.say("subscriptions.codex_signing_in"), said, markup=True)
            self.code.set_text(login.get("code") or "")
            self.copy.set_label(wording.say("common.copy"))
            self.code_row.set_visible(bool(login.get("code")))
        elif state == "signed_in":
            who = login.get("who")
            self.say(wording.say("subscriptions.codex_signed_in"),
                     wording.say("sign_in.as_who", who=who) if who else self.name)
        else:
            self.say(wording.say("sign_in.failed"), login.get("reason") or wording.say("subscriptions.codex_failed"),
                     dim=True)
        return False

    def copied(self):
        self.get_clipboard().set(self.code.get_text())
        self.copy.set_label(wording.say("subscriptions.code_copied"))

    def answered(self, reply):
        """Its end: signed in, or why not (the fields and Try Again back, to change the name if that's why)."""
        self.ask = None
        if "accounts" in reply:
            self.page.took(reply)
        login = reply.get("login") or ({"state": "signed_in", "who": reply.get("who")} if reply.get("signed_in") else
                                       {"state": "failed", "reason": reply.get("error")})
        self.show(login)
        if login.get("state") != "signed_in":
            for w in self.inputs:
                w.set_sensitive(True)
        return False

    def cancelled(self):
        self.stop()
        self.set_visible(False)
        self.submit.set_visible(True)
        for w in self.inputs:
            w.set_sensitive(True)

    def stop(self):
        if self.ask is not None:
            self.ask.stop()
            self.ask = None


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
    if account["provider"] == "claude":
        dialog, form, status, submit, say = _form(page, wording.say("subscriptions.sign_in_heading",
                                                                    name=account["name"]), wording.say("sign_in.sign_in"))
        login = _ClaudeLogin(page, dialog, form, status, submit, say, ())
        submit.connect("clicked", lambda *_: login.start({"what": "sign_in", "id": account["id"]}))
        dialog.present(page)
        return dialog
    dialog, form, status, submit, say = _form(page, wording.say("subscriptions.sign_in_heading", name=account["name"]),
                                              wording.say("subscriptions.device_code"))
    if (dep := page.missing.get("codex")) is not None:  # (its program isn't on the host: nothing to sign in with)
        from depsview import row
        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        rows.append(row(dep, deps.title(dep, title_of("codex")), page.get_ancestor(Adw.PreferencesDialog)))
        form.set_size_request(480, -1)  # (room for the command beside the title)
        form.append(rows)
        dialog.present(page)
        return dialog
    card = CodexLogin(page, submit, (), lambda *_: card.start({"what": "sign_in", "id": account["id"]},
                                                               account["name"]))
    for w in (card, status, submit):
        form.append(w)
    submit.connect("clicked", lambda *_: card.start({"what": "sign_in", "id": account["id"]}, account["name"]))
    dialog.connect("response", lambda *_: card.stop())
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


def replace_token(where, profile, token, done, session=None):
    """A chat's Claude account's token replaced on its host (where): done(error or None); session: the chat it's
    for there, whose sign-in its host puts right."""
    return Ask(where, {"what": "token", "id": profile, "token": token, **({"session": session} if session else {})},
               lambda r: done(r.get("error")) and False)
