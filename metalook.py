"""metalook — how Meta's column looks: its CSS, the marks on what the left bar lists (mark_row), the @ list in
its box (RefCompleter), rows of settings and the like as it shows them (rows_group), and the forms it opens (Form).
"""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

import widgets  # noqa: E402
from baudelib import tokens  # noqa: E402  (Meta's measures: schema/tokens, Baude's)
from baudelib import wording  # noqa: E402
from meta import REF_COLOR, REF_RE, plain_ref  # noqa: E402


# ---------------------------------------------------------------- how it looks

CSS = tokens.css(f"""
label.said-refs link {{ color: {REF_COLOR}; }}  /* (an @reference in what you wrote: MetaChat._said) */
.meta-ui {{ margin-top: 2px; }}
/* Settings' rows as Meta shows them (a setting's, chats', pages', the changelog's): hollow, in a tool card's outline,
   where Settings has them on a card */
.meta-ui list.boxed-list {{ background: none; box-shadow: none; border: 1px solid @baude_rule; border-radius: {{radius.card}}; }}
.meta-ui list.boxed-list > row {{ background: none; }}
.meta-ui list.boxed-list > row:hover {{ background: alpha(@view_fg_color, 0.04); }}
.tool-card button.meta-batch {{ min-height: 0; min-width: 0; padding: 1px 5px; }}
.changes-bar {{ background: alpha(@accent_bg_color, {{meta.changes-bar-alpha}}); border-radius: {{meta.changes-bar-radius}}; padding: 1px 2px 1px 10px; }}
.changes-bar button {{ min-height: 24px; padding: 0 8px; }}
/* (under the box as Baude's status line is under a chat's, as tall, so the two boxes are on a line) */
.meta-foot {{ font-size: 0.85em; padding: 4px 0; }}
.meta-foot > * {{ opacity: 0.75; }}
.meta-foot button {{ min-height: 0; padding: 0 4px; font-weight: normal; }}
label.meta-note {{ font-size: {{font.small}}em; opacity: 0.75; }}
/* a form (MetaChat.form_card): hollow, in a tool card's outline, as Meta's rows are; pinned over the conversation,
   on the pane's own background, a shadow under it */
.meta-form {{ border: 1px solid @baude_rule; border-radius: {{radius.card}}; padding: 10px 12px; }}
.meta-form.meta-pinned {{ background: @baude_inside; box-shadow: 0 3px 10px alpha(black, 0.25); }}
button.meta-item {{ min-height: 0; padding: 1px 4px; font-weight: normal; }}
""")
_CSS = []
MARK_COLORS = {how: tokens.color_hex(f"meta.{how}", "light") for how in ("added", "removed", "moved", "archived", "edited")}
# (edited: a page whose text changed; highlight: the accent's)
MARK_ALPHA, LIT_ALPHA = tokens.number("meta.mark-alpha"), tokens.number("meta.lit-alpha")  # (a mark's highlight; lit, its card under the pointer: MetaChat.light)


def install_css(display):
    """Meta's look, once for the app."""
    if not _CSS and display is not None:
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        _CSS.append(provider)
        widgets.rounded_css(display, CSS)  # (a form's corners, its rows', as Baude's under a GTK theme)


MARK_STRENGTH = {"archived": 4, "added": 3, "moved": 2, "edited": 1}  # (a row a change marks twice, after the
# undoing pairs below: the stronger; a page added, then edited, is added; a chat moved, then archived, archived)
# (what a change's later part does to a row an earlier part marked so: the row unmarked, None, or marked as given)
MARK_AFTER = {("added", "removed"): None, ("added", "archived"): None, ("archived", "added"): None,
              ("removed", "added"): "edited", ("moved", "removed"): "removed", ("edited", "removed"): "removed"}


def action_parts(a):
    """An action's parts, each {label, kind, undo, redo}: a bundle's, or the action itself."""
    return a.get("parts") or [a]


def bundle(a, label_, kind, undo, redo, marks):
    """Action a (one change to what the left bar lists) with another part, updated in place (what's drawn of it
    holds it): undone after the parts before it, done again before them (MetaChat.apply_action); its label the
    parts', its marks gathered. A row an earlier part marked: as MARK_AFTER says for the two (a page added and deleted
    again: not marked; deleted and added again: edited), else the stronger (MARK_STRENGTH), with both their notes. A
    row whose key changed carries its mark to the new key (a mark's fifth, the key it had)."""
    parts = [{"label": p["label"], "kind": p["kind"], "undo": p["undo"], "redo": p["redo"]} for p in action_parts(a)]
    parts.append({"label": label_, "kind": kind, "undo": undo, "redo": redo})
    gathered = {(m[0], m[1]): m for m in a.get("marks") or ()}
    for m in marks:
        was = gathered.pop((m[0], m[4]), None) if len(m) > 4 and m[4] else None
        old = gathered.get((m[0], m[1])) or was
        if old is not None:
            if (old[2], m[2]) in MARK_AFTER:
                how = MARK_AFTER[(old[2], m[2])]
            else:
                how = old[2] if MARK_STRENGTH.get(old[2], 0) > MARK_STRENGTH.get(m[2], 0) else m[2]
            if how is None:
                gathered.pop((m[0], m[1]), None)
                continue
            note = ", ".join(dict.fromkeys(n for n in (old[3] if len(old) > 3 else "", m[3] if len(m) > 3 else "")
                                           if n))
            m = [m[0], m[1], how, note] + m[4:]
        gathered[(m[0], m[1])] = m
    a.update(kind="bundle", undo=None, redo=None, parts=parts, kept=False, marks=list(gathered.values()),
             label="; ".join(dict.fromkeys(p["label"] for p in parts)))
    return a


def mark_rgba(how, lit=False):
    """A mark's highlight (MARK_COLORS; highlight: the accent's), as bright as it is (lit: its change's box under the
    pointer: MetaChat.light), the same in the left bar, Meta's boxes and a page's text."""
    rgba = widgets.accent_rgba().copy() if how == "highlight" else Gdk.RGBA()
    if how != "highlight":
        rgba.parse(MARK_COLORS[how])
    rgba.alpha = LIT_ALPHA if lit else MARK_ALPHA
    return rgba


def paint_label(lb, how, lit=False):
    """A label's text highlighted as a mark is (mark_rgba), square, behind its text only; struck through if gone."""
    rgba = mark_rgba(how, lit)
    attrs = Pango.AttrList()
    attrs.insert(Pango.attr_background_new(*(round(c * 65535) for c in (rgba.red, rgba.green, rgba.blue))))
    attrs.insert(Pango.attr_background_alpha_new(round(rgba.alpha * 65535)))
    if how == "removed":
        attrs.insert(Pango.attr_strikethrough_new(True))
    lb.set_attributes(attrs)


def mark_row(row, key, mark, chat, name_label, inline=True):
    """A row of a left bar's (key: (kind, its key): a chat, a page) as Meta's changed it (mark: (how, note, action)):
    its name highlighted, square, behind its text only (green added, red removed and struck through, blue moved;
    highlighted, the accent), its note (the group it went to, where it came from) after its name if inline, else under
    the pointer with what was done. Lit more while the pointer's over the card of the call that did it (meta_paint,
    MetaChat.light). Kept or undone from that card, all the call did at once: nothing here takes part of it back."""
    how, note, action = mark

    def paint(lit=False):
        paint_label(name_label, how, lit)
    paint()
    chat.painted.setdefault(key, {})["row"] = paint
    if note or action is not None:
        done = action["label"] if action else wording.say("meta_actions.highlighted")
        said = wording.say("meta_actions.by_meta_note", action=done, note=note) if note else \
            wording.say("meta_actions.by_meta", action=done)
        row.set_tooltip_text("\n".join(filter(None, [row.get_tooltip_text(), said])))
    if note and inline:
        said = Gtk.Label(label=note, ellipsize=Pango.EllipsizeMode.END, max_width_chars=12, valign=Gtk.Align.CENTER)
        said.add_css_class("meta-note")
        row.get_child().append(said)


class SaidLabel(widgets.LinkLabel):
    """What you wrote to Meta (MetaChat._said): a LinkLabel whose logos are in the @names' colour, as a page draws
    them, not the text's."""

    COLOR = Gdk.RGBA()
    COLOR.parse(REF_COLOR)

    def get_color(self):
        return self.COLOR


class RefCompleter:
    """@'s list over Meta's box: what space() lists (Record's: its notebooks and pages, the folders' names, the chats;
    Baude's: its chats and its chat's names), as Baude's / is over its composer (SlashCompleter). An @ typed at a word's
    start starts it; what's typed after it is its search, spaces and all, till Enter or Tab takes what's picked (as
    @name, or @{a name} with spaces), Esc leaves it, a new line, the caret back before the @, or words that find
    nothing. With nothing typed yet, first(items) puts them in order."""

    def __init__(self, space, view, anchor, first=None):
        self.space, self.view, self.anchor, self.first = space, view, anchor, first
        self.picks = widgets.PickList(anchor, self._bind, self.accept, position=Gtk.PositionType.TOP)
        self.pop = self.picks.pop
        self.pop.set_halign(Gtk.Align.START)  # (its left side on the @'s: PickList.over)
        self.pop.set_offset(0, -10)
        self.shown, self.query, self.moved, self.waiting, self.at = [], None, False, None, None
        buf = view.get_buffer()
        # (the @names it knows, as the notes colour theirs, and the search being typed: after the box's own markdown)
        buf.create_tag("record-ref", foreground=REF_COLOR, weight=600, background="rgba(79,138,124,0.14)")
        buf.connect("insert-text", self._inserting)
        buf.connect_after("changed", lambda *_: (self.refresh(), self.mark()))
        buf.connect("notify::cursor-position", lambda *_: (self.refresh(), self.mark()))

    def _inserting(self, buf, at, text, _n):
        if text != "@":
            return
        before = at.copy()
        if at.starts_line() or before.backward_char() and (before.get_char().isspace() or before.get_char() in "([{\"'"):
            if self.at is not None:
                buf.delete_mark(self.at)
            self.at = buf.create_mark(None, at, True)  # (left of what goes in: on the @)

    def word(self):
        """(where its @ is, what's typed after it) while @'s search is on, else None."""
        if self.at is None or self.at.get_deleted():
            return None
        buf = self.view.get_buffer()
        a = buf.get_iter_at_mark(self.at).get_offset()
        caret = buf.get_iter_at_mark(buf.get_insert()).get_offset()
        typed = buf.get_text(buf.get_iter_at_offset(a), buf.get_iter_at_offset(max(a, caret)), True)
        if caret <= a or not typed.startswith("@") or "\n" in typed:
            return None
        return a, typed[1:].lstrip("{")

    def forget(self):
        if self.at is not None:
            self.view.get_buffer().delete_mark(self.at)
            self.at = None
        if self.waiting is not None:  # (what it waited for may be another chat's: asked again as it's next shown)
            GLib.source_remove(self.waiting)
            self.waiting = None
        self.hide()

    def refresh(self):
        word = self.word()
        if word is None:
            if self.at is not None or self.picks.visible():
                self.forget()
            return
        space, q = self.space(), word[1]
        if space is None:
            return self.forget()
        shown, matched = space.search(q)
        if not q.strip() and self.first is not None:
            shown = self.first(shown)
        if not shown and " " in q:  # (an @ and words that name nothing: just words)
            return self.forget()
        i = self.picks.picked()
        kept = self.shown[i]["surface"] if self.moved and q == self.query and i is not None and i < len(self.shown) \
            else None  # (the same search, listed again as the catalogue comes in: what's picked stays picked)
        self.shown, self.query = shown, q
        at = next((j for j, it in enumerate(shown) if it["surface"] == kept), None)
        self.moved = at is not None
        if self.waiting is None and space.busy:
            self.waiting = space.refresh(self._catalogued)
        note = wording.say("meta.found", n=matched) if matched else wording.say("meta.nothing_found")
        wide, most = self.anchor.get_width() - 10, widgets.PickList.WIDTH
        self.picks.scroll.set_size_request(min(most, wide) if wide > 0 else most, -1)
        self.picks.show(len(shown), (at or 0) if shown else None, note,
                        widgets.PickList.over(self.view, word[0], self.anchor), coming=space.busy)

    def _catalogued(self):
        space = self.space()  # (some of it in, more on its way: waited for in turn, the list up or not)
        self.waiting = space.refresh(self._catalogued) if space is not None and space.busy else None
        if self.picks.visible():
            self.refresh()
        self.mark()

    def mark(self):
        """The @names the catalogue knows, in the notes' @names' colour, and @'s search while it's on."""
        buf = self.view.get_buffer()
        start, end = buf.get_bounds()
        buf.remove_tag_by_name("record-ref", start, end)
        text, space = buf.get_text(start, end, True), self.space()
        for m in REF_RE.finditer(text):
            if space is not None and space.find(m.group(1) or m.group(2)) is not None:
                buf.apply_tag_by_name("record-ref", buf.get_iter_at_offset(m.start()), buf.get_iter_at_offset(m.end()))
        if (word := self.word()) is not None:
            buf.apply_tag_by_name("record-ref", buf.get_iter_at_offset(word[0]),
                                  buf.get_iter_at_offset(word[0] + 1 + len(word[1])))

    def _bind(self, i, icon, name, where):
        it = self.shown[i]
        icon.icon = it["icon"]
        name.set_text(it.get("label") or it["surface"])
        where.set_text(f"{it['what']} · {it['summary']}")
        widgets.time_tip(where, it.get("at"), it.get("ago"))

    def accept(self):
        """The picked name in place of the @word, a space after it (one there already is the one)."""
        i, word = self.picks.picked(), self.word()
        if i is None or i >= len(self.shown) or word is None:
            return
        buf = self.view.get_buffer()
        end = buf.get_iter_at_mark(buf.get_insert())
        if end.get_char() == " ":
            end.forward_char()
        surface = self.shown[i]["surface"]
        self.forget()
        buf.begin_user_action()
        buf.delete(buf.get_iter_at_offset(word[0]), end)
        buf.insert_at_cursor(plain_ref(surface) + " ")
        buf.end_user_action()
        self.view.grab_focus()

    def visible(self):
        return self.picks.visible()

    def hide(self):
        self.moved = False
        self.picks.hide()

    def handle_key(self, keyval, state=0):
        """True if the key was the list's."""
        if not self.visible():
            return False
        steps = widgets.PickList.STEPS
        if state & Gdk.ModifierType.SHIFT_MASK and keyval in (*steps, Gdk.KEY_Left, Gdk.KEY_Right):
            self.forget()  # (Shift and an arrow: selecting the text, not picking in the list)
            return False
        if keyval in steps:
            if self.shown:
                self.picks.move(steps[keyval])
                self.moved = True
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and widgets.sends(state):
            self.forget()  # (Ctrl/⌘+Enter sends, past the list)
            return False
        elif keyval in (Gdk.KEY_Tab, Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self.picks.picked() is not None:
            self.accept()
        elif keyval == Gdk.KEY_Escape:
            self.forget()
        else:
            return False
        return True


def rows_group(rows, title=None, limit=None):
    """Rows to click (show chats, show pages), as Settings lists its rows: [{icon, title, subtitle, open}]; past limit,
    a Show N more row."""
    group = Adw.PreferencesGroup(title=title or "")
    group.add_css_class("meta-ui")
    shown = rows if limit is None else rows[:limit]

    def add(r):
        row = Adw.ActionRow(title=r["title"], subtitle=r.get("subtitle") or "", activatable=r.get("open") is not None,
                            use_markup=False)
        if r.get("icon"):
            row.add_prefix(Gtk.Image(icon_name=r["icon"]))
        if r.get("open") is not None:
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda *_, fn=r["open"]: fn())
        group.add(row)
    for r in shown:
        add(r)
    if len(shown) < len(rows):
        more = Adw.ButtonRow(title=wording.say("meta.show_more", n=len(rows) - len(shown)))

        def all_of_them(btn):
            group.remove(btn)
            for r in rows[len(shown):]:
                add(r)
        more.connect("activated", all_of_them)
        group.add(more)
    return group


# ---------------------------------------------------------------- forms
# A form Meta brings up in its pane (open_form): an app's own dialog's contents (its heading, what it says, its fields,
# its button) inline, in Meta's look; filled by the user, by pasting or typing, or by Meta (fill_form); checked as it's
# filled; submitted only by the user's click. One not yet submitted is pinned: scrolled past, it stops at the top of the
# pane and hovers there (MetaChat._pin_forms); right-click it: Unpin, or Pin.

class Form:
    """A form of an app's (MetaChat.forms: its name -> what makes one): heading, body (what it says, as its dialog's
    body), child (its fields, as its dialog has them), fields [(name, what it takes, widget: an entry, a text view, a
    drop-down)], verb (its button's word), check() -> what's wrong (to say), or None; submit() -> what was done (told
    the model, and with said, on its card too), or ValueError saying why not."""

    def __init__(self, heading, child, fields, check, submit, verb=wording.say("meta.submit"), body="", said=True):
        self.heading, self.child, self.fields, self.verb, self.body = heading, child, fields, verb, body
        self.check, self.submit, self.said = check, submit, said

    @staticmethod
    def get(widget):
        if isinstance(widget, Gtk.TextView):
            buf = widget.get_buffer()
            return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        if isinstance(widget, Gtk.DropDown):
            item = widget.get_selected_item()
            return item.get_string() if item is not None else ""
        return widget.get_text()

    @staticmethod
    def choices(widget):
        model = widget.get_model() if isinstance(widget, Gtk.DropDown) else None
        return [model.get_string(i) for i in range(model.get_n_items())] if model is not None else None

    @classmethod
    def put(cls, widget, value):
        """A field filled: a drop-down's choice the one named (any case; else the one that starts so, or has it)."""
        value = "" if value is None else str(value)
        if isinstance(widget, Gtk.TextView):
            widget.get_buffer().set_text(value)
        elif isinstance(widget, Gtk.DropDown):
            names, want = cls.choices(widget), value.strip().lower()
            for test in (lambda n: n.lower() == want, lambda n: n.lower().startswith(want), lambda n: want in n.lower()):
                if (found := next((i for i, n in enumerate(names) if test(n)), None)) is not None:
                    widget.set_selected(found)
                    return
            raise ValueError(f"{value!r} isn't one of: {', '.join(names)}.")
        else:
            widget.set_text(value)

    def values(self):
        return {name: self.get(w) for name, _takes, w in self.fields}

    def fill(self, values, partial=False):
        """Fields filled (values: {name: value}), in the form's order (a host picked before what it offers). partial:
        each one that can be, and the names of those that can't returned; else ValueError at the first that can't."""
        known = {name: w for name, _takes, w in self.fields}
        if unknown := [k for k in values if k not in known]:
            if not partial:
                raise ValueError(f"No field {unknown[0]!r}: its fields are {', '.join(known)}.")
        missed = set(unknown)
        for name, _takes, w in self.fields:
            if name in values:
                try:
                    self.put(w, values[name])
                except ValueError:
                    if not partial:
                        raise
                    missed.add(name)
        return missed

    def changed(self, fn):
        """fn() as any field changes."""
        for _name, _takes, w in self.fields:
            if isinstance(w, Gtk.TextView):
                w.get_buffer().connect("changed", lambda *_: fn())
            elif isinstance(w, Gtk.DropDown):
                w.connect("notify::selected", lambda *_: fn())
            else:
                w.connect("changed", lambda *_: fn())

    def described(self):
        """Its fields as Meta's told them: each one's name, what it takes, what it has, and its choices."""
        out = []
        for name, takes, w in self.fields:
            d = {"field": name, "takes": takes, "value": self.get(w)}
            if (choices := self.choices(w)) is not None:
                d["choices"] = choices
            out.append(d)
        return out
