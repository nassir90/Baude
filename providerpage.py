"""Host-local Claude and Codex subscriptions in Baude and Record settings."""
import json
import os
import sqlite3
import re
import shutil
import subprocess
import tempfile
import threading

from gi.repository import Adw, GLib, Gtk

from baudelib import accounts, models


def page():
    result = Adw.PreferencesPage(title="Providers", icon_name="system-users-symbolic")
    for provider, title in (("claude", "Claude"), ("codex", "Codex")):
        group = Adw.PreferencesGroup(title=title, description="Account for new sessions on this machine")
        result.add(group)
        chooser = Adw.ComboRow(title="Account")
        group.add(chooser)
        names = []

        def refresh(provider=provider, chooser=chooser, names=names):
            entries = [("", "CLI default"), *accounts.profiles(provider)]
            names[:] = [key for key, _ in entries]
            chooser.freeze_notify()
            chooser.set_model(Gtk.StringList.new([label for _, label in entries]))
            active = accounts.selected(provider)
            chooser.set_selected(names.index(active) if active in names else Gtk.INVALID_LIST_POSITION)
            chooser.thaw_notify()

        refresh()
        def changed(row, _, provider=provider, names=names):
            index = row.get_selected()
            if index < len(names):
                accounts.select(provider, names[index])

        chooser.connect("notify::selected", changed)
        if provider == "claude":
            usage = Adw.ActionRow(title="Subscription usage", activatable=True)
            usage.add_suffix(Gtk.Image(icon_name="view-refresh-symbolic"))
            usage.connect("activated", refresh_usage)
            chooser.connect("notify::selected", lambda *_, row=usage: row.set_subtitle(""))
            group.add(usage)
        add = Adw.ButtonRow(title="Add account", end_icon_name="list-add-symbolic")
        add.connect("activated", lambda row, p=provider, refresh=refresh: add_account(row, p, refresh))
        group.add(add)
    return result


def refresh_usage(row):
    profile = accounts.selected("claude")
    row.set_sensitive(False)
    row.set_subtitle("Checking…")

    def done(result):
        row.set_subtitle(result if accounts.selected("claude") == profile else "")
        row.set_sensitive(True)
        return False

    def read():
        try:
            limits = accounts.plan_usage(profile)
            result = " · ".join(f"{name} {amount:g}%" for name, amount in limits) or "Usage unavailable for this sign-in"
        except (OSError, subprocess.SubprocessError, sqlite3.Error):
            result = "Could not read usage from Claude Code"
        GLib.idle_add(done, result)

    threading.Thread(target=read, daemon=True).start()


def add_account(parent, provider, refresh):
    claude = provider == "claude"
    dialog = Adw.AlertDialog(heading="Add Claude account" if claude else "Add Codex account",
                             close_response="cancel")
    form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    name = Gtk.Entry(placeholder_text="Account name")
    form.append(name)
    token = Gtk.PasswordEntry() if claude else None
    if token is not None:
        form.append(Gtk.Label(label="Paste the token from claude setup-token", xalign=0))
        form.append(token)
    existing = None
    if not claude:
        existing = Gtk.Entry(placeholder_text="Existing signed-in CODEX_HOME (optional)")
        form.append(existing)
    status = Gtk.Label(xalign=0, wrap=True, selectable=True)
    form.append(status)
    submit = Gtk.Button(label="Save account" if claude else "Sign in with device code")
    if existing is not None:
        existing.connect("changed", lambda entry: submit.set_label("Use signed-in profile" if entry.get_text().strip()
                                                                   else "Sign in with device code"))
    form.append(submit)
    dialog.set_extra_child(form)
    dialog.add_response("cancel", "Close")
    running = []
    closed = False

    def close(*_):
        nonlocal closed
        closed = True
        if running and running[0].poll() is None:
            running[0].terminate()

    def save(*_):
        label = name.get_text().strip()
        if not label:
            name.grab_focus()
            return
        if claude:
            value = token.get_text().strip()
            if not value.startswith("sk-ant-oat") or any(c.isspace() for c in value):
                status.set_text("Enter a Claude setup token")
                return
            try:
                accounts.add(provider, label, value)
            except (ValueError, OSError, sqlite3.Error) as exc:
                status.set_text(str(exc))
                return
            token.set_text("")
            refresh()
            dialog.close()
            return
        location = existing.get_text().strip()
        if location:
            directory = os.path.realpath(os.path.expanduser(location))
            try:
                with open(os.path.join(directory, "auth.json"), encoding="utf-8") as f:
                    auth = json.load(f)
                if (not isinstance(auth, dict) or auth.get("auth_mode") not in (None, "chatgpt")
                        or not isinstance(auth.get("tokens"), dict) or not auth["tokens"].get("access_token")):
                    raise ValueError("Choose a file-backed Codex profile signed in with ChatGPT")
                accounts.add(provider, label, directory)
            except (ValueError, OSError, sqlite3.Error) as exc:
                status.set_text(str(exc))
                return
            refresh()
            dialog.close()
            return
        directory = None
        try:
            accounts.root().mkdir(mode=0o700, parents=True, exist_ok=True)
            directory = tempfile.mkdtemp(prefix="codex-", dir=accounts.root())
            with open(os.path.join(directory, "config.toml"), "x") as config:
                config.write('cli_auth_credentials_store = "file"\n')
            proc = subprocess.Popen([models.codex_exe() or "codex", "login", "--device-auth"],
                                    env={**os.environ, "CODEX_HOME": directory}, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        except OSError as exc:
            if directory:
                shutil.rmtree(directory)
            status.set_text(str(exc))
            return
        running[:] = [proc]
        submit.set_sensitive(False)
        existing.set_sensitive(False)
        name.set_sensitive(False)
        status.set_text("Requesting a device code…")

        def finish(success, message):
            if success and not closed:
                try:
                    accounts.add(provider, label, directory)
                except (ValueError, OSError, sqlite3.Error) as exc:
                    status.set_text(str(exc))
                else:
                    refresh()
                    dialog.close()
                    return False
            shutil.rmtree(directory, ignore_errors=True)
            if not closed:
                if not success:
                    status.set_text(message)
                existing.set_sensitive(True)
                name.set_sensitive(True)
                submit.set_sensitive(True)
            return False

        def read():
            lines = []
            with proc.stdout:
                for line in proc.stdout:
                    line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line).strip()
                    if line:
                        lines.append(line)
                        GLib.idle_add(status.set_text, "\n".join(lines[-14:]))
            success = proc.wait() == 0 and os.path.isfile(os.path.join(directory, "auth.json"))
            reason = lines[-1] if lines else "The Codex CLI did not complete sign-in"
            GLib.idle_add(finish, success, reason)

        threading.Thread(target=read, daemon=True).start()

    submit.connect("clicked", save)
    dialog.connect("response", close)
    dialog.present(parent)
