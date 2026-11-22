"""diaryfind — a page of notes' ⌘F (the bar and its matches) and @'s list over its text (DiaryFind, a
DiaryEditor's).
"""
import re

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gtk, Pango  # noqa: E402

import pointers  # noqa: E402  (the pointer's shape: beside this file)
import widgets  # noqa: E402  (what Record draws as Baude does: the links' logos; beside this file)
import markdownview  # noqa: E402
import composerview  # noqa: E402
from baudelib import wording  # noqa: E402  (its words, Baude's: schema/copy)
from codeview import FindInText  # noqa: E402
from recordstore import (  # noqa: E402
    CITE_SCHEME, HOME, ISSUE_LEAD, ISSUE_OPENS, LINKING_RE, LINK_RE, LIST_ITEM_RE, MD_LINK_RE, REF_RE, SUBPAGE_RE,
    URL_RE, github_ref, heading_levels, issue_query, link_name, page_link, page_target, ref_text, short_page,
    subtree_end, swap_with_sibling, whole_links,
)


class DiaryFind:
    """A page of notes' ⌘F (FindInText: the bar, the matches) and @'s list over its text."""

    # ---- ⌘F (FindInText: the bar, the matches)
    def open_search(self):
        """As a code tab's; a selection in the title, where the focus is, is what it looks for too."""
        picked = self.title.get_selection_bounds() if self.title.get_focus_child() is not None else ()
        words = self.title.get_text()[picked[0]:picked[1]] if picked else ""
        FindInText.open_search(self)
        if words:
            self.search_entry.set_text(words)
            self.search_entry.select_region(0, -1)

    def find_adjustment(self):
        return self.scroll.get_vadjustment()

    def find_colours(self):
        FindInText.find_colours(self)
        self._paint_title()

    def clear_matches(self):
        FindInText.clear_matches(self)
        self.title_hits, self.on_title = [], None
        self.title.set_attributes(Pango.AttrList())

    def find(self, text, scroll=True, near=None):
        """What's found in the text, and in the title, which comes before it: the title's the first if the page is at its
        top as the search is typed, or if that's the one it was on."""
        on_title = self.on_title
        hits = [m.span() for m in re.finditer(re.escape(text), self.title.get_text(), re.IGNORECASE)] if text else []
        at_top = self.scroll.get_vadjustment().get_value() <= 0
        first = bool(hits) and (at_top if scroll and near is None else on_title is not None)
        FindInText.find(self, text, scroll and not first, near)
        self.title_hits = hits
        if first or (hits and not self.matches):
            self._show_title_hit(min(on_title or 0, len(hits) - 1))
        else:
            self._paint_title()
            self._say_which()

    def find_next(self, step):
        total = len(self.title_hits) + len(self.matches)
        if not total:
            return FindInText.find_next(self, step)
        at = self.on_title if self.on_title is not None else len(self.title_hits) + self.match_index
        to = (at + step) % total
        if to < len(self.title_hits):
            self._show_title_hit(to)
        else:
            self.show_match(to - len(self.title_hits))

    def show_match(self, i, scroll=True):
        self.on_title = None
        FindInText.show_match(self, i, scroll)
        self._paint_title()
        self._say_which()

    def _show_title_hit(self, i):
        if 0 <= self.match_index < len(self.matches):
            s, e = self.matches[self.match_index]
            self.buf.remove_tag(self.current_tag, self.buf.get_iter_at_offset(s), self.buf.get_iter_at_offset(e))
        self.match_index, self.on_title = -1, i
        if self.title.get_focus_child() is None:  # (shown, in a title too long for its box; not as it's written in)
            self.title.set_position(self.title_hits[i][1])
        self._paint_title()
        self._say_which()

    def _say_which(self):
        if self.title_hits:
            at = self.on_title if self.on_title is not None else len(self.title_hits) + self.match_index
            total = len(self.title_hits) + len(self.matches)
            self.search_count.set_label(wording.say("find.match_of_more", at=at + 1, total=total)
                                        if len(self.matches) >= self.MATCH_LIMIT else
                                        wording.say("find.match_of", at=at + 1, total=total))

    def _paint_title(self):
        """The title's matches shaded as the text's are (it's an entry: no tags), the current one more."""
        match, current = self.find_tints()
        text, attrs = self.title.get_text(), Pango.AttrList()
        for n, (a, b) in enumerate(self.title_hits):
            red, green, blue, alpha = current if n == self.on_title else match
            first, last = len(text[:a].encode()), len(text[:b].encode())
            for attr in (Pango.attr_background_new(red * 257, green * 257, blue * 257),
                         Pango.attr_background_alpha_new(int(alpha * 65535))):
                attr.start_index, attr.end_index = first, last
                attrs.insert(attr)
        self.title.set_attributes(attrs)

    def _find_again(self):
        """The page drawn again, its tags all taken off, as it's written in or the caret goes to another line (what's
        hidden changing with it): the find bar's matches found afresh, the one it was on still the current one, and
        the page left where it is. (They're tags on the buffer, so they're in neither what's kept, text(), nor what's
        undone.)"""
        if self.search_bar.get_search_mode() and (typed := self.search_entry.get_text()):
            mark = self.buf.get_mark("found")
            self.find(typed, scroll=False, near=self.buf.get_iter_at_mark(mark).get_offset() if mark else None)

    def match_span(self, start, end):
        """A match from its first letter seen (GTK starts one before any markup hidden there). None if all that's seen
        of it is what's drawn over: a bullet's -, a box's [ ], a rule's ---, an @name's @ aren't there as written, and
        shaded they're a sliver beside what's drawn."""
        tags = self.buf.get_tag_table()
        hidden, unseen = tags.lookup("hidden"), (tags.lookup("drawn"), tags.lookup("logo-at"))
        while start.has_tag(hidden) and start.compare(end) < 0:
            start.forward_char()
        it = start.copy()
        while it.compare(end) < 0:
            if not it.has_tag(hidden) and not any(it.has_tag(tag) for tag in unseen):
                return start, end
            it.forward_char()
        return None

    def find_top(self):
        """The offset at the top of what's shown (it's the scroller that scrolls: follow_soon)."""
        ok, top = self.scroll.compute_point(self.view, Graphene.Point().init(0, 0))
        y = self.view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, 0, int(top.y))[1] if ok else 0
        return self.view.get_line_at_y(y)[0].get_offset()

    def find_scroll(self, mark):
        self.follow_caret(self.buf.get_iter_at_mark(mark))

    def close_search(self):
        """Esc in the bar: back in the text, the match it was on selected, as an editor's find leaves it, to be written
        over or gone on from (its line's markup shown, as the caret's is). On a match in the title, nothing's selected:
        what's written next is for the text, and over a title it would rename the page."""
        if self.matches and self.on_title is None:
            self.buf.select_range(*(self.buf.get_iter_at_offset(at) for at in self.matches[self.match_index]))
        FindInText.close_search(self)
        self.follow_soon()

    def _caret_line(self):
        """The caret (or the selection) on other lines: their markup shown, the last ones' hidden again."""
        if self.win.store.markup != "line":  # (shown on every line, or none: nothing changes as the caret goes)
            return
        a = self.buf.get_iter_at_mark(self.buf.get_insert()).get_line()
        b = self.buf.get_iter_at_mark(self.buf.get_selection_bound()).get_line()
        if (min(a, b), max(a, b) + 1) != getattr(self, "_shown_lines", None):
            self._highlight()

    def _prefix_px(self, prefix):
        """How wide an item's marker (or box) is as it's written, its spaces after it too."""
        layout = self.view.create_pango_layout(prefix)
        if re.match(r"\d", prefix):  # (a number's bold, the bullet tag's)
            attrs = Pango.AttrList()
            attrs.insert(Pango.attr_weight_new(Pango.Weight.BOLD))
            layout.set_attributes(attrs)
        return round(layout.get_size()[0] / Pango.SCALE)  # (the nearest pixel: the item's text starts in between)

    def _spaced(self, px):
        """The tag that puts px more round a character (the space after a list marker, to its column's end:
        _highlight), made the first time."""
        name = f"spaced{px}"
        if self.buf.get_tag_table().lookup(name) is None:
            self.buf.create_tag(name, letter_spacing=px * Pango.SCALE)
        return name

    def _hung(self, start, px):
        """The tag that hangs a list item's paragraph: its first line from start (its marker), the lines it wraps
        onto from px (its text), made the first time. (GTK's negative indent: the first line at the margin, the
        rest that much further in.)"""
        name = f"hung{start}-{px}"
        if self.buf.get_tag_table().lookup(name) is None:
            self.buf.create_tag(name, left_margin=start, indent=start - px)
        return name

    def _hang(self, px):
        """The tag that puts a line's text px in (an item's text going on under it: _highlight), made the first
        time."""
        name = f"hang{px}"
        if self.buf.get_tag_table().lookup(name) is None:
            self.buf.create_tag(name, left_margin=px)
        return name

    def _continued(self, line_no):
        """(the item's line start, its LIST_ITEM_RE match, the line's spaces) if line line_no goes on with a list
        item: spaces at its start (Shift+Enter in an item puts them, as many as the item's marker takes), and only
        such lines between it and the item. Else None."""
        ok, start = self.buf.get_iter_at_line(line_no)
        if not ok:
            return None
        end = start.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        text = self.buf.get_slice(start, end, True)
        if not (spaces := re.match(r" +", text)) or LIST_ITEM_RE.match(text):
            return None
        for up in range(line_no - 1, -1, -1):
            if (hit := self._item_at(up)) is not None:
                return hit[0], hit[1], spaces.end()
            ok, at = self.buf.get_iter_at_line(up)
            stop = at.copy()
            if not stop.ends_line():
                stop.forward_to_line_end()
            if not self.buf.get_slice(at, stop, True).startswith(" "):
                return None
        return None

    def _continue_item(self):
        """Shift+Enter in a list item, or in a line going on with one: a new line of the item's text, under it (its
        spaces as many as the item's marker takes, as Markdown has it). True if it was in one."""
        if self.buf.get_has_selection():
            return False
        ins = self.buf.get_iter_at_mark(self.buf.get_insert())
        if (hit := self._item_at(ins.get_line())) is not None and ins.get_line_offset() >= hit[1].end():
            col = hit[1].end()
        elif (cont := self._continued(ins.get_line())) is not None and ins.get_line_offset() >= cont[2]:
            col = cont[2]
        else:
            return False
        self.buf.begin_user_action()
        self.buf.insert_at_cursor("\n" + " " * col)
        self.buf.end_user_action()
        return True

    def _unbreak(self):
        """Backspace at the start of a line going on with an item: back onto the line before (its spaces and the line
        break gone). True if it was."""
        if self.buf.get_has_selection():
            return False
        ins = self.buf.get_iter_at_mark(self.buf.get_insert())
        if (cont := self._continued(ins.get_line())) is None or ins.get_line_offset() != cont[2]:
            return False
        start = ins.copy()
        start.set_line_offset(0)
        start.backward_char()
        self.buf.begin_user_action()
        self.buf.delete(start, ins)
        self.buf.end_user_action()
        return True

    def _item_at(self, line_no):
        """(the line's start, its LIST_ITEM_RE match) if line line_no is a list item, else None."""
        ok, start = self.buf.get_iter_at_line(line_no)
        if not ok:
            return None
        end = start.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        m = LIST_ITEM_RE.match(self.buf.get_slice(start, end, True))
        return (start, m) if m else None

    def _indent_items(self, step):
        """Tab (step 1) or Shift+Tab (-1), as an editor's: each list item the caret or the selection is on goes in,
        or out, by its marker's width (- : two spaces, 1. : three). True if there was one."""
        bounds = self.buf.get_selection_bounds() or (self.buf.get_iter_at_mark(self.buf.get_insert()),) * 2
        lines = [n for n in range(bounds[0].get_line(), bounds[1].get_line() + 1) if self._item_at(n)]
        if not lines:
            return False
        self.buf.begin_user_action()
        for n in lines:
            start, m = self._item_at(n)
            unit = 2 if m.group(2) in "-*+" else len(m.group(2)) + 1
            if step > 0:
                self.buf.insert(start, " " * unit)
            elif (out := min(unit, len(m.group(1)) - len(m.group(1).lstrip(" ")))) > 0:
                end = start.copy()
                end.forward_chars(out)
                self.buf.delete(start, end)
        self.buf.end_user_action()
        return True

    def _unbullet(self):
        """Backspace just after a list item's bullet (or box): in by a level, it comes out one; at the left, the bullet
        goes and the line is text. True if it was."""
        if self.buf.get_has_selection():
            return False
        ins = self.buf.get_iter_at_mark(self.buf.get_insert())
        if (hit := self._item_at(ins.get_line())) is None or ins.get_line_offset() != hit[1].end():
            return False
        start, m = hit
        if m.group(1):
            return self._indent_items(-1)
        end = start.copy()
        end.forward_chars(m.end())
        self.buf.begin_user_action()
        self.buf.delete(start, end)
        self.buf.end_user_action()
        return True

    def _caret_moved(self):
        """Where the caret's gone, and the text's length then; its snap out of a list item's marker (_caret_in_marker)
        once GTK's done moving it, not in the midst of that (its own steps, a click, a drag still going on)."""
        was, self.caret_was = self.caret_was, (self.buf.get_iter_at_mark(self.buf.get_insert()).get_offset(),
                                               self.buf.get_char_count())
        if was is None or was[1] != self.buf.get_char_count():
            return  # (moved by the text changing, as it's typed or as an edit puts in a bullet: left where it's put)
        if self.snapping is None:
            self.snapping = GLib.idle_add(self._caret_in_marker, was[0], priority=GLib.PRIORITY_HIGH_IDLE)  # (before
                                                                                     # the frame it'd be drawn in)

    def _caret_in_marker(self, was):
        """A list item has two places for the caret: before its bullet (or number), and at its text. Its unseen indent,
        the bullet and the space after it (and a box) are one step: the caret moved into them goes on the way it was
        going, right to the text, left to before the bullet, and left again to the line before. A selection's moving
        end too."""
        self.snapping = None
        if self.notebook is None:
            return False
        ins = self.buf.get_iter_at_mark(self.buf.get_insert())
        if (hit := self._item_at(ins.get_line())) is None:
            return False
        start, m = hit
        before, text, at = len(m.group(1)), m.end(), ins.get_line_offset()
        if at == before or at >= text:
            return False
        to = start.copy()
        back = was > ins.get_offset()
        if at < before and back and was == start.get_offset() + before and start.get_line() > 0:
            to.backward_char()  # (left from before the bullet: the line before's end)
        else:
            to.forward_chars(before if at < before or back else text)
        if self.buf.get_has_selection():
            self.buf.move_mark(self.buf.get_insert(), to)
        else:
            self.buf.place_cursor(to)
        return False

    def _out_of_marker(self):
        """The caret in a list item's bullet, box or indent (all drawn over, or unseen): put just after them, so what's
        typed goes in the item's text, not its marker."""
        if self.buf.get_has_selection():
            return
        ins = self.buf.get_iter_at_mark(self.buf.get_insert())
        start = ins.copy()
        start.set_line_offset(0)
        if self._green_at(start.get_offset()) is not None:
            return  # (a line of what's proposed: its marker isn't the page's to type after)
        if (hit := self._item_at(ins.get_line())) is not None and ins.get_line_offset() < hit[1].end():
            to = hit[0].copy()
            to.forward_chars(hit[1].end())
            self.buf.place_cursor(to)
        elif (cont := self._continued(ins.get_line())) is not None and ins.get_line_offset() < cont[2]:
            to = ins.copy()
            to.set_line_offset(cont[2])  # (a line going on with an item: after its unseen spaces)
            self.buf.place_cursor(to)

    def _list_enter(self):
        """Enter in a list item, as Self's editor has it: the next item (a number one more, a box not ticked); Enter
        on an empty one ends the list. True if it was in one."""
        if self.buf.get_has_selection():
            return False
        ins = self.buf.get_iter_at_mark(self.buf.get_insert())
        line = ins.copy()
        line.set_line_offset(0)
        end = ins.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        text = self.buf.get_slice(line, end, True)
        m = LIST_ITEM_RE.match(text)
        if not m and (cont := self._continued(ins.get_line())) is not None:  # (on a line going on with an item: the
            m = cont[1]                                                        # list's next item)
            n = re.match(r"\d+", m.group(2))
            marker = f"{int(n.group(0)) + 1}{m.group(2)[n.end():]}" if n else m.group(2)
            prefix = m.group(1) + marker + m.group(3) + ("[ ] " if m.group(4) else "")
            self.buf.begin_user_action()
            if not text.strip():  # (an empty one: the new item in its place)
                self.buf.delete(line, end)
                self.buf.insert_at_cursor(prefix)
            else:
                self.buf.insert_at_cursor("\n" + prefix)
            self.buf.end_user_action()
            return True
        if not m or ins.get_line_offset() < m.end():
            return False
        self.buf.begin_user_action()
        if not text[m.end():].strip() and ins.equal(end):  # (an empty item: the list ends)
            self.buf.delete(line, end)
        else:
            n = re.match(r"\d+", m.group(2))
            marker = f"{int(n.group(0)) + 1}{m.group(2)[n.end():]}" if n else m.group(2)
            self.buf.insert_at_cursor("\n" + m.group(1) + marker + m.group(3) + ("[ ] " if m.group(4) else ""))
        self.buf.end_user_action()
        return True

    def _paste(self, view):
        """A web address pasted over a selection: the selection, as a link to it ([text](url)), as Self's editor has
        it. Anything else pasted as it is."""
        if not self.buf.get_has_selection():
            return
        view.stop_emission_by_name("paste-clipboard")
        a, b = self.buf.get_selection_bounds()
        if any(a.get_offset() < y and b.get_offset() > x for x, y in self._greens()):
            return  # (over what's proposed: it isn't the page's to paste over)
        marks = (self.buf.create_mark(None, a, True), self.buf.create_mark(None, b, False))
        view.get_clipboard().read_text_async(None, self._pasted, marks)

    def _pasted(self, clipboard, result, marks):
        try:
            pasted = clipboard.read_text_finish(result) or ""
        except GLib.Error:
            pasted = ""
        a, b = (self.buf.get_iter_at_mark(m) for m in marks)
        if any(a.get_offset() < y and b.get_offset() > x for x, y in self._greens()):
            for m in marks:  # (what's proposed shown in it while the clipboard was read: not pasted over, as _paste)
                self.buf.delete_mark(m)
            return
        chosen = self.buf.get_slice(a, b, True)
        url = pasted.strip()
        self.buf.begin_user_action()
        self.buf.delete(a, b)
        a = self.buf.get_iter_at_mark(marks[0])
        self.buf.insert(a, f"[{chosen}]({url})" if re.fullmatch(r"https?://\S+", url) and chosen else pasted)
        self.buf.end_user_action()
        for m in marks:
            self.buf.delete_mark(m)

    def _at(self, x, y):
        """What's under the pointer: ("link", page), ("ref", name) or ("url", address), or None."""
        bx, by = self.view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y))
        ok, it = self.view.get_iter_at_location(bx, by)
        if not ok:
            return None
        off, text = it.get_offset(), self.text()
        for m in LINK_RE.finditer(text):
            if m.start() <= off < m.end():
                if m.group(1).lstrip().startswith("@"):  # ([[@name]]: a reference)
                    return "ref", m.group(1).strip()[1:]
                return "link", page_target(self.page, m.group(1))
        for m in REF_RE.finditer(text):
            if m.start() <= off < m.end():
                return "ref", m.group(1) or m.group(2)
        for m in MD_LINK_RE.finditer(text):
            if m.start() <= off < m.end():
                return "url", m.group(2)
        for m in URL_RE.finditer(text):
            if m.start() <= off < m.end():
                return "url", m.group(0)
        return None

    def _pressed(self, gesture, n, x, y):
        """A click come down on a link: taken from the text view, which would put the caret in it (and, the line's
        markup shown for the caret, move the link from under the pointer); it's followed as the click ends (_click),
        the caret left where it was. With ⌥ (Alt) or Shift, or a second click, it's the text view's: the caret in it,
        a selection. Elsewhere, the text view's, but the caret it puts at the end of a line's text goes past the
        markup hidden after it (_past_markup)."""
        self.pressed = None
        if gesture.get_current_button() != 1 or n != 1 or \
                gesture.get_current_event_state() & (Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SHIFT_MASK):
            return
        if (hit := self._at(x, y)) is not None:
            self.pressed = hit
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            return
        # where the line's text ends as it's shown, and where the line does, while its markup is still hidden
        line = self.view.get_line_at_y(self.view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, 0, int(y))[1])[0]
        end = line.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        shown, hidden = end.copy(), self.buf.get_tag_table().lookup("hidden")
        while shown.compare(line) > 0 and (before := shown.copy()).backward_char() and before.has_tag(hidden):
            shown = before
        if shown.compare(end) < 0:
            GLib.idle_add(self._past_markup, shown.get_offset(), end.get_offset())

    def _past_markup(self, shown, end):
        """After a click, once the text view has put the caret: put at the end of a line's text, just before markup
        hidden there (a code span's closing `, a bold's **, a link's address or ]]), it goes after it, at the line's
        end, out of the span, as a click at the end of a line means."""
        ins = self.buf.get_iter_at_mark(self.buf.get_insert())
        if not self.buf.get_has_selection() and ins.get_offset() == shown and end <= self.buf.get_char_count():
            self.buf.place_cursor(self.buf.get_iter_at_offset(end))
        return False

    def _click(self, _gesture, _n, _x, _y):
        hit, self.pressed = self.pressed, None
        if hit is None:
            return
        if hit[0] == "link":
            self.open_page(hit[1])
        elif hit[0] == "url" and hit[1].startswith(CITE_SCHEME):
            self.win.open_citation(hit[1])
        elif hit[0] == "url":
            Gtk.UriLauncher(uri=hit[1]).launch(self.get_root(), None, None)
        else:
            self.win.open_ref(hit[1])

    @staticmethod
    def _pointer(view, want):
        view = view()
        if view is not None:
            pointers.set_pointer(view, want)
        return False

    # ---- @
    def _token_changed(self):
        """The caret moved or the text changed: @'s list follows the @word it's at the end of, if any. Once @'s list
        is up, what's typed after the @ is its search, spaces and all (each word found in any order), till Esc, Enter
        or Tab takes or leaves it, or a new line, or the caret goes back before the @. A new @ or # typed after a space
        starts a list of its own, and what's before it stays as it was typed. # does the same, for GitHub's issues and
        pull requests, where there are any and only as long as what's typed still asks for them (issue_query)."""
        ins = self.buf.get_iter_at_mark(self.buf.get_insert())
        line = ins.copy()
        line.set_line_offset(0)
        before = self.buf.get_slice(line, ins, True)
        start, offset = line.get_offset(), ins.get_offset()
        if self.token is not None and self.typed_at is not None and self.typed_at > self.token[0] and \
                start < self.typed_at <= offset and ISSUE_OPENS.fullmatch(before[self.typed_at - start - 1]):
            self.token, self.pop_moved = None, False
        if self.token is not None and self.token[0] < offset:
            typed = self.buf.get_slice(self.buf.get_iter_at_offset(self.token[0]), ins, True)
            if typed.startswith("#") and "\n" not in typed:
                return self._issue_search(before, self.token[0] - start, start)
            if typed.startswith("+") and LINKING_RE.fullmatch(typed) and self._pages_made():
                self.token = (self.token[0], typed[1:])
                self.token_far = max(self.token_far or 0, offset)
                self._mark_token()
                return self._fill_pop()
            if typed.startswith("@") and "\n" not in typed:
                self.token = (self.token[0], typed[1:].lstrip("{"))
                self.token_far = max(self.token_far or 0, offset)
                self._mark_token()
                return self._fill_pop()
        m = re.search(r"(?<![\w@])@(\{[^}\n]*|[\w./:\-]*)$", before) or re.search(r"#[\w./:\-]*$", before) or \
            (plus if self._pages_made() and (plus := LINKING_RE.search(before)) and
             not (not plus.group(1) and not before[:plus.start()].strip()) and not self._in_code(offset) else None)
        if not m or offset - len(m.group(0)) != self.typed_at:  # (only an @ just typed: the caret come
            return self._hide_pop()  # back to an @name's end doesn't start its search again, to take in what's next)
        if m.group(0).startswith("#"):
            return self._issue_search(before, m.start(), start)
        self.token = (offset - len(m.group(0)), m.group(1).lstrip("{") if m.group(0).startswith("@") else m.group(1))
        self.token_far = offset
        self._mark_token()
        self._fill_pop()

    def _issue_search(self, before, at, start):
        """A # at position at of its line (it starts at offset start; before: the line up to the caret), and what's been
        typed after it: #'s list follows, as long as it's asking (issue_query, or it's in code: neither says so). A
        line's first # with nothing after it yet waits to see, listing nothing: a heading's, as like as not."""
        query = issue_query(before, at)
        if query is None or self._in_code(start + len(before)):
            return self._hide_pop()
        if not query and at == ISSUE_LEAD.match(before).end():
            self.token = self.token_far = None  # (typed_at kept: what follows decides)
            self._mark_token()
            if self.pop.get_visible():
                self.pop.popdown()
            return None
        self.token = (start + at, query)
        self.token_far = max(self.token_far or 0, start + len(before))
        self._mark_token()
        return self._fill_pop()

    def _in_code(self, offset):
        """Whether a place in the page is in a code block or a `code` span (a # there is a comment's, a colour's)."""
        text = self.text()
        before = text[:offset]
        at = before.count("\n")
        return any(first <= at <= last for first, last, _closed in markdownview.fence_spans(text.split("\n"), typing=(at,))) or \
            before.rpartition("\n")[2].count("`") % 2 == 1

    def _mark_token(self):
        """@'s search while it's on: what's been typed of it, from the @ to the farthest the caret has been, in the
        @names' colour; none once it's left."""
        start, end = self.buf.get_bounds()
        self.buf.remove_tag_by_name("token", start, end)
        if self.token is not None and self.token_far is not None:
            b = min(self.token_far, self.buf.get_char_count())
            if b > self.token[0]:
                self.buf.apply_tag_by_name("token", self.buf.get_iter_at_offset(self.token[0]),
                                           self.buf.get_iter_at_offset(b))

    def _pop_bind(self, i, icon, name, where):
        it = self.pop_shown[i]
        icon.icon = it["icon"]
        name.set_text(it.get("label") or it["surface"])
        where.set_text(f"{it['what']} · {it['summary']}")
        widgets.time_tip(where, it.get("at"), it.get("ago"))

    def pop_selected(self):
        """The item @'s list has selected, or None."""
        i = self.picks.picked()
        return self.pop_shown[i] if i is not None and i < len(self.pop_shown) else None

    def _issue_list(self):
        """Whether the list up is #'s."""
        return self.token is not None and self.buf.get_iter_at_offset(self.token[0]).get_char() == "#"

    def _link_list(self):
        """Whether the list up is +'s: the notebook's pages, to link to."""
        return self.token is not None and self.buf.get_iter_at_offset(self.token[0]).get_char() == "+"

    def link_items(self, query):
        """What +'s list lists for what's typed after the +: this page's sub-pages first, then the notebook's other
        pages, each word typed found in the name, in any order (the pages list's names: PagesList.names); last, a new
        sub-page of that name while no page is named it, in any case. Each is written as a link from here is (page_link:
        +Ideas for a sub-page)."""
        pages = self.notebook["pages"]
        names = set(self.win.pages.names()[0]) if self.notebook is self.win.current else \
            {n for n, text in pages.items() if text.strip()} | {HOME}
        names.discard(self.page)
        words = query.lower().replace(":", " ").split()
        found = [n for n in names if n and all(w in n.lower() for w in words)]
        mine = self.page + ":"
        found.sort(key=lambda n: (not n.startswith(mine), n.count(":"), n.lower()))
        items = [{"surface": n, "label": page_link(self.page, n), "link": page_link(self.page, n), "icon": "page",
                  "what": "Sub-page" if n.startswith(mine) else "Page",
                  "summary": next((line.strip().lstrip("#>-*+ ").strip()[:60] for line in pages.get(n, "").split("\n")
                                   if line.strip().lstrip("#>-*+ ").strip()), "nothing in it yet")}
                 for n in found]
        new = page_target(self.page, "+" + query) if re.fullmatch(r"\w[\w.\-]*(?::\w[\w.\-]*)*", query) else None
        if new and new.lower() not in {n.lower() for n in names}:
            items.append({"surface": new, "label": "+" + query, "link": "+" + query, "icon": "page",
                             "what": "New sub-page", "summary": f"of {short_page(self.page)}"})
        return items

    def _exact(self):
        """Whether #'s list's first is the number typed: what's been typed means that one (issues: it's first)."""
        return bool(self.pop_shown) and self.pop_shown[0].get("label") == self.token[1].strip()

    def _fill_pop(self):
        space = self.win.space()
        issues, linking = self._issue_list(), self._link_list()
        kept = self.pop_selected() if self.pop_moved and self.pop_query == self.token[1] else None
        if linking:
            self.pop_shown = self.link_items(self.token[1])
            matched = sum(1 for it in self.pop_shown if it["what"] != "New sub-page")
        else:
            self.pop_shown, matched = ([], 0) if space is None else \
                space.issues(self.token[1]) if issues else space.search(self.token[1])  # (all of them)
        if issues and not self.pop_shown:  # (a # is an ordinary thing to type: no list, and nothing said)
            return self._hide_pop()
        self.pop_query = self.token[1]
        at = next((i for i, it in enumerate(self.pop_shown) if kept and it["surface"] == kept["surface"]), None)
        self.pop_moved = at is not None  # (the same search, listed again: what's picked stays picked)
        if at is None and (not issues or self._exact()) and not (linking and not self.token[1]):
            at = 0  # (# picks nothing it wasn't asked to, nor a + with nothing after it yet: Enter's a new line)
        if linking:
            note = f"{matched} page{'s' if matched != 1 else ''}" if matched else "No page named that yet"
        elif space is None:
            note = wording.say("meta.nothing_found")
        else:
            if self.waiting is None:  # (catalogued afresh if it's old: listed again once it's in)
                self.waiting = space.refresh(self._catalogued)
            note = wording.say("meta.found", n=matched) if matched else wording.say("meta.nothing_found") \
                if self.win.read_folders() else \
                "Nothing found · open a chat for its folder's names"
        it = self.buf.get_iter_at_offset(self.token[0])
        loc = self.view.char_rect(it)
        x, y = self.view.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, loc.x, loc.y)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = x, y, 1, loc.height
        self.picks.show(len(self.pop_shown), at, note, rect, coming=not linking and space is not None and space.busy)

    def _catalogued(self):
        space = self.win.space()  # (a folder's in, another's on its way: waited for in turn, the list up or not)
        self.waiting = space.refresh(self._catalogued) if space is not None and space.busy else None
        self.github_changed()

    def github_changed(self):
        """What the names are has changed (the catalogue's in, or GitHub's lists are new)."""
        self._highlight()  # (the @names' logos, now they're known)
        if self.pop.get_visible() and self.token is not None:
            self._fill_pop()

    def _hide_pop(self):
        self.token = self.token_far = self.typed_at = None
        self.pop_moved = False
        self._mark_token()
        if self.pop.get_visible():
            self.pop.popdown()

    def _key(self, _c, keyval, _code, state):
        if keyval == Gdk.KEY_Escape and self.search_bar.get_search_mode() and not self.pop.get_visible():
            FindInText.close_search(self)  # (the find bar open as you write: closed, the caret left where it is)
            return True
        primary = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK)
        if primary and keyval in (Gdk.KEY_z, Gdk.KEY_Z, Gdk.KEY_y, Gdk.KEY_Y):  # (the page's own Undo: PageHistory)
            again = keyval in (Gdk.KEY_y, Gdk.KEY_Y) or state & Gdk.ModifierType.SHIFT_MASK
            self.history.redo() if again else self.history.undo()
            return True
        if keyval in (Gdk.KEY_Tab, Gdk.KEY_Escape) and not self.pop.get_visible() and not state & (
                Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK | Gdk.ModifierType.ALT_MASK
                | Gdk.ModifierType.SHIFT_MASK) and \
                (e := self._proposal_at(self.buf.get_property("cursor-position"))) is not None:
            self.decide(e["p"], keyval == Gdk.KEY_Tab)  # (the caret in a change proposed: Tab takes it, Esc leaves it)
            return True
        if self._edits_green(keyval, state):
            return True  # (what's proposed isn't the page's: nothing typed into it)
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab, Gdk.KEY_KP_Tab) and \
                self.pop.get_visible() and not self.pop_moved and (
                    self._issue_list() and not self._exact() or self._link_list() and not self.token[1]):
            # (#'s list, and nothing asked of it: what was typed stays as typed. Enter's a new line, as it always is)
            self._hide_pop()
            if keyval not in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                return True
        if state & Gdk.ModifierType.ALT_MASK and not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK) \
                and not self.pop.get_visible() and self._moved_heading(keyval, bool(state & Gdk.ModifierType.SHIFT_MASK)):
            return True
        if keyval in (Gdk.KEY_BackSpace, Gdk.KEY_Delete, Gdk.KEY_KP_Delete) or (
                Gdk.keyval_to_unicode(keyval) and not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK)):
            self._whole_links()  # (what's selected, about to go: no link's marks left behind)
        if Gdk.keyval_to_unicode(keyval) and not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK):
            self._into_span()  # (a whole formatted span selected: what's typed goes in it, the formatting kept)
        if composerview.leave_wrapped(self.buf, keyval, state):
            return True  # (just formatted, and Left or Right: outside its marks)
        if not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK) and \
                composerview.wrap_selection(self.buf, chr(Gdk.keyval_to_unicode(keyval) or 0)):
            return True  # (a selection, and * ` or /: bold, code, italic)
        if keyval in (Gdk.KEY_space, Gdk.KEY_Return, Gdk.KEY_KP_Enter) and \
                not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK):
            self._em_dash()  # (and the space or Enter goes in after it, as typed)
        if (keyval == Gdk.KEY_space or keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not self.pop.get_visible()) and \
                self._sub_page() and keyval == Gdk.KEY_space:
            return False  # (the space follows the link, as typed; Enter's the Enter it would have been: a list goes on)
        if keyval == Gdk.KEY_BackSpace and (self._unbullet() or self._unbreak()):
            return True
        if keyval in (Gdk.KEY_BackSpace, Gdk.KEY_Delete, Gdk.KEY_KP_Delete) and \
                self._delete_at_link(keyval == Gdk.KEY_BackSpace):
            return True
        if keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab, Gdk.KEY_KP_Tab) and not self.pop.get_visible() and \
                self._indent_items(-1 if keyval == Gdk.KEY_ISO_Left_Tab or state & Gdk.ModifierType.SHIFT_MASK else 1):
            return True
        if Gdk.keyval_to_unicode(keyval) and not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK):
            self._out_of_marker()
        if not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK) and \
                (Gdk.keyval_to_unicode(keyval) or keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter)):
            self._out_of_link()  # (what's typed at a link's edge goes beside it, not into its name)
        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up) and not self.pop.get_visible() and not state & (
                Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK
                | Gdk.ModifierType.ALT_MASK) and self._up_to_title():
            return True
        if not self.pop.get_visible():
            if keyval not in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                return False  # (Shift+Enter: a line of the item's text under it; Enter: the list's next item)
            return self._continue_item() if state & Gdk.ModifierType.SHIFT_MASK else self._list_enter()
        if keyval == Gdk.KEY_Escape:
            self._hide_pop()
            return True
        steps = widgets.PickList.STEPS
        if state & Gdk.ModifierType.SHIFT_MASK and keyval in (*steps, Gdk.KEY_Left, Gdk.KEY_Right):
            self._hide_pop()  # (Shift and an arrow: selecting the text, not picking in the list)
            return False
        if keyval in steps:
            if self.pop_shown:
                self.picks.move(steps[keyval])
                self.pop_moved = True
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_Tab) and self.pop_selected() is not None:
            if self._issue_list() and not self.pop_moved:
                self.pop_sel.set_selected(0)  # (the number typed: not whatever the pointer's over)
            if self._link_list() and keyval != Gdk.KEY_Tab:
                self._insert_ref(after="")  # (+'s link, then the Enter it was: a list goes on)
                return self._continue_item() if state & Gdk.ModifierType.SHIFT_MASK else self._list_enter()
            self._insert_ref()
            return True
        return False

    def _moved_heading(self, keyval, shift):
        """_heading_key, with what's proposed taken out first wherever it could move lines (Up or Down: a heading's, a
        list item's; Left or Right: on a heading's line): lines moved with what's proposed in them would make that the
        page's. Nothing done: the caret and the selection where they were."""
        if not self._greens() or keyval not in (Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Right):
            return self._heading_key(keyval, shift)
        marks = (self.buf.get_insert(), self.buf.get_selection_bound())
        if keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
            ins, sel = (self.buf.get_iter_at_mark(m).get_line() for m in marks)
            lines = self.text().split("\n")
            if not any(re.match(r"#{1,6}\s", lines[n]) for n in range(min(ins, sel), max(ins, sel) + 1)):
                return self._heading_key(keyval, shift)
        ins, sel = (self.kept_offset(self.buf.get_iter_at_mark(m).get_offset()) for m in marks)
        with self._greens_out():
            done = self._heading_key(keyval, shift)
        if done:
            self._changed(self.buf)
        else:
            self.buf.select_range(*(self.buf.get_iter_at_offset(self.buf_offset(k, past=False)) for k in (ins, sel)))
            self._highlight()
        return done

    def _heading_key(self, keyval, shift):
        """Alt and an arrow on a heading, as Org's: up or down, swapped with its sibling that way, all under it going
        along; Shift+Alt left or right, its level and all its children's up or down one; Alt left or right, its level
        alone, or every heading's in the selection. True if it was one of these on a heading (with nothing to do, as
        no sibling or a level past 1 to 6, nothing's done): elsewhere the key's as it always was."""
        lines = self.text().split("\n")
        levels = heading_levels(lines)
        at = self.buf.get_iter_at_mark(self.buf.get_insert()).get_line()
        if keyval in (Gdk.KEY_Up, Gdk.KEY_Down) and not shift:
            by = -1 if keyval == Gdk.KEY_Up else 1
            if not levels[at]:
                return self._move_item(lines, at, by)
            self._swap_heading(lines, at, by)
            return True
        if keyval not in (Gdk.KEY_Left, Gdk.KEY_Right):
            return False
        if shift:
            rows = [i for i in range(at, subtree_end(levels, at)) if levels[i]] if levels[at] else []
        elif self.buf.get_has_selection():
            start, end = self.buf.get_selection_bounds()
            last = end.get_line() - (1 if end.get_line_offset() == 0 and end.get_line() > start.get_line() else 0)
            rows = [i for i in range(start.get_line(), last + 1) if levels[i]]
        else:
            rows = [at] if levels[at] else []
        if not rows:
            return False
        by = 1 if keyval == Gdk.KEY_Right else -1
        if all(1 <= levels[i] + by <= 6 for i in rows):
            self._in_one_step(lambda: self._shift_headings(rows, by))
        return True

    def _shift_headings(self, rows, by):
        for row in rows:
            start = self.buf.get_iter_at_line(row)[1]
            if by > 0:
                self.buf.insert(start, "#")
            else:
                after = start.copy()
                after.forward_char()
                self.buf.delete(start, after)

    def _move_item(self, lines, at, by):
        """Alt and Up or Down in a list item: it, and all under it, past its sibling that way. True if it's in one (with
        none that way, nothing's done); elsewhere the key's as it always was."""
        start = at
        while start > 0 and lines[start].startswith((" ", "\t")) and lines[start].strip() and \
                not LIST_ITEM_RE.match(lines[start]):
            start -= 1
        if not LIST_ITEM_RE.match(lines[start]):
            return False
        self._swap_lines(lines, composerview.swap_list_item(lines, at, by))
        return True

    def _swap_heading(self, lines, at, by):
        self._swap_lines(lines, swap_with_sibling(lines, at, by))

    def _swap_lines(self, lines, swapped):
        if swapped is None:
            return
        new, one, three, moved = swapped
        column = self.buf.get_iter_at_mark(self.buf.get_insert()).get_line_offset()

        def replace():
            start = self.buf.get_iter_at_line(one)[1]
            end = self.buf.get_iter_at_line(three)[1] if three < len(lines) else self.buf.get_end_iter()
            self.buf.delete(start, end)
            self.buf.insert(start, "\n".join(new) + ("\n" if three < len(lines) else ""))
            self.buf.place_cursor(self.buf.get_iter_at_line_offset(moved, column)[1])
        self._in_one_step(replace)

    def _in_one_step(self, edit):
        """An edit of many steps: one undo, the page kept and drawn again once, when it's done."""
        was, self.loading = self.loading, True
        self.buf.begin_user_action()
        try:
            edit()
        finally:
            self.buf.end_user_action()
            self.loading = was
        self._changed(self.buf)

    def _whole_links(self):
        """A selection about to be cut, copied, deleted or typed over: no link split by it (whole_links), the marks
        unseen so nothing looks different."""
        if not self.buf.get_has_selection():
            return
        start, end = (i.get_offset() for i in self.buf.get_selection_bounds())
        wide = whole_links(self.text(), start, end)
        if wide != (start, end):
            ins = self.buf.get_iter_at_mark(self.buf.get_insert()).get_offset()
            first, last = self.buf.get_iter_at_offset(wide[0]), self.buf.get_iter_at_offset(wide[1])
            self.buf.select_range(*((first, last) if ins == start else (last, first)))

    def _into_span(self):
        """A whole formatted span selected (its text and the markup round it, hidden: a double-click on it, say):
        narrowed to its text, so what's typed in its place is formatted so, the markup kept. Markup shown (Settings
        → Markup visibility, the caret's line) is the selection's as it's shown."""
        if not self.buf.get_has_selection():
            return
        s, e = (i.get_offset() for i in self.buf.get_selection_bounds())
        text, hidden = self.text(), self.buf.get_tag_table().lookup("hidden")
        start = text.rfind("\n", 0, s) + 1
        end = len(text) if (n := text.find("\n", s)) < 0 else n
        if e > end:
            return

        def unseen(x, y):
            return all(self.buf.get_iter_at_offset(i).has_tag(hidden) for i in range(x, y))
        for rx, _name, side in self.INLINE:
            for m in rx.finditer(text[start:end]):
                x, y = start + m.start(), start + m.end()
                if x <= s <= x + side and y - side <= e <= y and (s, e) != (x + side, y - side) and \
                        unseen(s, x + side) and unseen(y - side, e):
                    self.buf.select_range(self.buf.get_iter_at_offset(x + side), self.buf.get_iter_at_offset(y - side))
                    return

    def _em_dash(self):
        """-- just typed on its own, whitespace before it, then a space or Enter: it's an em dash, as the text's -- is
        drawn in a reply (markdownview.EM_DASH). Not in code: a `span` (an odd number of backticks before it on the
        line, so one still being typed too) or a ``` block. True if it was."""
        if self.notebook is None or self.buf.get_has_selection():
            return False
        ins = self.buf.get_iter_at_mark(self.buf.get_insert())
        line = ins.copy()
        line.set_line_offset(0)
        before = self.buf.get_slice(line, ins, True)
        if not re.search(r"(?<=\s)--$", before) or before[:-2].count("`") % 2:
            return False
        start = self.buf.get_iter_at_offset(ins.get_offset() - 2)
        table = self.buf.get_tag_table()
        if any(start.has_tag(table.lookup(name)) for name in ("verbatim", "pre")):
            return False
        self.buf.begin_user_action()
        self.buf.delete(start, ins)
        self.buf.insert_at_cursor("\u2014")
        self.buf.end_user_action()
        return True

    def _pages_made(self):
        """Does a link here make a page (+'s list of pages to link to, +Ideas a sub-page)? Not in a one-page notebook
        (EditorHost.pages_made)."""
        return self.notebook is not None and getattr(self.win, "pages_made", True)

    def _sub_page(self):
        """+Ideas just typed, then a space or Enter: it becomes [[+Ideas]], a link to this page's sub-page Ideas, which
        the page list has for the link, grey until something's written in it, as Zim does. True if it was."""
        if not self._pages_made() or self.buf.get_has_selection():
            return False
        ins = self.buf.get_iter_at_mark(self.buf.get_insert())
        line = ins.copy()
        line.set_line_offset(0)
        m = SUBPAGE_RE.search(self.buf.get_slice(line, ins, True))
        if not m:
            return False
        start = self.buf.get_iter_at_offset(ins.get_offset() - len(m.group(0)))
        self.buf.begin_user_action()
        self.buf.delete(start, ins)
        self.buf.insert_at_cursor(f"[[+{m.group(1)}]]")  # (listed, and drawn, as the text changes: _changed)
        self.buf.end_user_action()
        return True

    def _delete_at_link(self, back):
        """Backspace or Delete at a link: its hidden marks ([[ ]], +, target|) are never broken into text. Backspace
        just after it takes its name's last letter, Delete just before it its first, as a link's text is edited in
        Zim; once there's none left, the link goes. At its name's first letter Backspace reaches past the marks to
        the text before the link, at its last Delete to the text after. A pull request's or an issue's #12 goes whole,
        from anywhere in it (as #1, it would still be #12 when it's clicked). True if it was a link's."""
        if self.notebook is None or self.buf.get_has_selection():
            return False
        at = self.buf.get_property("cursor-position")
        text = self.text()
        for m in LINK_RE.finditer(text):
            if m.start() > at:
                break
            if at > m.end():
                continue
            a, b = link_name(m)
            if github_ref(m) and (a < at <= m.end() if back else m.start() <= at < b):
                return self._delete_span(m.start(), m.end())
            if back and (at == m.end() or b < at < m.end()):
                return self._shorten(m, a, b, last=True)
            if not back and (at == m.start() or m.start() < at < a):
                return self._shorten(m, a, b, last=False)
            if back and m.start() < at <= a:
                return self._delete_span(m.start() - 1, m.start()) if m.start() else True
            if not back and b <= at < m.end():
                return self._delete_span(m.end(), m.end() + 1) if m.end() < len(text) else True
        return False

    def _shorten(self, m, a, b, last):
        if b - a <= 1:
            return self._delete_span(m.start(), m.end())
        return self._delete_span(b - 1, b) if last else self._delete_span(a, a + 1)

    def _delete_span(self, a, b):
        if any(a < y and b > x for x, y in self._greens()):
            return True  # (what's proposed, in a link or beside it: not the page's to delete)
        self.buf.begin_user_action()
        self.buf.delete(self.buf.get_iter_at_offset(a), self.buf.get_iter_at_offset(b))
        self.buf.end_user_action()
        return True

    def _out_of_link(self):
        """The caret at a link's name's first or last letter (or among its hidden marks): put just outside the link,
        so what's typed there goes beside it, not into its name. Inside its name, it stays."""
        if self.notebook is None or self.buf.get_has_selection():
            return
        at = self.buf.get_property("cursor-position")
        for m in LINK_RE.finditer(self.text()):
            if m.start() > at:
                break
            a, b = link_name(m)
            to = m.start() if m.start() < at <= a else m.end() if b <= at < m.end() or github_ref(m) and a < at < b \
                else None
            if to is not None:
                self.buf.place_cursor(self.buf.get_iter_at_offset(to))
                return

    def _insert_ref(self, after=" "):
        item = self.pop_selected()
        if item is None or self.token is None:
            return
        start = self.buf.get_iter_at_offset(self.token[0])
        end = self.buf.get_iter_at_mark(self.buf.get_insert())
        self._hide_pop()
        self.buf.begin_user_action()
        self.buf.delete(start, end)
        self.buf.insert_at_cursor((f"[[{item['link']}]]" if "link" in item else ref_text(item["surface"], item.get("label")))
                                  + after)
        self.buf.end_user_action()
        self.view.grab_focus()
        self._hide_pop()
