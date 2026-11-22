"""composerview — the box a message is written in (Composer): its markdown shown as it's written
(ComposerMarkdown), a selection wrapped in marks, a word's steps, a list item swapped with its neighbour, the
keys that send or break a line, and the prompt a session was started with (start_prompt_view).
"""
import re
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402,F401

from baudelib import tokens, wording  # noqa: E402
from widgets import code_scale, ensure_icons  # noqa: E402
from markdownview import BULLET, INDENT_STEP, MarkdownView, SUB_BULLET, _ITEM, bullet_for, fence_spans  # noqa: E402


# ------------------------------------------------------------- composer --
# The box a message is written in (Baude's, Record's): its markdown shown as it's written.

HEADING_SCALE = {1: 1.44, 2: 1.2}  # Pango's x-large / large; h3+ are bold at body size


WRAP_MARKS = {"*": "**", "`": "`", "/": "*"}  # (a selection, and one of these typed: bold, code, italic)
_LINE_LEAD = re.compile(r"[ \t]*(?:(?:[-*+•◦]|\d+[.)])[ \t]+(?:\[[ xX]\][ \t]+)?|#{1,6}[ \t]+|>[ \t]?)?")


def wrap_selection(buf, ch):
    """A selection, and *, ` or / typed (ch): what's selected made bold (**…**), code (`…`) or italic (*…*), as
    Markdown has them, each of its lines on its own (a list's marker, a box, a heading's #s left out), and still
    selected, so another can go round it too (* then /: bold italic). Marked so already (the marks just outside it):
    they're taken off. False (nothing done: the key types as ever) for another key, or nothing selected but space."""
    mark = WRAP_MARKS.get(ch)
    if mark is None or not buf.get_has_selection():
        return False
    start, end = buf.get_selection_bounds()
    a, b, n = start.get_offset(), end.get_offset(), len(mark)
    full = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
    if "\n" not in full[a:b]:  # (marked so already: unmarked)
        run = lambda i, step: next((k for k in range(8) if not (0 <= i + step * k < len(full)) or  # noqa: E731
                                    full[i + step * k] != mark[0]), 8)
        left, right = run(a - 1, -1), run(b, 1)
        if (left >= 1 and right >= 1) if mark == "`" else (left >= 2 and right >= 2) if mark == "**" else \
                (left in (1, 3) and right in (1, 3)):
            buf.begin_user_action()
            buf.delete(buf.get_iter_at_offset(b), buf.get_iter_at_offset(b + n))
            buf.delete(buf.get_iter_at_offset(a - n), buf.get_iter_at_offset(a))
            buf.end_user_action()
            buf.select_range(buf.get_iter_at_offset(a - n), buf.get_iter_at_offset(b - n))
            buf.just_wrapped = (a - n, b - n)
            return True
    pieces, at = [], a
    for line in full[a:b].split("\n"):
        s, e = at, at + len(line)
        at = e + 1
        if s == full.rfind("\n", 0, s) + 1:  # (from the line's start: its marker, #s or > left out)
            s = max(s, min(e, _LINE_LEAD.match(full, s).end()))
        while s < e and full[s].isspace():
            s += 1
        while e > s and full[e - 1].isspace():
            e -= 1
        if s < e:
            pieces.append((s, e))
    if not pieces:
        return False
    buf.begin_user_action()
    for s, e in reversed(pieces):
        buf.insert(buf.get_iter_at_offset(e), mark)
        buf.insert(buf.get_iter_at_offset(s), mark)
    buf.end_user_action()
    first, last = pieces[0][0] + n, pieces[-1][1] + n * (2 * len(pieces) - 1)
    buf.select_range(buf.get_iter_at_offset(first), buf.get_iter_at_offset(last))
    buf.just_wrapped = (first, last)
    return True


def leave_wrapped(buf, keyval, state):
    """Left or Right on its own with what wrap_selection just formatted still selected: the caret outside its marks, all
    that are round it (before the first, or after the last), so what's typed next is plain. True if so. A selection made
    any other way collapses as ever, to its own end."""
    if keyval not in (Gdk.KEY_Left, Gdk.KEY_Right) or not buf.get_has_selection() or \
            state & (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK |
                     Gdk.ModifierType.ALT_MASK):
        return False
    start, end = buf.get_selection_bounds()
    a, b = start.get_offset(), end.get_offset()
    if getattr(buf, "just_wrapped", None) != (a, b):
        return False
    buf.just_wrapped = None
    full = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
    while mark := next((m for m in ("**", "`", "*") if full[max(a - len(m), 0):a] == m and full[b:b + len(m)] == m), None):
        a, b = a - len(mark), b + len(mark)
    buf.place_cursor(buf.get_iter_at_offset(b if keyval == Gdk.KEY_Right else a))
    return True


def no_text_drag(view):
    """A text view whose selection isn't dragged away: GTK's, pressed in and dragged, moves the text, which
    surprises. A press in the selection puts the caret there, as a press anywhere else does, so a drag selects
    anew (Shift, Ctrl, ⌘ or Alt held, and a double or triple click: as GTK has them). view.press_at(x, y): the
    same, for a press at (x, y) in the view."""
    def press_at(x, y):
        buf = view.get_buffer()
        if not buf.get_has_selection():
            return False
        bx, by = view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y))
        ok, at = view.get_iter_at_location(bx, by)
        start, end = buf.get_selection_bounds()
        if not ok or not start.compare(at) <= 0 < end.compare(at):
            return False
        buf.place_cursor(at)
        return True

    def pressed(gesture, n, x, y):
        held = Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK | \
            Gdk.ModifierType.ALT_MASK
        if n == 1 and gesture.get_current_button() == Gdk.BUTTON_PRIMARY and \
                not gesture.get_current_event_state() & held:
            press_at(x, y)
    click = Gtk.GestureClick(button=0, propagation_phase=Gtk.PropagationPhase.CAPTURE)  # (before the view's own)
    click.connect("pressed", pressed)
    view.add_controller(click)
    view.press_at = press_at
    return view


def word_stop(text, at, forward=True, hidden=lambda _i: False):
    """Where Option (Ctrl elsewhere) and an arrow goes from at: to the end of the next word, or back to the start of the
    last, and also to each mark that follows a word where nothing else does (a closing }, a full stop at a line's end), so
    a line can be selected to exactly there. A mark inside a word (orders-api, a.b) or leading one (@{, an opening quote)
    isn't a stop, nor is what's the same over again (... **). hidden(i): the character at i is unseen, as if it weren't
    there; a word's stop is outside what's unseen around it, so a link's marks go with it."""
    n = len(text)

    def seen(i, step):
        while 0 <= i < n and hidden(i):
            i += step
        return i

    def word(i):
        if not 0 <= i < n:
            return False
        if text[i] in "'’":  # (don't, inside a word)
            return 0 < i < n - 1 and text[i - 1].isalnum() and text[i + 1].isalnum()
        return text[i].isalnum() or text[i] == "_"

    def space(i):
        return 0 <= i < n and text[i].isspace()

    if forward:
        i = seen(at, 1)
        while True:
            while space(i):
                i = seen(i + 1, 1)
            if i >= n:
                return n
            if word(i):
                while word(i):
                    i = seen(i + 1, 1)
                return i
            j = i
            while j < n and not space(j) and not word(j):
                j = seen(j + 1, 1)
            if word(j):  # (marks before a word, or inside one: on to its end)
                i = j
                continue
            mark, i = text[i], seen(i + 1, 1)
            while i < n and text[i] == mark:
                i = seen(i + 1, 1)
            return i

    def start(i):  # (and any unseen before it, as what they mark is what's being reached)
        while i > 0 and hidden(i - 1):
            i -= 1
        return i
    k = seen(at - 1, -1)
    while True:
        while space(k):
            k = seen(k - 1, -1)
        if k < 0:
            return 0
        if word(k):
            while word(seen(k - 1, -1)):
                k = seen(k - 1, -1)
            return start(k)
        j, first = k, k
        while j >= 0 and not space(j) and not word(j):
            first, j = j, seen(j - 1, -1)
        after = seen(k + 1, 1)
        if word(after):  # (caret in or just before a word: marks inside it are passed, ones leading it the stop)
            if word(j):
                k = j
                continue
            return start(first)
        mark = text[k]
        while seen(k - 1, -1) >= 0 and text[seen(k - 1, -1)] == mark:
            k = seen(k - 1, -1)
        return k  # (just before it: an unseen } before a full stop stays with what it closes)


def word_steps(view):
    """A text view's word steps by word_stop, its selection going with them with Shift, what its "hidden" tag hides (the
    notes' markup) not counted."""
    def step(view, movement, count, extend):
        if movement != Gtk.MovementStep.WORDS or not count:
            return
        buf = view.get_buffer()
        tag = buf.get_tag_table().lookup("hidden")
        hidden = (lambda i: buf.get_iter_at_offset(i).has_tag(tag)) if tag is not None else (lambda _i: False)
        text = buf.get_slice(buf.get_start_iter(), buf.get_end_iter(), True)
        at = buf.get_iter_at_mark(buf.get_insert()).get_offset()
        for _ in range(abs(count)):
            at = word_stop(text, at, count > 0, hidden)
        where = buf.get_iter_at_offset(at)
        if extend:
            buf.move_mark(buf.get_insert(), where)
        else:
            buf.place_cursor(where)
        view.reset_im_context()
        view.scroll_mark_onscreen(buf.get_insert())
        view.stop_emission_by_name("move-cursor")  # (GTK's: Pango's words, to the next line's past these)
    view.connect("move-cursor", step)


_LIST_ITEM = re.compile(r"(\s*)([-*+•◦]|\d+[.)])\s")


def swap_list_item(lines, at, by):
    """The list item the line at is in, with all under it (its sub-items, the lines going on with it), swapped with its
    sibling above (by -1) or below (by 1): the nearest item that way at its level, with nothing shallower, no blank line
    and no other text between, so it never leaves its list. Numbers stay in order, the items taking each other's.
    (the new lines, the first and past the last line replaced, where the line at is now), or None: not in an item, or
    no sibling that way. In a code block it's not an item, whatever it looks like."""
    fenced = sum(bool(re.match(r"\s*(```|~~~)", line)) for line in lines[:at + 1]) % 2 == 1

    def indent(i):
        return len(_LIST_ITEM.match(lines[i]).group(1).expandtabs(4))

    def goes_on(i):  # (a line going on with the item above: spaces first, and not an item itself)
        return lines[i].startswith((" ", "\t")) and bool(lines[i].strip()) and not _LIST_ITEM.match(lines[i])

    def past(i):
        end = i + 1
        while end < len(lines) and (goes_on(end) or _LIST_ITEM.match(lines[end]) and indent(end) > indent(i)):
            end += 1
        return end
    start = at
    while start >= 0 and goes_on(start):
        start -= 1
    if fenced or start < 0 or not _LIST_ITEM.match(lines[start]):
        return None
    end = past(start)
    if by > 0:
        if end >= len(lines) or not _LIST_ITEM.match(lines[end]) or indent(end) != indent(start):
            return None
        one, two, three = start, end, past(end)
    else:
        above = start - 1
        while above >= 0 and (goes_on(above) or _LIST_ITEM.match(lines[above]) and indent(above) > indent(start)):
            above -= 1
        if above < 0 or not _LIST_ITEM.match(lines[above]) or indent(above) != indent(start):
            return None
        one, two, three = above, start, end
    first, second = lines[one:two], lines[two:three]
    n1, n2 = _LIST_ITEM.match(first[0]).group(2), _LIST_ITEM.match(second[0]).group(2)
    if n1[0].isdigit() and n2[0].isdigit():  # (each takes the other's number: they stay in order)
        i1, i2 = len(_LIST_ITEM.match(first[0]).group(1)), len(_LIST_ITEM.match(second[0]).group(1))
        first[0], second[0] = first[0][:i1] + n2 + first[0][i1 + len(n1):], second[0][:i2] + n1 + second[0][i2 + len(n2):]
    moved = at - start + (one if by < 0 else one + len(second))
    return second + first, one, three, moved


class ComposerView(Gtk.TextView):
    """The input TextView. Code blocks get one rounded background drawn behind the text:
    GTK doesn't paint a paragraph background on empty lines, so per-line backgrounds made
    a new, empty code line flash unshaded until its first character was typed."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.code_blocks = []  # (first line, last line) of each code block, fences included
        self.block_rgba = Gdk.RGBA()

    def do_snapshot_layer(self, layer, snapshot):
        if layer != Gtk.TextViewLayer.BELOW_TEXT or not self.code_blocks:
            return
        buf = self.get_buffer()
        visible = self.get_visible_rect()
        x = self.get_left_margin() + 2
        width = max(0, visible.x + visible.width - self.get_right_margin() + 8 - x)
        for first, last in self.code_blocks:
            y0, _ = self.get_line_yrange(buf.get_iter_at_line(first)[1])
            y1, h1 = self.get_line_yrange(buf.get_iter_at_line(last)[1])
            rect = Graphene.Rect().init(x, y0, width, y1 + h1 - y0)
            rounded = Gsk.RoundedRect()
            rounded.init_from_rect(rect, 6)  # in place: the returned object is not usable from Python
            snapshot.push_rounded_clip(rounded)
            snapshot.append_color(self.block_rgba, rect)
            snapshot.pop()


class ComposerMarkdown:
    """Live preview for the input TextView, styled like messages in the chat: real bullets,
    headings at the chat's sizes, bold/italic/inline code, shaded code blocks. Markdown
    symbols (#, **, `) are hidden except on the line being edited. Bullets are stored as
    "•" while editing and turned back into "-" by to_markdown() when the message is sent.
    Also continues lists on Enter."""

    def __init__(self, view):
        self.view, self.buf = view, view.get_buffer()
        t = self.buf.create_tag
        left = view.get_left_margin()
        t("code", family="monospace", scale=code_scale(), left_margin=left + 8)
        t("fence", family="monospace", scale=code_scale(), left_margin=left + 8)
        t("inline", family="monospace", scale=code_scale())
        t("bold", weight=Pango.Weight.BOLD)
        t("italic", style=Pango.Style.ITALIC)
        for level, scale in HEADING_SCALE.items():
            t(f"h{level}", weight=Pango.Weight.BOLD, scale=scale)
        t("h3", weight=Pango.Weight.BOLD)
        t("marker", weight=Pango.Weight.BOLD)
        t("syntax")
        t("hidden", invisible=True)
        for depth in range(6):  # hanging indent so wrapped lines align with the item text
            t(f"item{depth}", left_margin=left + 6 + INDENT_STEP * depth + 18, indent=-18)
        self.pending, self.cursor_line, self.styling = False, -1, False
        # style synchronously so a new line is drawn styled in the same frame it appears
        # (an idle restyle let it flash unstyled first); bullet conversion edits text, so
        # it stays in an idle callback
        self.buf.connect_after("changed", lambda *_: (self.restyle_tags(), self.queue()))
        self.buf.connect("notify::cursor-position", self.on_cursor)

    def colours(self):
        """Tag colours follow the view's text colour, so they work in light and dark themes."""
        fg = self.view.get_color()
        def tone(alpha):
            c = fg.copy()
            c.alpha = alpha
            return c
        tags = self.buf.get_tag_table()
        if isinstance(self.view, ComposerView):
            self.view.block_rgba = tone(0.07)  # same shade as the chat's code blocks
        tags.lookup("fence").set_property("foreground-rgba", tone(0.45))
        tags.lookup("inline").set_property("background-rgba", tone(0.10))
        tags.lookup("marker").set_property("foreground-rgba", tone(0.7))
        tags.lookup("syntax").set_property("foreground-rgba", tone(0.4))

    def on_cursor(self, *_):
        line = self.buf.get_iter_at_mark(self.buf.get_insert()).get_line()
        if line != self.cursor_line:  # symbols show/hide as the cursor changes lines
            self.restyle_tags()

    def queue(self, *_):
        if not self.pending:
            self.pending = True
            GLib.idle_add(self.restyle)

    def _tag(self, name, line, start, end):
        it_s = self.buf.get_iter_at_line_offset(line, start)[1]
        it_e = self.buf.get_iter_at_line_offset(line, end)[1]
        self.buf.apply_tag_by_name(name, it_s, it_e)

    def _tag_line(self, name, line):
        """Tag a whole line including its line break: paragraph styling (margins, background)
        comes from the tags on a line's first character, which for an empty line is the break."""
        it_s = self.buf.get_iter_at_line(line)[1]
        it_e = it_s.copy()
        it_e.forward_line()
        self.buf.apply_tag_by_name(name, it_s, it_e)

    def _spans(self, text):
        return fence_spans(text.split("\n"), tildes=False,
                           typing=(self.buf.get_iter_at_mark(self.buf.get_insert()).get_line(),))

    def _block_here(self):
        """The code block the caret's in (on an opening fence, or inside): (first line, last line, closed), else None."""
        buf = self.buf
        cur_line = buf.get_iter_at_mark(buf.get_insert()).get_line()
        for first, last, closed in self._spans(buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)):
            if first <= cur_line and (cur_line < last if closed else cur_line <= last):
                return first, last, closed
        return None

    def code_state(self):
        """(cursor is on an opening fence or inside a code block, that block is closed)"""
        block = self._block_here()
        return block is not None, bool(block and block[2])

    def newline(self):
        """Enter's handling (a box's, not sending). In a code block: a plain new line, and an unclosed block
        gets its closing fence, so the new line is never the (unstylable) last line.
        On a list item: the next item. Returns False to let the TextView insert a newline."""
        block = self._block_here()
        if block is not None:
            buf = self.buf
            buf.begin_user_action()
            buf.insert_at_cursor("\n")
            if not block[2]:  # (its closing fence where its lines end: not after what's below them)
                mark = buf.create_mark(None, buf.get_iter_at_mark(buf.get_insert()), True)
                end = buf.get_iter_at_line(block[1] + 1)[1]
                if not end.ends_line():
                    end.forward_to_line_end()
                buf.insert(end, "\n```")
                buf.place_cursor(buf.get_iter_at_mark(mark))
                buf.delete_mark(mark)
            buf.end_user_action()
            return True
        return self.continue_list()

    def move_item(self, by):
        """Alt and Up or Down: the list item the caret's in moved past its sibling that way (swap_list_item). False if
        it's not in one."""
        buf = self.buf
        ins = buf.get_iter_at_mark(buf.get_insert())
        lines = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True).split("\n")
        line, column = ins.get_line(), ins.get_line_offset()
        at = line
        while at >= 0 and lines[at].startswith((" ", "\t")) and lines[at].strip() and not _LIST_ITEM.match(lines[at]):
            at -= 1
        if at < 0 or not _LIST_ITEM.match(lines[at]):
            return False
        swapped = swap_list_item(lines, line, by)
        if swapped is None:
            return True  # (no sibling that way: nothing to do, and the key's still this)
        new, one, three, moved = swapped
        buf.begin_user_action()
        start = buf.get_iter_at_line(one)[1]
        end = buf.get_iter_at_line(three)[1] if three < len(lines) else buf.get_end_iter()
        buf.delete(start, end)
        buf.insert(start, "\n".join(new) + ("\n" if three < len(lines) else ""))
        buf.place_cursor(buf.get_iter_at_line_offset(moved, column)[1])
        buf.end_user_action()
        return True

    def _lines(self, text):
        """(line number, line, inside a code fence?, is a fence line?)"""
        kinds = {}
        for first, last, closed in self._spans(text):
            kinds.update((n, (True, False)) for n in range(first + 1, last + 1))
            kinds[first] = False, True
            if closed:
                kinds[last] = True, True
        for n, line in enumerate(text.split("\n")):
            yield (n, line, *kinds.get(n, (False, False)))

    def make_bullets(self, text):
        """'- ', '* ' or '+ ' starting a line becomes a real bullet: • at the top level, ◦ one
        level in (re-picked when a line is indented or outdented). Returns True if it edited."""
        edits = []
        for n, line, code, fence in self._lines(text):
            m = None if code or fence else re.match(r"^(\s*)([-*+•◦]) ", line)
            if m and m.group(2) != (want := bullet_for(len(m.group(1).expandtabs(4)) // 2)):
                edits.append((n, len(m.group(1)), want))
        if not edits:
            return False
        self.buf.begin_user_action()
        for n, col, glyph in edits:
            start = self.buf.get_iter_at_line_offset(n, col)[1]
            end = start.copy()
            end.forward_char()
            self.buf.delete(start, end)
            self.buf.insert(self.buf.get_iter_at_line_offset(n, col)[1], glyph)
        self.buf.end_user_action()
        return True

    @staticmethod
    def to_markdown(text):
        """Editor text -> markdown to send: bullets go back to '-' (code blocks untouched)."""
        lines = text.split("\n")
        code = {n for first, last, _closed in fence_spans(lines, tildes=False) for n in range(first, last + 1)}
        return "\n".join(line if n in code else re.sub(rf"^(\s*)[{BULLET}{SUB_BULLET}](?= )", r"\1-", line)
                         for n, line in enumerate(lines))

    def restyle(self):
        self.pending = False
        buf = self.buf
        self.make_bullets(buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True))
        return False

    def restyle_tags(self):
        if self.styling:  # applying tags can re-enter through notify::cursor-position
            return
        self.styling = True
        try:
            self._restyle_tags()
        finally:
            self.styling = False

    def _restyle_tags(self):
        buf = self.buf
        self.colours()
        buf.remove_all_tags(buf.get_start_iter(), buf.get_end_iter())
        self.cursor_line = buf.get_iter_at_mark(buf.get_insert()).get_line()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        blocks = [(first, last) for first, last, _closed in self._spans(text)]
        if isinstance(self.view, ComposerView):
            self.view.code_blocks = blocks
            self.view.queue_draw()
        for n, line, in_code, fence in self._lines(text):
            hide = "syntax" if n == self.cursor_line else "hidden"
            if fence or in_code:
                self._tag_line("fence" if fence else "code", n)
                continue
            if h := re.match(r"^(#{1,6})\s+", line):
                self._tag_line(f"h{min(len(h.group(1)), 3)}", n)
                self._tag(hide, n, 0, h.end())
            elif m := _ITEM.match(line):
                depth = min(5, len(m.group(1).expandtabs(4)) // 2)
                self._tag_line(f"item{depth}", n)
                self._tag("marker", n, m.start(2), m.end(2))
            for rx, tag, mark in ((r"`[^`\n]+`", "inline", 1), (r"\*\*[^*\n]+\*\*", "bold", 2),
                                  (r"(?<![\w*])\*(?!\s)[^*\n]+(?<!\s)\*(?![\w*])", "italic", 1)):
                for m in re.finditer(rx, line):
                    self._tag(tag, n, m.start(), m.end())
                    self._tag(hide, n, m.start(), m.start() + mark)
                    self._tag(hide, n, m.end() - mark, m.end())
        return False

    def indent(self, step):
        """Tab (step 1) or Shift+Tab (-1) on the lines the caret or the selection is on: a list item in or out a level
        (a bullet two spaces, its glyph the new level's; a number its marker's width), a line inside a code block four
        spaces. Fence lines and prose are left as they are. True if any such line was there."""
        buf = self.buf
        bounds = buf.get_selection_bounds() or (buf.get_iter_at_mark(buf.get_insert()),) * 2
        first, last = bounds[0].get_line(), bounds[1].get_line()
        if last > first and bounds[1].starts_line():
            last -= 1  # (a selection ending at a line's start doesn't take that line)
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        moves = []  # (line, its leading whitespace's length, unit, the list item's match or None)
        for n, line, code, fence in self._lines(text):
            if first <= n <= last and not fence:
                m = None if code else _LIST_ITEM.match(line)
                if code or m:
                    lead = len(line) - len(line.lstrip(" \t"))
                    unit = 4 if m is None else 2 if m.group(2) in "-*+•◦" else len(m.group(2)) + 1
                    moves.append((n, lead, unit, m))
        if not moves:
            return False
        buf.begin_user_action()
        for n, lead, unit, m in moves:
            width = len(text.split("\n")[n][:lead].expandtabs(4))
            new = width + unit if step > 0 else max(0, width - unit)
            if new == width:
                continue
            start = buf.get_iter_at_line(n)[1]
            end = start.copy()
            end.forward_chars(lead)
            buf.delete(start, end)  # (its indent made again, in spaces: a tab's counted as four)
            buf.insert(buf.get_iter_at_line(n)[1], " " * new)
            if m is not None and m.group(2) in "•◦":  # (the bullet the level's: • ◦ • ...)
                at = buf.get_iter_at_line_offset(n, new)[1]
                to = at.copy()
                to.forward_char()
                buf.delete(at, to)
                buf.insert(buf.get_iter_at_line_offset(n, new)[1], bullet_for(new // 2))
        buf.end_user_action()
        return True

    def continue_list(self):
        """Enter on a list item starts the next item (or ends the list if this one is
        empty). Returns False when the cursor isn't on a list item."""
        buf = self.buf
        cur = buf.get_iter_at_mark(buf.get_insert())
        start = cur.copy()
        start.set_line_offset(0)
        m = _ITEM.match(buf.get_text(start, cur, True))
        if not m:
            return False
        if not m.group(3).strip():  # empty item: drop the marker instead
            buf.delete(start, cur)
            return True
        marker = m.group(2)
        if marker[0].isdigit():
            marker = f"{int(marker[:-1]) + 1}{marker[-1]}"
        elif marker in "-*+":
            marker = bullet_for(len(m.group(1).expandtabs(4)) // 2)
        buf.insert_at_cursor(f"\n{m.group(1)}{marker} ")
        return True


SEND_KEY = wording.say("composer.send_key_mac" if sys.platform == "darwin" else "composer.send_key")  # (what sends from a box: Enter is a new line)


def sends(state):
    """Whether Enter with these modifiers sends what's written: Ctrl+Enter, or ⌘Enter on a Mac too. Plain Enter is a
    new line (a list's next item); Shift+Enter a plain line break (newline_key)."""
    return bool(state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK)) if sys.platform == "darwin" \
        else bool(state & Gdk.ModifierType.CONTROL_MASK)


def tab_key(markdown, keyval, state):
    """Tab or Shift+Tab in a box (ComposerMarkdown), as on a Record page: list items in or out a level, a code block's
    lines in or out four spaces. True if it did; elsewhere Tab is left to move the focus."""
    if keyval not in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab, Gdk.KEY_KP_Tab) or \
            state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK | Gdk.ModifierType.ALT_MASK):
        return False
    return markdown.indent(-1 if keyval == Gdk.KEY_ISO_Left_Tab or state & Gdk.ModifierType.SHIFT_MASK else 1)


def newline_key(markdown, state):
    """Enter not sending, in a box (ComposerMarkdown): Shift+Enter a plain line break (False: the TextView's own),
    inside a list item too, as Record's pages have it; Enter a new line, a list's next item."""
    return False if state & Gdk.ModifierType.SHIFT_MASK else markdown.newline()


class Composer(Gtk.Overlay):
    """The box a message is written in, Baude's and Record's alike: its text (ComposerView, its markdown shown as it's
    written: ComposerMarkdown) in a card, wrapped close round its buttons while it's a line (schema/tokens' composer:
    a button's height, its inset above and below), growing with the text and scrolling past nine lines, with its
    buttons in its bottom right corner, the send button last; the text's right margin keeps it from running under
    them. buttons: any to go before the send button (Baude's attach). head: what's over the text, in the card, the
    card as tall again as it needs (Record's quote). Its owner connects the send button and the keys."""

    BUTTON, INSET = tokens.px("composer.button"), tokens.px("composer.inset")
    LEAST = BUTTON + 2 * INSET  # (a line's: the buttons and their inset)
    PAD = 10  # (the text's margins: a line of the chat's text, 16px, centred on the buttons')

    def __init__(self, buttons=(), send_tip=wording.say("composer.send", key=SEND_KEY), follow_text=False):
        across = (self.BUTTON + 4) * (len(buttons) + 1) - 4  # (the buttons, 4 apart: actions' spacing)
        self.input = ComposerView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=self.PAD, bottom_margin=self.PAD,
                                  left_margin=self.PAD, right_margin=self.INSET + across + self.PAD, accepts_tab=False)
        self.input.add_css_class("zoomable")
        no_text_drag(self.input)  # (a selection isn't dragged away: a press in it places the caret)
        word_steps(self.input)
        self.markdown = ComposerMarkdown(self.input)
        # (no scrollbar till the text's past the box's most: a shown one's least length would keep it two lines tall)
        scroll = Gtk.ScrolledWindow(min_content_height=self.LEAST, max_content_height=220, propagate_natural_height=True,
                                    hexpand=True, child=self.input, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    vscrollbar_policy=Gtk.PolicyType.EXTERNAL)
        adj = scroll.get_vadjustment()
        adj.connect("changed", lambda *_: self._scrollbar_soon())
        self.scroll, self.scrollbar_soon = scroll, None
        self.head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.head.add_css_class("composer-head")
        click = Gtk.GestureClick()  # (a click there puts the caret in the text, as one in the card's margins does)
        click.connect("released", lambda *_: self.input.grab_focus())
        self.head.add_controller(click)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.head)
        body.append(scroll)
        super().__init__(child=body, hexpand=True)
        self.send, self.send_tip = Gtk.Button(), send_tip
        actions = Gtk.Box(spacing=4, halign=Gtk.Align.END, valign=Gtk.Align.END, margin_end=self.INSET,
                          margin_bottom=self.INSET)
        actions.add_css_class("composer-actions")
        for button in buttons:
            actions.append(button)
        actions.append(self.send)
        self.add_overlay(actions)
        if follow_text:  # (a box taller than its text: the buttons level with its last line, at most at the bottom)
            self.actions = actions
            self.connect("get-child-position", self._place_actions)
            self.input.get_buffer().connect("changed", lambda *_: self.queue_allocate())
            adj.connect("value-changed", lambda *_: self.queue_allocate())
        self.add_css_class("card")
        self.add_css_class("composer-box")
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.gates, self.busy = set(), False  # (what keeps what's written from being sent now: gate)
        self.set_busy(False)

    def _scrollbar_soon(self):
        """The scrollbar shown once the text's past the box's most, hidden again under it: after this layout (it's
        laid out again, its least height changed), not in it."""
        if self.scrollbar_soon is None:
            self.scrollbar_soon = GLib.idle_add(self._scrollbar)

    def _scrollbar(self):
        self.scrollbar_soon = None
        adj = self.scroll.get_vadjustment()
        want = Gtk.PolicyType.AUTOMATIC if adj.get_upper() > adj.get_page_size() + 0.5 else Gtk.PolicyType.EXTERNAL
        if self.scroll.get_policy()[1] != want:
            self.scroll.set_policy(Gtk.PolicyType.NEVER, want)
        return GLib.SOURCE_REMOVE

    def _place_actions(self, _overlay, child, rect):
        """follow_text's buttons: in the bottom right, but no lower than centred on the text's last line."""
        if child is not getattr(self, "actions", None):
            return False
        nat = child.get_preferred_size()[1]  # (its margins in it: the buttons are its top part)
        rect.width, rect.height = nat.width, nat.height
        rect.x, rect.y = self.get_width() - nat.width, self.get_height() - nat.height
        line = self.input.get_iter_location(self.input.get_buffer().get_end_iter())
        wy = self.input.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, 0, line.y + line.height // 2)[1]
        ok, at = self.input.compute_point(self, Graphene.Point().init(0, wy))
        if ok:
            buttons = nat.height - child.get_margin_top() - child.get_margin_bottom()
            rect.y = max(child.get_margin_end() - child.get_margin_top(),
                         min(rect.y, round(at.y - buttons / 2 - child.get_margin_top())))
        return True

    def gate(self, name, on):
        """A reason what's written can't be sent now (the context exhausted, a host's connection dropped or gone),
        on or off: the send button's off while there's any, unless it's Stop (busy). The text box stays as it is."""
        (self.gates.add if on else self.gates.discard)(name)
        self.send.set_sensitive(self.busy or not self.gates)

    def set_busy(self, busy):
        """The send button: grey, to send; red, to stop, while the reply's coming."""
        self.busy = busy
        self.send.set_sensitive(busy or not self.gates)
        if busy:
            self.send.set_icon_name("media-playback-stop-symbolic")
            self.send.set_tooltip_text(wording.say("composer.stop"))
            self.send.add_css_class("destructive-action")
        else:
            ensure_icons()
            self.send.set_icon_name("baude-return-symbolic")  # an icon, not a text glyph
            self.send.set_tooltip_text(self.send_tip)
            self.send.remove_css_class("destructive-action")

    def text(self):
        """What's written, as markdown to send (ComposerMarkdown.to_markdown: its bullets back to -)."""
        buf = self.input.get_buffer()
        return ComposerMarkdown.to_markdown(buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True))


START_PROMPT_HEIGHT = 260  # (past this, the prompt a session was started with scrolls in its own box)


def start_prompt_view(text, above=None):
    """The prompt a session was started with from outside, at the top of its chat (Baude's and Record's): a dim
    heading, the prompt as markdown (under above, its images, if any) in a box that scrolls once it's long, and a rule
    under it."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    head = Gtk.Label(label=wording.say("chat_turns.started_with_prompt"), xalign=0)
    head.add_css_class("dim-label")
    head.add_css_class("caption-heading")
    box.append(head)
    inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    if above is not None:
        inner.append(above)
    inner.append(MarkdownView(text))
    scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, max_content_height=START_PROMPT_HEIGHT,
                                propagate_natural_height=True, child=inner)
    pass_edge_scroll(scroll)
    box.append(scroll)
    box.append(Gtk.Separator(margin_top=6, margin_bottom=6))
    return box


def pass_edge_scroll(scroll):
    """A scrolling box inside the chat's that doesn't keep the wheel at its ends: scrolled up at its top (or down at
    its bottom, or when it has nothing to scroll), the chat scrolls instead."""
    ctl = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL,
                                    propagation_phase=Gtk.PropagationPhase.CAPTURE)

    def on_scroll(ctl, _dx, dy):
        adj = scroll.get_vadjustment()
        value, top, end = adj.get_value(), adj.get_lower(), adj.get_upper() - adj.get_page_size()
        if not (dy < 0 and value <= top or dy > 0 and value >= end - 0.5 or end <= top):
            return False  # (its own to scroll)
        outer = scroll.get_parent()
        while outer is not None and not isinstance(outer, Gtk.ScrolledWindow):
            outer = outer.get_parent()
        if outer is None:
            return False
        oadj = outer.get_vadjustment()
        step = dy if ctl.get_unit() == Gdk.ScrollUnit.SURFACE else dy * oadj.get_page_size() ** (2 / 3)
        oadj.set_value(min(max(oadj.get_value() + step, oadj.get_lower()), oadj.get_upper() - oadj.get_page_size()))
        return True

    ctl.connect("scroll", on_scroll)
    scroll.add_controller(ctl)
