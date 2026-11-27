"""inboxview — Baude's Inbox (schema/ui/Inbox.page.json, InboxAsk.page.json): the row over Search sessions
(InboxRow), the Inbox in a tab of the main area (InboxPage: every host's asks of a person, each a card cut a few lines
down, then the chats waiting on a permission and those at a turn), and an ask in a tab of its own (AskPage). An ask
(AskView) is drawn as a message is: who it's from and ⋯ (History), its Markdown with its pictures (AskMarkdown), its
form as setting rows, the task's buttons wrapping as whole buttons (Adw.WrapBox; its picture buttons large, a few to a
row: PictureGrid, AskView.picture_button) and Route to…, a model's answer to confirm, and, opened, a box that writes to the asking task's Bot. What's shown is what each host says
(baudelib.client.Remote.inbox, as inbox_changed tells it); what's done is asked of the ask's host (baudelib/inbox.py).
"""
import base64
import datetime
import re
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

import composerview  # noqa: E402
import imageviews  # noqa: E402
import markdownview  # noqa: E402
import pickers  # noqa: E402
from baudelib import providers as providerslib  # noqa: E402
from baudelib import tokens, wording  # noqa: E402
from baudelib.client import chat_name  # noqa: E402
from baudelib.tasks import number_of  # noqa: E402
from sessionlist import bot_picture, network, remote_info  # noqa: E402
from widgets import Spinner, discard, ensure_icons, toast  # noqa: E402

CUT = 160  # (px of an ask's body shown in the Inbox's list before Show more)
FADE = 48  # (px the cut fades over)
PICTURE_BUTTON = 240  # (a picture button, at least, wide: as many to a row as fit, PICTURE_ROW at most)
PICTURE_ROW = 3
PICTURE_TALL = 1.25  # (a picture button's picture, at most, this many times as tall as it's wide)
COLOURS = ("blue", "teal", "green", "yellow", "orange", "red", "pink", "purple", "slate")
_IMAGE = re.compile(r"^\s*!\[([^\]]*)\]\(([^)\s]+)\)\s*$")
_PICTURES = {}  # ((host key, ask id, version, name): its bytes, as fetched) — the last PICTURES_KEPT
_FETCHING = {}  # (the same key: what waits on it)
PICTURES_KEPT = 64


def css():
    """The ask's buttons in the accent palette's colours (schema/tokens accent.*), its pictures' corners, its cut's
    fade into the card: once per display."""
    display = Gdk.Display.get_default()
    if display is None or getattr(display, "_inbox_css", False):
        return
    display._inbox_css = True
    rules = [f"button.ask-{c} {{ background-color: {tokens.color_hex('accent.' + c)}; color: "
             f"{'rgba(0, 0, 0, 0.8)' if c == 'yellow' else 'white'}; }}\n"
             f"button.ask-{c}:hover {{ background-image: image(alpha(currentColor, 0.1)); }}\n"
             f"button.ask-{c}:active {{ background-image: image(alpha(black, 0.12)); }}" for c in COLOURS]
    rules.append(f"""
.ask-card {{ padding: 14px 16px 16px 16px; }}
.ask-picture {{ border-radius: 12px; }}
.picture-frame {{ border-radius: 12px; border: 1px solid alpha(currentColor, 0.12); }}
.picture-frame picture:hover {{ filter: brightness(0.94); }}
.picture-view {{ margin: 8px; padding: 6px; border-radius: 99px; background-color: alpha(black, 0.55); color: white; }}
button.picture-press {{ padding: 8px 12px; font-weight: bold; }}
.ask-fade {{ background-image: linear-gradient(alpha(@card_bg_color, 0), @card_bg_color); min-height: {FADE}px; }}
.ask-gone {{ opacity: 0.55; }}
.ask-proposal {{ padding: 8px 12px; border-radius: 10px; background-color: alpha(@accent_bg_color, 0.1); }}
button.ask-suggested {{ outline: 2px solid alpha(@accent_color, 0.8); outline-offset: 1px; }}
""")
    p = Gtk.CssProvider()
    p.load_from_string("\n".join(rules))
    Gtk.StyleContext.add_provider_for_display(display, p, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2)


def when(t):
    """When an ask came, as the list says it: 09:12 today, Mon 09:12 this week, else 3 Nov."""
    at = datetime.datetime.fromtimestamp(t)
    now = datetime.datetime.now()
    if at.date() == now.date():
        return at.strftime("%H:%M")
    if (now.date() - at.date()).days < 7:
        return at.strftime("%a %H:%M")
    return f"{at.day} {at.strftime('%b')}"


def latest(ask):
    return ask["versions"][-1]


def title_of(version):
    """An ask's title: its own, else its body's first line that says something (baudelib.inbox.title_of)."""
    from baudelib.inbox import title_of as titled
    return titled(version)


def waiting_count():
    return sum(a.get("status") == "waiting" for a in network().asks())


def find_ask(key, aid):
    """The ask aid of the host key, as it says now (with its host: server), or None."""
    r = next((r for r in network().remotes() if r.key == key), None)
    ask = r.inbox.get(aid) if r is not None else None
    return {**ask, "server": r} if ask is not None else None


def bot_info(ask):
    """The asking Bot as its host lists it (remote_info's), or None."""
    r = ask["server"]
    listed = r.sessions.get(ask["session"])
    return remote_info({**listed, "server": r}) if listed else None


def avatar(info, name, size=20):
    a = Adw.Avatar(size=size, text=name or "Bot", show_initials=True, valign=Gtk.Align.CENTER)
    if info is not None:
        bot_picture(info, lambda tex: tex is not None and a.set_custom_image(tex))
    return imageviews.shaded_picture(a)


def picture(ask, n, name, then):
    """then(bytes or None) with one of an ask's pictures (its version n), fetched from its host once."""
    r = ask["server"]
    key = (r.key, ask["id"], n, name)
    if key in _PICTURES:
        return then(_PICTURES[key])
    if key in _FETCHING:
        return _FETCHING[key].append(then)
    _FETCHING[key] = [then]

    def got(reply):
        data = None
        if reply.get("op") == "inbox_picture_data":
            try:
                data = base64.b64decode(reply.get("data") or "")
            except ValueError:
                data = None
        if data is not None:
            _PICTURES[key] = data
            while len(_PICTURES) > PICTURES_KEPT:
                _PICTURES.pop(next(iter(_PICTURES)))
        for fn in _FETCHING.pop(key, []):
            fn(data)
    r.request({"op": "inbox_picture", "ask": ask["id"], "version": n, "name": name}, got)


class WidePicture(Gtk.Picture):
    """A picture between paragraphs: as wide as the text, its height as its shape has it (known before it's here,
    from the size its host lists), rounded; click to see it large."""

    def __init__(self, width, height, alt):
        super().__init__(can_shrink=True, content_fit=Gtk.ContentFit.FILL, hexpand=True, alternative_text=alt,
                         overflow=Gtk.Overflow.HIDDEN)
        self.add_css_class("ask-picture")
        self.shape = (width or 4, height or 3)

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        w, h = self.shape
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 0, w, -1, -1
        n = round((for_size if for_size > 0 else w) * h / w)
        return n, n, -1, -1


class SizedPicture(Gtk.Picture):
    """A picture drawn at a size of its own (a table cell's, a picture button's), whatever the size of its bytes."""

    def __init__(self, width, height, alt):
        super().__init__(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN, alternative_text=alt,
                         overflow=Gtk.Overflow.HIDDEN, halign=Gtk.Align.CENTER, valign=Gtk.Align.START)
        self.add_css_class("ask-picture")
        self.size = (width, height)

    def do_measure(self, orientation, for_size):
        n = self.size[0 if orientation == Gtk.Orientation.HORIZONTAL else 1]
        return n, n, -1, -1


class PicturePart(WidePicture):
    """A picture button's picture: as wide as its button, its height as its shape has it (PICTURE_TALL times its width
    at most, shown whole in that); click to see it large."""

    def __init__(self, width, height, alt):
        super().__init__(width, height, alt)
        self.set_content_fit(Gtk.ContentFit.CONTAIN)

    def do_measure(self, orientation, for_size):
        w, h = self.shape
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 0, w, -1, -1
        n = round((for_size if for_size > 0 else w) * min(h / w, PICTURE_TALL))
        return n, n, -1, -1


class PictureGrid(Gtk.Widget):
    """An ask's picture buttons, as many to a row as fit at least `least` wide each (`most` at most), each as wide as
    the row shares out, a row as tall as its tallest: a picture button wraps as a whole."""

    def __init__(self, least, most, gap):
        super().__init__(hexpand=True)
        self.least, self.most, self.gap, self.kids = least, most, gap, []

    def append(self, child):
        child.set_parent(self)
        self.kids.append(child)

    def do_dispose(self):
        for k in self.kids:
            k.unparent()
        self.kids = []

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def cells(self, width):
        """(columns, each one's width) at this width."""
        n = max(1, min(self.most, (width + self.gap) // (self.least + self.gap)))
        return n, max(1, (width - self.gap * (n - 1)) // n)

    def rows(self, width):
        n, w = self.cells(width)
        out = []
        for i in range(0, len(self.kids), n):
            row = self.kids[i:i + n]
            out.append((row, max(k.measure(Gtk.Orientation.VERTICAL, w)[1] for k in row)))
        return n, w, out

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            least = max([k.measure(orientation, -1)[0] for k in self.kids] or [0])
            return least, self.least * self.most + self.gap * (self.most - 1), -1, -1
        _n, _w, rows = self.rows(for_size if for_size > 0 else self.least * self.most)
        h = sum(r[1] for r in rows) + self.gap * max(0, len(rows) - 1)
        return h, h, -1, -1

    def do_size_allocate(self, width, height, baseline):
        _n, w, rows = self.rows(width)
        y = 0
        for row, h in rows:
            for c, k in enumerate(row):
                box = Gdk.Rectangle()
                box.x, box.y, box.width, box.height = c * (w + self.gap), y, w, h
                k.size_allocate(box, -1)
            y += h + self.gap


def sized(meta, edge, alt):
    """A SizedPicture for one of an ask's pictures (meta: as its host lists it), its longest side edge at most."""
    w, h = meta.get("width") or edge, meta.get("height") or edge
    scale = min(1.0, edge / max(w, h))
    return SizedPicture(round(w * scale), round(h * scale), alt)


class AskMarkdown(markdownview.MarkdownView):
    """An ask's body: Markdown as a message's, a picture on a line of its own as wide as the text, one alone in a
    table's cell shown there, never a file path; each fetched from the ask's host by its name."""

    def __init__(self, ask, n):
        self.ask, self.n = ask, n
        self.listed = {p["name"]: p for p in ask["versions"][n - 1]["pictures"]}
        super().__init__(ask["versions"][n - 1]["body"])

    def _load(self, name, alt, show):
        if name not in self.listed:
            return
        def got(data):
            if data is None:
                return
            try:
                show(data)
            except GLib.Error:
                pass
        picture(self.ask, self.n, name, got)

    def render_image(self, alt, src):
        meta = self.listed.get(src)
        if meta is None:
            return markdownview.sel_label(GLib.markup_escape_text(alt or ""))
        pic = WidePicture(meta.get("width"), meta.get("height"), alt or src)

        def show(data):
            pic.set_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(data)))
            imageviews.make_image_interactive(pic, data, name=alt or src)
        self._load(src, alt, show)
        return pic

    def render(self, b):
        if b[0] != "table":
            return super().render(b)
        rows = b[1]
        scroll = markdownview.markdown_table(rows)
        grid = scroll.grid
        for r, cells in enumerate(rows):
            for c, cell in enumerate(cells):
                m = _IMAGE.match(cell)
                if not m or m.group(2) not in self.listed:
                    continue
                meta = self.listed[m.group(2)]
                holder = Gtk.Box(css_classes=["md-cell"], valign=Gtk.Align.FILL)
                pic = sized(meta, 210, m.group(1))
                pic.set_halign(Gtk.Align.START)
                holder.append(pic)

                def show(data, pic=pic, alt=m.group(1)):
                    pic.set_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(data)))
                    imageviews.make_image_interactive(pic, data, name=alt)
                self._load(m.group(2), m.group(1), show)
                old = grid.get_child_at(c, r)
                if old is not None:
                    grid.remove(old)
                grid.attach(holder, c, r, 1, 1)
        return scroll


class Cut(Gtk.Widget):
    """Its child shown down to height at most (open: all of it); cut: told as it changes (on_cut(bool))."""

    def __init__(self, child, height, on_cut):
        super().__init__(overflow=Gtk.Overflow.HIDDEN, hexpand=True)
        self.child, self.height, self.open, self.cut, self.on_cut = child, height, False, None, on_cut
        child.set_parent(self)

    def do_dispose(self):
        if self.child is not None:
            self.child.unparent()
            self.child = None

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        mn, nat, _b1, _b2 = self.child.measure(orientation, for_size)
        if orientation == Gtk.Orientation.VERTICAL and not self.open:
            return min(mn, self.height), min(nat, self.height), -1, -1
        return mn, nat, -1, -1

    def do_size_allocate(self, width, height, baseline):
        nat = self.child.measure(Gtk.Orientation.VERTICAL, width)[1]
        self.child.allocate(width, max(height, nat), -1, None)
        cut = nat > self.height + 1
        if cut != self.cut:
            self.cut = cut
            GLib.idle_add(lambda: (self.on_cut(cut), False)[1])

    def do_snapshot(self, snapshot):
        self.snapshot_child(self.child, snapshot)

    def set_open(self, on):
        self.open = on
        self.queue_resize()


def field_rows(form):
    """An ask's form as setting rows: {name: (row, read(), write(value))}. A text field fills its row's width (its
    label in it); a number's entry at the row's end; on/off a switch; a choice a drop-down."""
    out = {}
    for f in form:
        title = f.get("label") or f["name"]
        default = f.get("default")
        if f["type"] == "bool":
            row = Adw.SwitchRow(title=title, use_markup=False, active=bool(default))
            out[f["name"]] = (row, row.get_active, lambda v, row=row: row.set_active(bool(v)))
        elif f["type"] == "choice":
            choices = f.get("choices") or [""]
            row = Adw.ComboRow(title=title, use_markup=False, model=Gtk.StringList.new(choices))
            if default in choices:
                row.set_selected(choices.index(default))
            out[f["name"]] = (row, lambda row=row, c=choices: c[row.get_selected()],
                              lambda v, row=row, c=choices: v in c and row.set_selected(c.index(v)))
        elif f["type"] == "number":
            entry = Gtk.Entry(valign=Gtk.Align.CENTER, width_request=150, input_purpose=Gtk.InputPurpose.NUMBER,
                              text="" if default is None else str(default))
            row = Adw.ActionRow(title=title, use_markup=False, activatable_widget=entry)
            row.add_suffix(entry)

            def read(entry=entry):
                try:
                    return number_of(entry.get_text())
                except ValueError:
                    return entry.get_text()
            out[f["name"]] = (row, read, lambda v, entry=entry: entry.set_text("" if v is None else str(v)))
        else:
            row = Adw.EntryRow(title=title, use_markup=False, text="" if default is None else str(default))
            out[f["name"]] = (row, row.get_text, lambda v, row=row: row.set_text("" if v is None else str(v)))
    return out


def route_menu(ask, pick_model, pick_bot):
    """Route to…'s popover: Have a model answer (the ask's host's models, as the model picker names them), then Send
    to a Bot (that host's Bots)."""
    r = ask["server"]
    view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6, margin_bottom=6)
    pop = Gtk.Popover(child=view, has_arrow=False)

    def heading(text):
        view.append(Gtk.Label(label=text, xalign=0, css_classes=["heading", "dim-label"], margin_start=12,
                              margin_end=12, margin_top=4, margin_bottom=2))

    def item(icon, text, fn):
        box = Gtk.Box(spacing=8)
        box.append(icon)
        box.append(Gtk.Label(label=text, xalign=0, css_classes=["body"]))
        b = Gtk.Button(child=box, css_classes=["flat"])
        b.connect("clicked", lambda *_: (pop.popdown(), fn()))
        view.append(b)

    listed = r.info.get("models") or []
    models = [m for m in listed if isinstance(m, dict) and isinstance(m.get("value"), str)
              and m["value"] not in providerslib.DEFAULTS]
    heading(wording.say("inbox.have_model_answer"))
    for m in models[:12]:
        name = providerslib.model_name(m["value"], listed)
        icon = Gtk.Image(icon_name=pickers.model_icon(m["value"], name) or "baude-model-claude-symbolic")
        item(icon, name, lambda m=m: pick_model(m["value"]))
    bots = sorted((s for s in r.sessions.values() if s.get("bot") and not s.get("archived")),
                  key=lambda s: ((s.get("bot") or {}).get("name") or "").lower())
    if bots:
        view.append(Gtk.Separator(margin_top=4, margin_bottom=4))
        heading(wording.say("inbox.send_to_bot"))
        for s in bots:
            name = (s.get("bot") or {}).get("name") or chat_name(s) or "Bot"
            item(avatar(remote_info({**s, "server": r}), name), name, lambda s=s: pick_bot(s["id"]))
    return pop


class AskView(Gtk.Box):
    """One ask: from line and ⋯, its body (cut: in the Inbox's list, a few lines down with Show more), its form, a
    model's answer, its buttons and Route to…. update(ask) shows it as its host says it now, what's typed in its form
    kept."""

    def __init__(self, win, ask, cut=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        css()
        self.win, self.ask, self.cutting = win, ask, cut
        self.shown_n = len(ask["versions"])  # (the version shown: History picks another)
        self.fields, self.drawn = {}, None
        top = Gtk.Box(spacing=8)
        self.face = Gtk.Box()
        top.append(self.face)
        self.from_line = Gtk.Label(xalign=0, hexpand=True, valign=Gtk.Align.CENTER, ellipsize=Pango.EllipsizeMode.END)
        self.from_line.add_css_class("sender-from")
        top.append(self.from_line)
        self.state = Gtk.Label(valign=Gtk.Align.CENTER, css_classes=["dim-label", "caption"], visible=False)
        top.append(self.state)
        self.more = Gtk.MenuButton(icon_name="view-more-symbolic", css_classes=["flat"], valign=Gtk.Align.CENTER,
                                   tooltip_text=wording.say("inbox.more"))
        top.append(self.more)
        self.append(top)
        self.body_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.append(self.body_box)
        self.form_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.append(self.form_box)
        self.proposal = Gtk.Box(spacing=10, visible=False, css_classes=["ask-proposal"])
        self.append(self.proposal)
        self.pictures_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=False)
        self.append(self.pictures_row)
        self.buttons_row = Gtk.Box(spacing=12)
        self.append(self.buttons_row)
        self.image_group = True  # (the large view steps through this ask's pictures: Lightbox.open)
        actions = Gio.SimpleActionGroup()
        pick = Gio.SimpleAction.new_stateful("version", GLib.VariantType.new("s"), GLib.Variant.new_string(""))
        pick.connect("change-state", lambda a, v: self.show_version(int(v.get_string())))
        actions.add_action(pick)
        remove = Gio.SimpleAction.new("remove", None)
        remove.connect("activate", lambda *_: self.request({"op": "inbox_remove", "ask": self.ask["id"]}))
        actions.add_action(remove)
        self.insert_action_group("ask", actions)
        self.version_action = pick
        self.draw()

    # ---- drawn
    def gone(self):
        return self.ask.get("status") == "stopped"

    def live(self):
        """Can it be answered here now: waiting, its latest version shown, its host there."""
        return self.ask.get("status") == "waiting" and self.shown_n == len(self.ask["versions"]) and \
            self.ask["server"].state == "alive"

    def draw(self):
        ask = self.ask
        info = bot_info(ask)
        key = (len(ask["versions"]), self.shown_n, ask.get("status"))
        child = self.face.get_first_child()
        if child is None:
            self.face.append(avatar(info, ask.get("bot")))
        self.from_line.set_label(wording.say("inbox.from_task", task=ask.get("task_name") or "",
                                             host=ask["server"].label))
        state = wording.say("inbox.not_answered") if self.gone() else \
            wording.say("inbox.sent_to", bot=ask["sent_to_name"]) if ask.get("sent_to_name") else ""
        self.state.set_label(state)
        self.state.set_visible(bool(state))
        self.menu()
        if key != self.drawn:
            kept = {name: read() for name, (_row, read, _w) in self.fields.items()}
            self.drawn = key
            self.draw_body()
            self.draw_form(kept)
            self.draw_buttons()
        self.draw_proposal()
        for w in (self.form_box, self.buttons_row, self.route, *self.button_widgets):  # (its pictures still open)
            w.set_sensitive(self.live())

    def menu(self):
        menu, hist = Gio.Menu(), Gio.Menu()
        for n in range(len(self.ask["versions"]), 0, -1):
            v = self.ask["versions"][n - 1]
            when_ = datetime.datetime.fromtimestamp(v["at"])
            day = wording.say("sidebar.today").lower() if when_.date() == datetime.date.today() else \
                when_.strftime("%a")
            item = Gio.MenuItem.new(wording.say("inbox.version_at", n=str(n), when=f"{day} {when_:%H:%M}"), None)
            item.set_action_and_target_value("ask.version", GLib.Variant.new_string(str(n)))
            hist.append_item(item)
        menu.append_section(wording.say("inbox.history"), hist)
        if self.gone():
            menu.append(wording.say("common.remove"), "ask.remove")
        self.version_action.set_state(GLib.Variant.new_string(str(self.shown_n)))
        self.more.set_menu_model(menu)

    def draw_body(self):
        while (c := self.body_box.get_first_child()) is not None:
            self.body_box.remove(c)
            discard(c)
        v = self.ask["versions"][self.shown_n - 1]
        text = v["body"]
        if self.gone():  # (its first heading, struck through)
            text = re.sub(r"^(#{1,6})\s+(.+)$", lambda m: f"{m.group(1)} ~~{m.group(2).strip()}~~", text, count=1,
                          flags=re.M)
        md = AskMarkdown({**self.ask, "versions": [*self.ask["versions"][:self.shown_n - 1], {**v, "body": text}]},
                         self.shown_n)
        md.add_css_class("zoomable")
        if self.gone():
            md.add_css_class("ask-gone")
        self.md = md
        if not self.cutting:
            self.body_box.append(md)
            return
        fade = Gtk.Box(valign=Gtk.Align.END, css_classes=["ask-fade"], can_target=False, visible=False)
        show = Gtk.Button(label=wording.say("git.show_more"), css_classes=["flat"], halign=Gtk.Align.START,
                          visible=False)

        def cut_changed(cut):
            fade.set_visible(cut and not clip.open)
            show.set_visible(cut or clip.open)
        clip = Cut(md, CUT, cut_changed)
        over = Gtk.Overlay(child=clip)
        over.add_overlay(fade)

        def toggle(*_):
            clip.set_open(not clip.open)
            show.set_label(wording.say("inbox.show_less" if clip.open else "git.show_more"))
            fade.set_visible(not clip.open and bool(clip.cut))
        show.connect("clicked", toggle)
        self.body_box.append(over)
        self.body_box.append(show)

    def draw_form(self, kept):
        while (c := self.form_box.get_first_child()) is not None:
            self.form_box.remove(c)
            discard(c)
        form = self.ask["versions"][self.shown_n - 1]["form"]
        self.fields = field_rows(form)
        if not form:
            self.form_box.set_visible(False)
            return
        group = Adw.PreferencesGroup()
        for name, (row, _read, write) in self.fields.items():
            group.add(row)
            if name in kept:
                write(kept[name])
        self.form_box.append(group)
        self.form_box.set_visible(True)

    def draw_buttons(self):
        for row in (self.pictures_row, self.buttons_row):
            while (c := row.get_first_child()) is not None:
                row.remove(c)
                discard(c)
        v = self.ask["versions"][self.shown_n - 1]
        grid = PictureGrid(PICTURE_BUTTON, PICTURE_ROW, 12)
        wrap = Adw.WrapBox(child_spacing=8, line_spacing=8, hexpand=True)
        self.button_widgets = [None] * len(v["buttons"])
        for i, b in enumerate(v["buttons"]):
            if b.get("picture"):
                unit, btn = self.picture_button(b, self.shown_n)
                grid.append(unit)
            else:
                btn = Gtk.Button(label=b["label"], valign=Gtk.Align.START)
                wrap.append(btn)
            style = b.get("style") or "secondary"
            if style == "primary":
                btn.add_css_class("suggested-action")
            elif style in COLOURS:
                btn.add_css_class(f"ask-{style}")
            btn.connect("clicked", lambda _b, i=i: self.press(i))
            self.button_widgets[i] = btn
        if grid.kids:
            self.pictures_row.append(grid)
        self.pictures_row.set_visible(bool(grid.kids))
        self.buttons_row.append(wrap)
        route = Gtk.MenuButton(label=wording.say("inbox.route_to"), css_classes=["flat"], valign=Gtk.Align.START,
                               tooltip_text=wording.say("inbox.route_tip"), always_show_arrow=True)
        route.set_create_popup_func(lambda m: m.set_popover(route_menu(
            self.ask, lambda model: self.request({"op": "inbox_route", "ask": self.ask["id"], "model": model}),
            lambda bot: self.request({"op": "inbox_route", "ask": self.ask["id"], "bot": bot}))))
        self.route = route
        self.buttons_row.append(route)

    def picture_button(self, b, n):
        """A picture button: its picture, which a click opens large, over a button with its label, which presses it.
        (the whole, and that button)"""
        meta = next((p for p in self.ask["versions"][n - 1]["pictures"] if p["name"] == b["picture"]), {})
        pic = PicturePart(meta.get("width") or 4, meta.get("height") or 3, b["label"])
        frame = Gtk.Overlay(child=pic, overflow=Gtk.Overflow.HIDDEN, css_classes=["picture-frame"])
        frame.add_overlay(Gtk.Image(icon_name="system-search-symbolic", halign=Gtk.Align.END, valign=Gtk.Align.START,
                                    can_target=False, css_classes=["picture-view"]))

        def got(data):
            if data is None:
                return
            try:
                pic.set_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(data)))
            except GLib.Error:
                return
            imageviews.make_image_interactive(pic, data, name=b["label"])
        picture(self.ask, n, b["picture"], got)
        label = Gtk.Label(label=b["label"], wrap=True, justify=Gtk.Justification.CENTER,
                          wrap_mode=Pango.WrapMode.WORD_CHAR)
        btn = Gtk.Button(child=label, css_classes=["picture-press"])
        unit = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.START)
        unit.append(frame)
        unit.append(btn)
        return unit, btn

    def draw_proposal(self):
        box = self.proposal
        while (c := box.get_first_child()) is not None:
            box.remove(c)
        for b in getattr(self, "button_widgets", ()):
            b.remove_css_class("ask-suggested")
        p = self.ask.get("proposal")
        if not p or not self.live():
            box.set_visible(False)
            return
        box.set_visible(True)
        name = p.get("name") or p.get("model") or ""
        if p["status"] == "answering":
            box.append(Spinner(width_request=16, height_request=16, valign=Gtk.Align.CENTER))
            box.append(Gtk.Label(label=wording.say("inbox.model_answering", model=name), xalign=0, hexpand=True))
            return
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        if p["status"] == "failed":
            text.append(Gtk.Label(label=wording.say("inbox.model_failed", model=name, why=p.get("text") or ""),
                                  xalign=0, wrap=True))
        else:
            labels = [b["label"] for b in latest(self.ask)["buttons"]]
            button = labels[p["button"]] if isinstance(p.get("button"), int) and p["button"] < len(labels) else ""
            head = Gtk.Label(label=wording.say("inbox.model_would_press", model=name, button=button), xalign=0,
                             wrap=True, css_classes=["heading"])
            text.append(head)
            if p.get("text"):
                text.append(Gtk.Label(label=p["text"], xalign=0, wrap=True))
            if isinstance(p.get("button"), int) and p["button"] < len(self.button_widgets):
                self.button_widgets[p["button"]].add_css_class("ask-suggested")
            key = (p.get("model"), p.get("button"), repr(p.get("values")))
            if getattr(self, "filled_from", None) != key:  # (filled once: what's typed after it, kept)
                self.filled_from = key
                self.fill(p.get("values") or {})
        box.append(text)
        dismiss = Gtk.Button(label=wording.say("code_apart.dismiss"), css_classes=["flat"], valign=Gtk.Align.START)
        dismiss.connect("clicked", lambda *_: self.request({"op": "inbox_route", "ask": self.ask["id"]}))
        box.append(dismiss)

    def show_version(self, n):
        if 1 <= n <= len(self.ask["versions"]) and n != self.shown_n:
            self.shown_n = n
            self.draw()

    def update(self, ask):
        """The ask as its host says it now: a new version shown (if the latest was), its state, a model's answer."""
        was = len(self.ask["versions"])
        self.ask = ask
        if self.shown_n == was:
            self.shown_n = len(ask["versions"])
        self.draw()

    # ---- the form
    def values(self):
        return {name: read() for name, (_row, read, _w) in self.fields.items()}

    def fill(self, values):
        """values written into the form (a model's answer, Meta's fill_ask): what didn't fit, {name: why}."""
        wrong = {}
        form = {f["name"]: f for f in self.ask["versions"][self.shown_n - 1]["form"]}
        for name, value in (values or {}).items():
            if name not in self.fields:
                wrong[name] = "not one of the form's fields"
                continue
            f = form[name]
            if f["type"] == "choice" and value not in (f.get("choices") or ()):
                wrong[name] = "one of " + ", ".join(f.get("choices") or ())
                continue
            self.fields[name][2](value)
        return wrong

    # ---- done
    def request(self, msg, done=None):
        r = self.ask["server"]

        def got(reply):
            if reply.get("op") == "error":
                toast(self, reply.get("error") or "")
            elif done is not None:
                done(reply)
        r.request(msg, got)

    def press(self, i):
        if not self.live():
            return
        self.request({"op": "inbox_press", "ask": self.ask["id"], "button": i, "values": self.values(),
                      "version": self.ask["versions"][self.shown_n - 1].get("n")})


class AskCard(Gtk.Box):
    """An ask in the Inbox's list: its title, when and the waiting dot (a stopped one: struck through, and Not
    answered — the task stopped), then the ask itself, cut a few lines down."""

    def __init__(self, win, ask, opened):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10, css_classes=["card", "ask-card"])
        from sessionsidebar import StatusDot
        self.win, self.ask = win, ask
        head = Gtk.Box(spacing=8)
        self.title_label = pickers.FadeLabel(xalign=0, css_classes=["heading"], hexpand=True)
        self.title_label.set_cursor_from_name("pointer")
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: opened(self.ask))
        self.title_label.add_controller(click)
        self.title = self.title_label
        head.append(self.title_label)
        self.at = Gtk.Label(css_classes=["dim-label", "caption"], valign=Gtk.Align.CENTER)
        head.append(self.at)
        self.dot = StatusDot("waiting")
        head.append(self.dot)
        self.append(head)
        self.view = AskView(win, ask, cut=True)
        self.append(self.view)
        touch = Gtk.GestureClick(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        touch.connect("pressed", lambda *_: setattr(win, "inbox_selected", (self.ask["server"].key, self.ask["id"])))
        self.add_controller(touch)
        self.update(ask)

    def update(self, ask):
        self.ask = ask
        gone = ask.get("status") == "stopped"
        title = title_of(latest(ask)) or ask.get("task_name") or ""
        if gone:
            self.title_label.set_markup(f"<s>{GLib.markup_escape_text(title)}</s>")
        else:
            self.title_label.set_label(title)
        self.title.set_tooltip_text(title)
        self.at.set_label(when(ask["at"]))
        self.at.set_visible(not gone)
        self.dot.set_visible(not gone)
        (self.add_css_class if gone else self.remove_css_class)("ask-gone-card")
        self.title_label.set_opacity(0.55 if gone else 1.0)
        self.view.update(ask)


class InboxPage(Gtk.ScrolledWindow):
    """The Inbox, in a tab of the main area: every host's asks, newest first (AskCard), then Pending requests (chats
    waiting on a permission) and Running (chats at a turn), as the sidebar draws their rows. refresh() as the network
    changes: cards kept (what's typed in them too), added and dropped."""

    def __init__(self, win):
        super().__init__(vexpand=True, hexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        css()
        self.win, self.cards = win, {}
        self.col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_top=18, margin_bottom=18,
                           margin_start=28, margin_end=28)
        self.asks_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.col.append(self.asks_box)
        self.others = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["navigation-sidebar"])
        self.others.connect("row-activated", lambda _l, row: getattr(row, "info", None) and self.open_chat(row.info))
        others = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["session-sidebar"])
        others.append(self.others)
        self.col.append(others)
        self.empty = Gtk.Label(label=wording.say("inbox.nothing"), css_classes=["dim-label"], vexpand=True,
                               valign=Gtk.Align.CENTER, margin_top=80)
        self.col.append(self.empty)
        self.set_child(self.col)
        self.others_key = None
        self.refresh()

    def open_ask(self, ask):
        self.win.open_ask(ask["server"].key, ask["id"])

    def open_chat(self, info):
        self.win.show_chat()
        if info["id"] == self.win.session_id:
            return
        self.win.open_remote_soon(info)

    def refresh(self):
        asks = sorted(network().asks(), key=lambda a: -a["at"])
        keep = {(a["server"].key, a["id"]) for a in asks}
        for key in [k for k in self.cards if k not in keep]:
            card = self.cards.pop(key)
            self.asks_box.remove(card)
            discard(card)
        prev = None
        for a in asks:
            key = (a["server"].key, a["id"])
            card = self.cards.get(key)
            if card is None:
                card = self.cards[key] = AskCard(self.win, a, self.open_ask)
                self.asks_box.insert_child_after(card, prev)
            else:
                if card.ask != a:
                    card.update(a)
                if card.get_prev_sibling() is not prev:
                    self.asks_box.reorder_child_after(card, prev)
            prev = card
        self.fill_others()
        self.empty.set_visible(not asks and self.others.get_first_child() is None)

    def fill_others(self):
        listed = [s for s in network().sessions() if not s.get("archived") and not s.get("draft")]
        pending = [s for s in listed if s.get("asks")]
        running = [s for s in listed if s.get("state") == "working" and not s.get("asks")]
        key = tuple((s["id"], s.get("state"), tuple(a.get("tool") or "" for a in s["asks"])) for s in pending) + \
            ("|",) + tuple(s["id"] for s in running)
        if key == self.others_key:
            return
        self.others_key = key
        while (row := self.others.get_first_child()) is not None:
            self.others.remove(row)
            discard(row)
        sidebar = getattr(self.win, "sidebar", None)
        for heading, group in ((wording.say("inbox.pending_requests"), pending), (wording.say("signal.running"),
                                                                                    running)):
            if not group or sidebar is None:
                continue
            box = markdownview.group_header(heading, False, lambda: None)
            box.set_margin_top(tokens.px("gap.header-top"))
            box.set_margin_start(tokens.px("gap.header-start"))
            self.others.append(Gtk.ListBoxRow(child=box, activatable=False, selectable=False))
            for s in group:
                info = remote_info(s)
                row = sidebar.session_row(info)
                if group is pending:
                    tool = (s["asks"][0].get("tool") or wording.say("asks.a_tool"))
                    tag = Gtk.Label(label=wording.say("asks.allow_tool", tool=tool), valign=Gtk.Align.CENTER)
                    tag.add_css_class("row-tag")
                    row.get_child().insert_child_after(tag, row.get_child().get_first_child()) \
                        if row.get_child().get_first_child() is not None else row.get_child().append(tag)
                    row.dot.set_state("waiting")
                else:
                    row.dot.set_state("working")
                row.info = info
                self.others.append(row)


class AskPage(Gtk.Box):
    """An ask in a tab of its own: AskView at the full width there is, and at the foot a box that writes to the asking
    task's Bot. Answered elsewhere (or by a Bot), it says the ask isn't waiting any more, its buttons off."""

    def __init__(self, win, ask):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        css()
        self.win, self.key, self.aid = win, ask["server"].key, ask["id"]
        self.view = AskView(win, ask)
        self.view.set_margin_top(18)
        self.view.set_margin_bottom(18)
        self.view.set_margin_start(28)
        self.view.set_margin_end(28)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.banner = Adw.Banner(title=wording.say("inbox.gone"), revealed=False)
        col.append(self.banner)
        col.append(self.view)
        self.scroll = Gtk.ScrolledWindow(child=col, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.append(self.scroll)
        bot = ask.get("bot") or "Bot"
        self.writing = composerview.Composer(send_tip=wording.say("inbox.send_to", bot=bot))
        self.writing.set_margin_start(20)
        self.writing.set_margin_end(20)
        self.writing.set_margin_bottom(10)
        self.writing.send.connect("clicked", lambda *_: self.send())
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.writing.input.add_controller(keys)
        self.append(self.writing)

    def _key(self, _c, keyval, _code, state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and composerview.sends(state):
            self.send()
            return True
        return False

    def send(self):
        text = self.writing.text().strip()
        if not text:
            return
        buf = self.writing.input.get_buffer()

        def done(_reply):
            buf.set_text("")
        self.view.request({"op": "inbox_say", "ask": self.aid, "text": text}, done)

    def refresh(self):
        ask = find_ask(self.key, self.aid)
        if ask is None:  # (answered, here or elsewhere, or withdrawn: not waiting any more)
            self.banner.set_revealed(True)
            self.view.ask = {**self.view.ask, "status": "answered"}
            self.view.draw()
            return
        self.banner.set_revealed(False)
        if ask != self.view.ask:
            self.view.update(ask)


class InboxRow(Gtk.Button):
    """The Inbox row, over Search sessions: as New session and Open folder are drawn, with how many asks wait and the
    yellow dot a chat waiting for you has (neither, with none)."""

    def __init__(self, open_inbox):
        from sessionsidebar import StatusDot
        ensure_icons()
        super().__init__(tooltip_text=wording.say("inbox.tip"), margin_top=2)
        self.add_css_class("flat")
        self.add_css_class("sidebar-action")
        box = Gtk.Box(spacing=10)
        box.append(Gtk.Image.new_from_icon_name("mail-unread-symbolic"))
        box.append(Gtk.Label(label=wording.say("inbox.title"), xalign=0, hexpand=True))
        self.count = Gtk.Label(valign=Gtk.Align.CENTER, css_classes=["dim-label"], visible=False)
        box.append(self.count)
        self.dot = StatusDot("inactive")
        self.dot.set_margin_end(4)
        box.append(self.dot)
        self.set_child(box)
        self.connect("clicked", lambda *_: open_inbox())
        self.refresh()

    def refresh(self):
        n = waiting_count()
        self.count.set_label(str(n))
        self.count.set_visible(n > 0)
        self.dot.set_state("waiting" if n else "inactive")


def ask_text(ask, view=None):
    """What Meta's told of an ask (read_ask): its title, task, body, buttons by index, and its form's fields with
    their values now (view: the one shown, if it's drawn)."""
    v = latest(ask)
    values = view.values() if view is not None else {}
    lines = [f"Ask {ask['id']} on {ask['server'].label}: {title_of(v)}",
             f"From Task “{ask.get('task_name')}” (task {ask.get('task')}, run {ask.get('run')}), Bot "
             f"{ask.get('bot') or ''} (chat {ask.get('session')}); status {ask.get('status')}; version "
             f"{len(ask['versions'])} of {len(ask['versions'])}", "", "<body>", v["body"].strip(), "</body>", "",
             "Buttons (by index): " + "; ".join(f"{i}: {b['label']}" for i, b in enumerate(v["buttons"]))]
    if v["form"]:
        lines.append("Form fields (name, type, value now):")
        for f in v["form"]:
            kind = f["type"] + (" of " + ", ".join(f.get("choices") or ()) if f["type"] == "choice" else "")
            lines.append(f"- {f['name']} ({f.get('label') or f['name']}; {kind}): {values.get(f['name'], '')!r}")
    if ask.get("proposal"):
        p = ask["proposal"]
        lines.append(f"A model's answer waiting to be confirmed: {p.get('name')} {p.get('status')} "
                     f"button {p.get('button')} values {p.get('values')}")
    return "\n".join(lines)


def run_log(ask, limit=60):
    """The asking run's record and the end of its log, as its host gives them (off the main loop)."""
    got = ask["server"].call({"op": "task_run", "session": ask["session"], "run": ask["run"]}, timeout=15)
    if got.get("op") == "error":
        return f"(its run's log couldn't be read: {got.get('error')})"
    run = got.get("run") or {}
    lines = [f"Run {run.get('id')}: {run.get('status')}, started {time.strftime('%H:%M', time.localtime(run.get('started') or 0))}"]
    lines += [f"{x.get('kind')}: {x.get('text')}" for x in (got.get("log") or [])[-limit:]]
    return "\n".join(lines)
