"""A chat's transcript followed as it's written, as Baude's chat and Record's Read both show it: Tail reads what's
added the moment it's there, and FollowEnd keeps the newest of it in view. What's done with what's read is each
window's own (Baude's tool cards, banner and sidebar dot; Record's prose)."""
import os
import re
import time

from gi.repository import Gio, GLib

LIVE_SECONDS = 60  # a transcript written to this recently is probably open somewhere else
TURN_OPEN_SECONDS = 600  # mid-turn yet silent this long: whatever ran it has probably stopped
_TURN_END = re.compile(r'"stop_reason":\s*"(?:end_turn|stop_sequence|max_tokens|refusal)"')
_TURN_GOES_ON = re.compile(r'"stop_reason":\s*"(?:tool_use|pause_turn)"')


def read_appended(path, offset, partial=b""):
    """Lines added to a transcript since offset: (lines, new offset, unfinished last line)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size < offset:  # rewritten from scratch: start over
                return [], size, b""
            f.seek(offset)
            data = partial + f.read()
    except OSError:
        return [], offset, partial
    *done, rest = data.split(b"\n")
    return [l.decode("utf-8", "replace") for l in done if l.strip()], size, rest


def turn_open_in(lines):
    """Whether transcript lines end inside a turn (Claude still working): True after a prompt,
    a tool result or an assistant message that stopped for a tool; False after one that ended
    the turn; None if they hold no conversation. Subagents' lines don't count."""
    for line in reversed(lines):
        if '"isSidechain":true' in line or '"isSidechain": true' in line:
            continue
        if '"type":"assistant"' in line or '"type": "assistant"' in line:
            if _TURN_END.search(line):
                return False
            return True if _TURN_GOES_ON.search(line) else None  # no stop_reason: can't tell
        if '"type":"user"' in line or '"type": "user"' in line:
            return True
    return None


def transcript_turn_open(path, tail=256 * 1024):
    """turn_open_in for the end of a transcript file (without reading all of it)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - tail))
            data = f.read()
    except OSError:
        return None
    lines = data.decode("utf-8", "replace").split("\n")
    return turn_open_in(lines[1:] if size > tail else lines)


def external_state(quiet, turn_open):
    """The state of a session another program writes, from how long its file has been quiet
    and whether it ends mid-turn: a long tool call can be silent for minutes and still be working."""
    if turn_open and quiet < TURN_OPEN_SECONDS or turn_open is None and quiet < 20:
        return "working"
    if turn_open is False and quiet < LIVE_SECONDS:
        return "done"
    return "inactive"


def file_monitor(path):
    """path's file events (inotify, kqueue), each as it happens; None where there are none (some network filesystems)."""
    try:
        mon = Gio.File.new_for_path(path).monitor_file(Gio.FileMonitorFlags.NONE, None)
    except GLib.Error:
        return None
    mon.set_rate_limit(0)  # GIO batches a file's changes into one event per 800 ms by default
    return mon


class Tail:
    """A transcript followed the way `tail -f` does: transcripts are only ever appended to, so each file event reads
    just the new bytes, and nothing polls (with no file events, a read a second). on_lines(lines) has what each read
    found, whole lines only. on_rewritten(), if given, says the file got shorter: it's read again from its start
    (without it, followed from its new end). last_written and turn_open say what's writing it is up to."""

    def __init__(self, on_lines, on_rewritten=None):
        self.on_lines, self.on_rewritten = on_lines, on_rewritten
        self.path, self.offset, self.partial = None, 0, b""
        self.monitor = self.idle = self.ticking = None
        self.last_written, self.turn_open = 0.0, None

    def start(self, path, offset=None):
        """path followed from offset (none: from its end). Nothing's read yet: read() for what's there."""
        self.stop()
        try:
            st = os.stat(path)
        except OSError:
            st = None
        self.path, self.partial = path, b""
        self.offset = offset if offset is not None else st.st_size if st else 0
        self.last_written = st.st_mtime if st else 0.0
        self.turn_open = transcript_turn_open(path)
        self.monitor = file_monitor(path)
        if self.monitor is None:
            self.ticking = GLib.timeout_add(1000, self._tick)
        else:
            self.monitor.connect("changed", self._changed)

    def stop(self):
        """Not followed any more: no file events, nothing waiting to read it."""
        self.path = None
        if self.monitor is not None:
            self.monitor.cancel()
            self.monitor = None
        for attr in ("idle", "ticking"):
            if getattr(self, attr) is not None:
                GLib.source_remove(getattr(self, attr))
                setattr(self, attr, None)

    def _changed(self, _mon, _file, _other, event):
        if event in (Gio.FileMonitorEvent.CHANGED, Gio.FileMonitorEvent.CHANGES_DONE_HINT,
                     Gio.FileMonitorEvent.CREATED) and self.idle is None:
            # one read for however many writes land before the next frame
            self.idle = GLib.idle_add(self._read_soon, priority=GLib.PRIORITY_HIGH_IDLE)

    def _read_soon(self):
        self.idle = None
        self.read()
        return False

    def _tick(self):
        self.read()
        return True

    def read(self, new=True):
        """What's been added since the last read. new: just written, not what was there when it was opened."""
        if self.path is None:
            return
        if self.on_rewritten is not None:
            try:
                rewritten = os.path.getsize(self.path) < self.offset
            except OSError:
                return
            if rewritten:
                self.offset, self.partial = 0, b""
                self.on_rewritten()
        lines, self.offset, self.partial = read_appended(self.path, self.offset, self.partial)
        if lines:
            if new:
                self.last_written = time.time()
            if (turn := turn_open_in(lines)) is not None:
                self.turn_open = turn
            self.on_lines(lines)

    def state(self):
        """working / done / inactive: what the program writing it is doing (external_state)."""
        return external_state(time.time() - self.last_written, self.turn_open)

    def in_use(self):
        """Is it in use by another program (Claude Desktop, a terminal)? Written in the last minute, or mid-turn: a long
        tool call can be silent for minutes."""
        quiet = time.time() - self.last_written
        return quiet < LIVE_SECONDS or bool(self.turn_open) and quiet < TURN_OPEN_SECONDS


class FollowEnd:
    """A scrolled window kept at its end as what's in it grows, once it's been read all the way down (being near isn't
    enough), and left alone once it's scrolled up. stick says which. Till it's scrolled by hand after being put back
    where it was read to (widgets.hold_place: scroller.held), it's left there."""

    GLIDE_US = 200_000  # a big arrival (a tool result while tailing) eases in over this long

    def __init__(self, scroller):
        self.scroller = scroller
        self.stick, self.glide = True, None  # (glide: (from, start µs, tick id) while easing down to new content)
        self.auto, self.last_set = False, 0.0
        self.clock = self.layout = None
        scroller.get_vadjustment().connect("value-changed", self.scrolled)

    def attach(self):
        """laid_out right after each layout pass of the scroller's (a window with more to do then calls it itself)."""
        self.scroller.connect("realize", self._realized)
        self.scroller.connect("unrealize", lambda *_: self.detach())

    def _realized(self, *_):
        self.detach()
        self.clock = self.scroller.get_frame_clock()
        self.layout = self.clock.connect_after("layout", self.laid_out)

    def detach(self):
        """No more layout passes followed, and no glide left running."""
        if self.layout is not None:
            self.clock.disconnect(self.layout)
            self.clock = self.layout = None
        self.stop_glide()

    def scrolled(self, adj):
        if self.auto:
            return
        if self.glide is not None:
            if adj.get_value() >= self.last_set - 1:
                return  # not the reader: GTK settling while the view eases down
            self.stop_glide()  # the reader scrolled up mid-glide: let them
        # follow new content only once the reader is all the way down (being near isn't enough)
        self.stick = adj.get_value() + adj.get_page_size() >= adj.get_upper() - 1

    def put(self, value):
        self.auto, self.last_set = True, value
        try:
            self.scroller.get_vadjustment().set_value(value)
        finally:
            self.auto = False

    def to_end(self):
        adj = self.scroller.get_vadjustment()
        self.put(adj.get_upper() - adj.get_page_size())

    def laid_out(self, *_):
        """Keep the newest content in view, right after each layout pass. (Set from notify::upper,
        which fires while the scroller itself is being laid out, the new position never reached
        the screen: GTK drops a relayout asked for mid-layout, so the view sat a step behind its
        content and the line being streamed stayed cut off at the bottom.) Small growth, like
        streamed text, stays pinned in this same frame; a big arrival glides in."""
        adj = self.scroller.get_vadjustment()
        page = adj.get_page_size()
        if getattr(self.scroller, "held", False):  # (where it's put, it's been read to)
            self.stick = adj.get_value() + page >= adj.get_upper() - 1
            return
        if not self.stick or self.glide is not None:
            return
        gap = adj.get_upper() - page - adj.get_value()
        if gap <= 0.5:
            return
        if gap <= page / 3 or gap > page * 4:  # a line or two: pin; a whole session: just go there
            self.to_end()
            return
        start = self.scroller.get_frame_clock().get_frame_time()
        self.glide = (adj.get_value(), start, self.scroller.add_tick_callback(self.glide_step))

    def glide_step(self, _widget, clock):
        if self.glide is None:
            return False
        origin, start, _tick = self.glide
        adj = self.scroller.get_vadjustment()
        target = adj.get_upper() - adj.get_page_size()  # it may grow again while gliding
        t = min(1.0, (clock.get_frame_time() - start) / self.GLIDE_US)
        self.put(origin + (target - origin) * (1 - (1 - t) ** 3))  # ease-out
        if t >= 1.0:
            self.glide = None
            return False
        return True

    def stop_glide(self):
        if self.glide is not None:
            self.scroller.remove_tick_callback(self.glide[2])
            self.glide = None
