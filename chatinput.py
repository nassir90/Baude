"""chatinput — what goes into a chat's box: attachments (Attachment, prepare_image, attachment_widget), /login
(LoginCard, auth_status), and the completers for / and @ (SlashCompleter, ChatRefCompleter).
"""
import base64
import html
import os
import re
import subprocess
import threading

from baudelib.claude import claude_env, claude_exe
from baudelib import hostlogin, wire  # noqa: E402  (signing in on another machine; what a turn's error needing it is)
from baudelib.hostlogin import auth_status, needs_login, signed_in_as  # noqa: E402,F401
import baudelib.client  # noqa: E402  (servers on the network and their sessions: see baudelib/client.py)
import baudelib.hosts  # noqa: E402  (this machine's host process, started when it's wanted: see baudelib/hosts.py)
from baudelib import wording  # noqa: E402  (the words Baude shows: schema/copy, Record's and the Android app's too)

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

import metalook  # noqa: E402
import thumbs  # noqa: E402  (files' thumbnails, the system's)
import widgets  # noqa: E402  (what Record draws as Baude does: the links' logos; see widgets.py)
import imageviews  # noqa: E402
import markdownview  # noqa: E402
import composerview  # noqa: E402
from imageviews import make_image_interactive  # noqa: E402,F401
from markdownview import sel_label  # noqa: E402,F401
from codeview import file_icon  # noqa: E402,F401


# ------------------------------------------------------------ attachments --
# Images and PDFs go to the model as content blocks; anything else is passed
# as a path so Claude can open it with its own tools.

API_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_IMAGE_EDGE = baudelib.wire.CONSTANTS["IMAGE_EDGE_MAX"]  # larger images are downscaled by the API anyway
MAX_IMAGE_BYTES = baudelib.wire.CONSTANTS["IMAGE_BYTES_MAX"]  # 5 MB once base64-encoded
MAX_PDF_BYTES = 32_000_000


def _pixbuf_from_bytes(data):
    loader = GdkPixbuf.PixbufLoader()
    loader.write(data)
    loader.close()
    return loader.get_pixbuf()


def _encode_pixbuf(pb):
    ok, png = pb.save_to_bufferv("png", [], [])
    if ok and len(png) <= MAX_IMAGE_BYTES:
        return "image/png", png
    if pb.get_has_alpha():  # JPEG has no alpha: flatten onto white
        flat = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, pb.get_width(), pb.get_height())
        flat.fill(0xFFFFFFFF)
        pb.composite(flat, 0, 0, pb.get_width(), pb.get_height(), 0, 0, 1, 1,
                     GdkPixbuf.InterpType.BILINEAR, 255)
        pb = flat
    for quality in ("90", "75", "60"):
        ok, jpg = pb.save_to_bufferv("jpeg", ["quality"], [quality])
        if ok and len(jpg) <= MAX_IMAGE_BYTES:
            return "image/jpeg", jpg
    raise ValueError("image too large even after compression")


def prepare_image(data, mime):
    """Return (media_type, bytes) the API accepts: a supported type, <= MAX_IMAGE_EDGE px, small enough."""
    pb = _pixbuf_from_bytes(data)
    w, h = pb.get_width(), pb.get_height()
    if mime in API_IMAGE_TYPES and max(w, h) <= MAX_IMAGE_EDGE and len(data) <= MAX_IMAGE_BYTES:
        return mime, data
    scale = min(1.0, MAX_IMAGE_EDGE / max(w, h))
    if scale < 1.0:
        pb = pb.scale_simple(max(1, round(w * scale)), max(1, round(h * scale)), GdkPixbuf.InterpType.HYPER)
    return _encode_pixbuf(pb)


class Attachment:
    """kind: 'image' | 'pdf' (sent as content blocks) or 'file' (sent as a path)."""

    def __init__(self, name, kind, media_type=None, data=None, path=None):
        self.name, self.kind, self.media_type, self.data, self.path = name, kind, media_type, data, path

    @classmethod
    def from_path(cls, path):
        path = os.path.abspath(path)
        name = os.path.basename(path)
        ctype, _ = Gio.content_type_guess(path, None)
        mime = Gio.content_type_get_mime_type(ctype) or ""
        try:
            if mime == "application/pdf" and os.path.getsize(path) <= MAX_PDF_BYTES:
                with open(path, "rb") as f:
                    return cls(name, "pdf", mime, f.read(), path)
            if mime.startswith("image/"):
                with open(path, "rb") as f:
                    media_type, data = prepare_image(f.read(), mime)
                return cls(name, "image", media_type, data, path)
        except (OSError, GLib.Error, ValueError):
            pass  # unreadable / undecodable: fall back to passing the path
        return cls(name, "file", mime, None, path)

    @classmethod
    def from_texture(cls, texture, name="pasted image.png"):
        media_type, data = prepare_image(texture.save_to_png_bytes().get_data(), "image/png")
        return cls(name, "image", media_type, data)

    def state(self):
        """As a restart keeps it (BaudeWindow.restart_state): a pasted image is only here."""
        return {"name": self.name, "kind": self.kind, "media_type": self.media_type, "path": self.path,
                "data": base64.b64encode(self.data).decode() if self.data else None}

    @classmethod
    def from_state(cls, state):
        return cls(state["name"], state["kind"], state["media_type"],
                   base64.b64decode(state["data"]) if state["data"] else None, state["path"])

    def block(self):
        if self.kind == "image":
            return {"type": "image", "source": {"type": "base64", "media_type": self.media_type,
                                                "data": base64.b64encode(self.data).decode()}}
        if self.kind == "pdf":
            return {"type": "document", "title": self.name,
                    "source": {"type": "base64", "media_type": "application/pdf",
                               "data": base64.b64encode(self.data).decode()}}
        return None

    def thumbnail(self, size, scale=2.0):
        """A square, centre-cropped texture, size logical pixels on a screen of this scale."""
        return thumbnail_texture(self.data, size, scale)

    def icon(self):
        return file_icon(self.name)


def clipboard_has(formats, gtype, mime_prefixes):
    """True if the clipboard offers `gtype` (or a subclass) locally, or a matching MIME type
    from another app. Other apps' clipboards only ever list MIME types."""
    if any(GObject.type_is_a(t, gtype) for t in formats.get_gtypes() or []):
        return True
    return any(m.startswith(p) for m in formats.get_mime_types() or [] for p in mime_prefixes)


def thumbnail_texture(data, size, scale):
    """A square, centre-cropped texture of an image's bytes: size logical pixels on a screen of this scale."""
    pb = _pixbuf_from_bytes(data)
    w, h = pb.get_width(), pb.get_height()
    side, px = min(w, h), max(1, round(size * scale))
    pb = pb.new_subpixbuf((w - side) // 2, (h - side) // 2, side, side).scale_simple(px, px,
                                                                                     GdkPixbuf.InterpType.BILINEAR)
    fmt = Gdk.MemoryFormat.R8G8B8A8 if pb.get_has_alpha() else Gdk.MemoryFormat.R8G8B8
    return Gdk.MemoryTexture.new(px, px, fmt, pb.read_pixel_bytes(), pb.get_rowstride())


def attachment_widget(att, size, on_remove=None):
    """A thumbnail (images) or icon + name, optionally with a remove button. A thumbnail is its size at once, and decoded
    off the main thread as it nears the view (imageviews.FixedPicture): made here, a chat's history stopped for 9 ms an
    image as it was drawn, 300 ms a chunk of screenshots, mid-scroll."""
    box = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER, tooltip_text=att.path or att.name)
    thumb = None
    if att.kind == "image" and widgets.image_dims(att.data):
        thumb = imageviews.FixedPicture(size=(size, size), load=lambda scale, data=att.data:
                                     thumbnail_texture(data, size, scale))
    elif att.kind == "image":  # (a header image_dims can't read: decoded now, to know it's an image at all)
        try:
            thumb = Gtk.Image.new_from_paintable(att.thumbnail(size))
            thumb.set_pixel_size(size)
        except GLib.Error:  # (an image no loader here reads, say a HEIC sent from elsewhere: shown as a file is)
            thumb = None
    if thumb is not None:
        thumb.add_css_class("attach-thumb")
        make_image_interactive(thumb, att.data, att.path, att.name)
        box.append(thumb)
    else:
        icon = Gtk.Image.new_from_gicon(att.icon())
        box.append(icon)
        name = Gtk.Label(label=att.name, ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=22)
        box.append(name)
        if att.path and thumbs.wanted(att.path):  # (a PDF, a video: the system's thumbnail of it, as it has one)
            side = size * 2 // 3
            icon.set_pixel_size(side)  # (its room from the start: a chat's history doesn't move when it comes)

            def shown(tex):
                if tex is not None:
                    icon.set_from_paintable(tex)
                    icon.set_pixel_size(side)
                    icon.add_css_class("attach-thumb")
            thumbs.thumbnail(att.path, round(side * widgets.screen_scale()), shown)
    if on_remove:
        rm = Gtk.Button(icon_name="window-close-symbolic", tooltip_text=wording.say("common.remove"),
                        valign=Gtk.Align.CENTER)
        rm.add_css_class("flat")
        rm.add_css_class("circular")
        rm.connect("clicked", lambda *_: on_remove(att))
        box.append(rm)
    box.add_css_class("attach-chip")
    if thumb is not None and not on_remove:
        box.add_css_class("image")
    return box


# --------------------------------------------------------- slash completer --

LOCAL_COMMANDS = [  # handled by baude itself, never sent to claude
    {"name": "new", "description": wording.say("slash_commands.new"), "argumentHint": "", "aliases": ["clear"]},
    {"name": "cd", "description": wording.say("slash_commands.cd"), "argumentHint": "<path>"},
    {"name": "btw", "description": wording.say("slash_commands.btw"),
     "argumentHint": "<question>"},
    {"name": "login", "description": wording.say("slash_commands.login"),
     "argumentHint": "[--console | --sso | --email <address>]"},
    {"name": "logout", "description": wording.say("slash_commands.logout"), "argumentHint": ""},
]


# ------------------------------------------------------------- /login --

_AUTH_ESCAPES = hostlogin.ESCAPES


class _HereLogin:
    """claude auth login run on this machine, for LoginCard (it needs no terminal: it writes lines, and reads a pasted
    code from its input): heard(self, line) as it writes each, done(self, problem, code, who) once it's over (who: as
    claude auth status says, signed in). OSError if it can't be started."""

    def __init__(self, args, heard, done):
        self.proc = subprocess.Popen([claude_exe(), "auth", "login", *args], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                                     env=claude_env())
        threading.Thread(target=self._read, args=(heard, done), daemon=True, name="login").start()

    def _read(self, heard, done):
        for line in self.proc.stdout:
            GLib.idle_add(heard, self, line)
        code = self.proc.wait()
        who = signed_in_as(auth_status()) if code == 0 else None
        GLib.idle_add(done, self, None, code, who)

    def paste(self, code):
        if self.proc.poll() is not None:
            return False
        try:
            self.proc.stdin.write(code + "\n")
            self.proc.stdin.flush()
        except OSError:
            return False
        return True

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()


class _HostLogin:
    """claude auth login run on another machine, by its host process (hostlogin there), on a connection of its own to
    it (through the mesh, if that's how it's reached), for LoginCard as _HereLogin is; stop() lets go of the
    connection, which stops it there."""

    REQ = 1

    def __init__(self, where, args, heard, done):
        self.ch, self.stopped = None, False
        threading.Thread(target=self._run, args=(where, list(args), heard, done), daemon=True,
                         name="host-login").start()

    def _run(self, where, args, heard, done):
        try:
            ch, _info = wire.reach(where, timeout=10)
        except OSError as e:
            GLib.idle_add(done, self, str(e) or wording.say("host_status.no_answer"), None, None)
            return
        self.ch = ch
        if self.stopped:
            ch.close()
        ch.sock.settimeout(wire.SILENT_S)
        ch.keep_alive()
        ch.send({"op": "login", "req": self.REQ, "args": args})
        end = None
        for msg in ch:
            if msg.get("req") != self.REQ:
                continue
            if msg.get("op") == "login_said":
                GLib.idle_add(heard, self, (msg.get("line") or "") + "\n")
            elif msg.get("op") == "login_end":
                end = (msg.get("why"), msg.get("code"), msg.get("who"))
                break
            elif msg.get("op") == "error":
                end = (wire.said(msg), None, None)
                break
        ch.close()
        if self.stopped:
            end = (None, -15, None)
        GLib.idle_add(done, self, *(end or (wording.say("host_status.connection_dropped"), None, None)))

    def paste(self, code):
        return self.ch is not None and self.ch.send({"op": "login_code", "code": code})

    def stop(self):
        self.stopped = True
        if self.ch is not None:
            self.ch.close()


class _AccountLogin:
    """claude auth login run with a Claude account's own folder, by its host process (hostaccounts' sign_in there),
    for LoginCard as _HereLogin is: its lines heard, a pasted code passed on (code), stop() lets go of the connection,
    which stops it there."""

    def __init__(self, where, profile, heard, done):
        import subscriptionpage

        def answered(reply):
            done(self, reply.get("error"), 0 if reply.get("signed_in") else 1, reply.get("who"))
            return False
        self.ask = subscriptionpage.Ask(where, {"what": "sign_in", "id": profile}, answered,
                                        lambda line: heard(self, line + "\n"))

    def paste(self, code):
        return self.ask.send({"what": "code", "code": code})

    def stop(self):
        self.ask.stop()


class LoginCard(Gtk.Box):
    """Signing in to Claude in the chat, on the machine the chat runs on: claude auth login run there (this one's here;
    another machine's by its host process: where, its wire.reach place, and host, its name). /login starts it at once;
    a turn that failed for want of it (ask) first says so, with why and Sign in. The sign-in page opens in the browser
    here (another machine's, its link to open); if the page shows a code instead of coming back by itself, it's pasted
    in the box; Cancel stops it. Once it's done, who's signed in, and the chat's claude started again on it
    (win.signed_in); retry: what the failed turn sent from here (its text and attachments), Send again to send it again
    (win.send_again); the chat it's in is the one started again, whichever's open when it's done.
    args: claude auth login's (--console, --sso, --email …). account: the chat's Claude account (Account's fields),
    for a chat run under one, signed in to in place of the CLI's own sign-in, on its host (account_where): a folder
    account's by claude auth login with its folder (_AccountLogin), a token account's by a token from claude
    setup-token pasted in place of it, replacing that account's."""

    def __init__(self, win, args=(), where=None, host=None, ask=None, retry=None, account=None, account_where=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("perm-card")
        self.win, self.args, self.where, self.host, self.retry = win, list(args), where, host, retry
        self.account, self.account_where = account, account_where
        self.chat = (win.remote, win.session_id) if hasattr(win, "at_chat") else None  # (the chat it's in: signed in for)
        self.proc, self.said = None, []
        self.title = sel_label("")
        self.body = sel_label("")
        self.append(self.title)
        self.append(self.body)
        self.row = Gtk.Box(spacing=6, visible=False)
        self.code = Gtk.Entry(hexpand=True, placeholder_text=wording.say("sign_in.code_hint"))
        self.code.connect("activate", lambda *_: self.paste())
        self.send = Gtk.Button(label=wording.say("common.send"))
        self.send.connect("clicked", lambda *_: self.paste())
        self.cancel = Gtk.Button(label=wording.say("common.cancel"))
        self.cancel.connect("clicked", lambda *_: self.stop())
        for w in (self.code, self.send, self.cancel):
            self.row.append(w)
        self.append(self.row)
        self.again = Gtk.Button(label=wording.say("common.try_again"), halign=Gtk.Align.START, visible=False)
        self.again.add_css_class("suggested-action")
        self.again.connect("clicked", lambda *_: self.start())
        self.append(self.again)
        self.resend = Gtk.Button(label=wording.say("sign_in.send_again"), halign=Gtk.Align.START, visible=False)
        self.resend.add_css_class("suggested-action")
        self.resend.connect("clicked", lambda *_: self.send_again())
        self.append(self.resend)
        if account is not None and account.get("kind") == "token":
            return self.token_box(ask)
        if ask is None:
            self.start()
            return
        self.say(wording.say("subscriptions.sign_in_heading", name=account["name"]) if account is not None else
                 wording.say("sign_in.not_signed_in_on", host=host) if host else wording.say("sign_in.not_signed_in"),
                 hostlogin.reason(ask), dim=True)
        self.again.set_label(wording.say("sign_in.sign_in"))
        self.again.set_visible(True)

    def token_box(self, ask):
        """A chat under a Claude account: its token box (the account's name, why it failed if it did), Save replacing
        the account's token on the chat's host, then the chat's claude started again on it as a sign-in's is."""
        self.say(wording.say("subscriptions.replace_heading", name=self.account["name"]),
                 hostlogin.reason(ask) if ask else "", dim=True)
        row = Gtk.Box(spacing=6)
        token = Gtk.PasswordEntry(hexpand=True, show_peek_icon=True,
                                  placeholder_text=wording.say("subscriptions.token_hint"))
        save = Gtk.Button(label=wording.say("common.save"))
        save.add_css_class("suggested-action")
        row.append(token)
        row.append(save)
        self.append(row)

        def saved(error):
            save.set_sensitive(True)
            token.set_sensitive(True)
            if error:
                self.say(wording.say("sign_in.failed"), error, dim=True)
                return
            row.set_visible(False)
            self.say(wording.say("subscriptions.token_saved"), self.account["name"])
            self.resend.set_visible(bool(self.retry) and (self.chat is None or self.win.at_chat(self.chat)))
            self.win.signed_in(self.chat) if self.chat is not None else self.win.signed_in()

        def send(*_):
            if not token.get_text().strip():
                return
            save.set_sensitive(False)
            token.set_sensitive(False)
            import subscriptionpage
            subscriptionpage.replace_token(self.account_where, self.account["id"], token.get_text().strip(), saved)
        save.connect("clicked", send)
        token.connect("activate", send)

    def say(self, title, body="", dim=False, markup=False):
        self.title.set_markup(f"<b>{html.escape(title)}</b>")
        text = body if markup else html.escape(body or "")
        self.body.set_markup(f"<span alpha='60%'>{text}</span>" if dim and text else text)
        self.body.set_visible(bool(body))

    def start(self):
        self.say(wording.say("sign_in.signing_in"), wording.say("sign_in.starting"), dim=True)
        self.row.set_visible(True)
        self.again.set_visible(False)
        self.again.set_label(wording.say("common.try_again"))
        self.said = []
        try:
            if self.account is not None:
                self.proc = _AccountLogin(self.account_where, self.account["id"], self.heard, self.done)
            elif self.where is not None:
                self.proc = _HostLogin(self.where, self.args, self.heard, self.done)
            else:
                self.proc = _HereLogin(self.args, self.heard, self.done)
        except OSError as e:
            self.proc = None
            self.done(None, f"couldn't start claude: {e}")

    def heard(self, proc, line):
        """A line from claude auth login: the sign-in page's address shown as a link; anything else kept, for if it
        fails."""
        if proc is not self.proc:
            return False
        # (its prompt has no line break: what it says next, "Login failed: …", comes on the prompt's line)
        line = _AUTH_ESCAPES.sub("", line).replace("Paste code here if prompted > ", "").strip()
        if m := re.search(r"https://\S+", line):
            url = html.escape(m.group(0).rstrip(".,)"))
            if self.where is None:
                link = f"<a href=\"{url}\">{html.escape(wording.say('sign_in.open_page'))}</a>"
                said = html.escape(wording.say("sign_in.browser_opened", link="\x00")).replace("\x00", link)
            else:  # (another machine's: its browser isn't here, nor maybe anywhere: the page opened here)
                link = f"<a href=\"{url}\">{html.escape(wording.say('sign_in.open_page_there'))}</a>"
                said = html.escape(wording.say("sign_in.page_there", link="\x00")).replace("\x00", link)
            self.body.set_markup(said)
            self.body.set_visible(True)
        elif line and not line.startswith("Opening browser"):
            self.said.append(line)
        return False

    def paste(self):
        code = self.code.get_text().strip()
        if not code or self.proc is None or not self.proc.paste(code):
            return
        self.code.set_text("")
        self.body.set_markup(f"<span alpha='60%'>{html.escape(wording.say('sign_in.checking_code'))}</span>")

    def stop(self):
        if self.proc is not None:
            self.proc.stop()

    def done(self, proc, problem=None, code=None, who=None):
        """It's finished: signed in (who as), or why not, with Try again."""
        if proc is not None and proc is not self.proc:
            return False
        self.proc = None
        self.row.set_visible(False)
        if code == 0:
            return self.signed_in(who)
        why = problem or next((x for x in reversed(self.said) if x.lower().startswith(("login failed", "error"))),
                              None) or (self.said[-1] if self.said else None)
        cancelled = code in (-15, 130) and not problem
        self.say(wording.say("sign_in.cancelled") if cancelled else wording.say("sign_in.failed"),
                 why if why and code != -15 else "", dim=True)
        self.again.set_visible(True)
        return False

    def signed_in(self, who):
        self.say(wording.say("sign_in.signed_in"), wording.say("sign_in.as_who", who=who) if who else
                 wording.say("sign_in.who_unknown"), dim=not who)
        self.resend.set_visible(bool(self.retry) and (self.chat is None or self.win.at_chat(self.chat)))
        self.win.signed_in(self.chat) if self.chat is not None else self.win.signed_in()
        return False

    def send_again(self):
        """Send again: what the failed turn sent, sent again (once the chat's claude has started on the new sign-in)."""
        if self.retry and self.win.send_again(self.retry):
            self.resend.set_visible(False)


class SlashCompleter:
    """/'s list, over the composer while the word the caret's at the end of starts with '/': the prompt's first word,
    or, anywhere in it, a / just typed at a word's start (after a space or a new line), as @ is in Record. Not a / in
    a path (src/app) or a link (https://…), nor in code. The commands, Baude's own and claude's, as they're typed,
    each with whose it is (Claude Code's own: its logo), its arguments and what it does. It looks and goes as Record's
    @ list does (widgets.PickList): ↑ ↓ Page Up and Page Down move, Enter or Tab or a click puts the command in where
    its /word was, Esc leaves it. (Only a prompt's first word is a command to Claude Code: one later on is words to
    Claude, which takes a skill's as asking for it.)"""

    WORD = re.compile(r"(?:^|(?<=\s))/(\S*)$")  # (a /word at the end of what's before the caret, at a word's start)

    def __init__(self, textview, anchor):
        self.view, self.anchor, self.commands, self.matches = textview, anchor, list(LOCAL_COMMANDS), []
        self.q, self.moved = None, False  # (what's being typed, and whether the arrows moved in its list)
        self.picks = widgets.PickList(anchor, self._bind, self.accept, position=Gtk.PositionType.TOP,
                                      about_ellipsis=Pango.EllipsizeMode.END)
        self.pop = self.picks.pop
        self.pop.set_halign(Gtk.Align.START)  # (its left side on the /'s: PickList.over)
        self.pop.set_offset(0, -10)  # a clear gap above the input box
        buf = textview.get_buffer()
        # (where a / was just typed: past the prompt's start, only that one starts the list, as only an @ just typed
        # does in Record; text pasted or put back that has a /word in it doesn't. A mark where it went in, so it goes
        # with the text, and how far into that the / is)
        self.typed, self.typed_in = buf.create_mark(None, buf.get_start_iter(), True), 0
        buf.connect("insert-text", self._inserting)
        buf.connect("changed", lambda *_: self.refresh())

    def _inserting(self, buf, at, text, _n):
        if "/" in text and len(text) <= 2:
            buf.move_mark(self.typed, at)
            self.typed_in = text.rfind("/")

    def set_commands(self, cmds):
        seen = {n for c in LOCAL_COMMANDS for n in [c["name"], *c.get("aliases", [])]}
        self.commands = list(LOCAL_COMMANDS) + sorted(
            (c for c in cmds if c.get("name") and c["name"] not in seen), key=lambda c: c["name"])
        self.refresh()

    def word(self):
        """(where its / is, what's typed after it) of the /word being typed, or None if we shouldn't complete: the
        caret's at its end (a space, a new line or the text's end next), and it's the prompt's first word or its / was
        just typed, not in code."""
        buf = self.view.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        cursor = buf.get_iter_at_mark(buf.get_insert()).get_offset()
        m = self.WORD.search(text, 0, cursor)
        if not m or text[cursor:cursor + 1].strip() or \
                m.start() and m.start() != buf.get_iter_at_mark(self.typed).get_offset() + self.typed_in or \
                self.in_code(text, m.start()):
            return None
        return m.start(), m.group(1).lower()

    def query(self):
        """The '/word' being typed, or None if we shouldn't complete."""
        word = self.word()
        return word and word[1]

    @staticmethod
    def in_code(text, offset):
        """Whether a place in the text is in a code block or a `code` span (a / there is a path's, a shell's)."""
        before = text[:offset]
        at = before.count("\n")
        return any(first <= at <= last for first, last, _closed in
                   markdownview.fence_spans(text.split("\n"), tildes=False, typing=(at,))) or \
            before.rpartition("\n")[2].count("`") % 2 == 1

    def refresh(self):
        word = self.word()
        q = word and word[1]
        if q is None or "/" in q:  # (a path's, not a command)
            return self.hide()
        def rank(c):
            name, aliases = c["name"].lower(), [a.lower() for a in c.get("aliases") or []]
            if name.startswith(q):
                return 0
            if any(a.startswith(q) for a in aliases):
                return 1
            if any(q in n for n in [name, *aliases]):
                return 2
            return 3 if q and q in (c.get("description") or "").lower() else None
        i = self.picks.picked()
        kept = self.matches[i]["name"] if self.moved and q == self.q and i is not None and i < len(self.matches) \
            else None  # (the same, listed again, as claude's commands come in: what's picked stays picked)
        scored = [(r, c) for c in self.commands if (r := rank(c)) is not None]
        self.matches = [c for _, c in sorted(scored, key=lambda rc: rc[0])]
        at = next((j for j, c in enumerate(self.matches) if c["name"] == kept), None)
        self.q, self.moved = q, at is not None
        n = len(self.matches)
        # (as wide as @'s, or the input box, if that's narrower: its padding and line either side as well)
        wide, most = self.anchor.get_width() - 10, widgets.PickList.WIDTH
        self.picks.scroll.set_size_request(min(most, wide) if wide > 0 else most, -1)
        self.picks.show(n, at or 0, wording.say("meta.found", n=n) if n else wording.say("meta.nothing_found"),
                        widgets.PickList.over(self.view, word[0], self.anchor))

    def _bind(self, i, icon, name, about):
        c = self.matches[i]
        icon.icon = "claude" if c.get("builtin") else "cli"
        name.set_text("/" + c["name"])
        desc = (c.get("description") or "").strip()
        also = ", ".join("/" + a for a in c.get("aliases") or [])
        about.set_text(" · ".join(x for x in (also and wording.say("slash_commands.also", names=also), c.get("argumentHint") or "",
                                              desc.split("\n")[0]) if x))
        about.set_tooltip_text(desc or None)

    def visible(self):
        return self.picks.visible()

    def hide(self):
        self.moved = False
        self.picks.hide()

    def accept(self):
        """The picked command in place of the /word, the caret after it and a space (the space hides us; one that was
        next already goes, so there's one)."""
        i, word = self.picks.picked(), self.word()
        if i is None or i >= len(self.matches) or word is None:
            return
        name, buf = f"/{self.matches[i]['name']} ", self.view.get_buffer()
        end = buf.get_iter_at_mark(buf.get_insert())
        if end.get_char() == " ":
            end.forward_char()
        buf.begin_user_action()
        buf.delete(buf.get_iter_at_offset(word[0]), end)
        buf.insert_at_cursor(name)
        buf.end_user_action()
        self.view.grab_focus()

    def handle_key(self, keyval, state=0):
        """Returns True if the key was consumed by the popover."""
        if not self.visible():
            return False
        steps = widgets.PickList.STEPS
        if state & Gdk.ModifierType.SHIFT_MASK and keyval in (*steps, Gdk.KEY_Left, Gdk.KEY_Right):
            self.hide()  # (Shift and an arrow: selecting the text, not picking in the list)
            return False
        if keyval in steps:
            if self.matches:
                self.picks.move(steps[keyval])
                self.moved = True
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and composerview.sends(state):
            self.hide()  # (Ctrl/⌘+Enter sends what's typed, past the list)
            return False
        elif keyval in (Gdk.KEY_Tab, Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self.picks.picked() is not None:
            self.accept()
        elif keyval == Gdk.KEY_Escape:
            self.hide()
            return bool(self.matches)  # ("Nothing found": Esc does what it would have, as a turn's stop, too)
        else:
            return False
        return True


class ChatRefCompleter(metalook.RefCompleter):
    """@'s list over a chat's box, as Meta's box has it (metalook.RefCompleter), not in code (an @ there is a
    decorator's, an address's), nor while /'s list is up. Enter takes what's picked while what's typed after the @ is
    one word, or once the arrows have moved to it; else it's a new line. Ctrl+Enter (⌘Enter) always sends, past the
    list. Esc over "Nothing found" is the box's (a turn's stop), as with /'s list."""

    def __init__(self, space, view, anchor, slash):
        self.slash = slash
        super().__init__(space, view, anchor)

    def word(self):
        word = super().word()
        if word is None or self.slash.visible():
            return None
        buf = self.view.get_buffer()
        return None if SlashCompleter.in_code(buf.get_text(*buf.get_bounds(), True), word[0]) else word

    def handle_key(self, keyval, state=0):
        if not self.visible():
            return False
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and (
                composerview.sends(state) or state & Gdk.ModifierType.SHIFT_MASK or
                not self.moved and " " in (self.word() or (0, ""))[1].strip()):
            self.forget()
            return False
        if keyval == Gdk.KEY_Escape:
            found = bool(self.shown)
            self.forget()
            return found
        return super().handle_key(keyval, state)
