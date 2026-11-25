"""clearrelay: a program run in a pty of its own inside a terminal's pty (VTE's, or the host process's for its
terminals), its bytes copied both ways, and its clears of the screen (Ctrl+L's and clear's: ESC[H ESC[2J, either way
round) done on their way out as iTerm2 does them. VTE's own clear puts a whole screen of rows in the scrollback each
time, the blank ones too. Done here instead: the cursor sent to the lowest row anything's been put on since the last
clear, a newline for each of the screen's rows (the first ones take it to the bottom, the rest scroll every row down
to it off the top, into the scrollback), then the cursor sent home and the screen erased. Left as they are on the
alternate screen (a full-screen program's, which has no scrollback) and while a scroll region's set.

Standalone (nothing of Baude's imported): run as `python -I -S clearrelay.py -- argv` (relayed(argv) says that). It
ends as its program does: the same exit status, or the same signal."""
import fcntl
import os
import pty
import re
import select
import signal
import struct
import sys
import termios
import tty

IUTF8 = getattr(termios, "IUTF8", 0x4000)
HOLD_S = 0.05  # (the start of a sequence, held for the rest of it: sent as it is if no more comes this soon)
HOLD_MOST = 4096  # (an unended sequence longer than this isn't held)
BUFFER = 1 << 20  # (bytes waiting either way before no more are read)
CLEAR = rb"\x1b\[(?:1;1)?H\x1b\[2J|\x1b\[2J\x1b\[(?:1;1)?H"
TOKEN = re.compile(rb"(?P<clear>" + CLEAR + rb")|\x1b\[(?P<p>[<=>?]?)(?P<n>[0-9;:]*)[ -/]*(?P<f>[@-~])"
                   rb"|\x1b[\]P_^X][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b(?![\[\]P_^X])[ -/]*[0-~]")
UNENDED = re.compile(rb"\x1b(?:\[[<=>?]?[0-9;:]*[ -/]*|[\]P_^X][^\x07\x1b]*|[ -/]*)")
HALVES = (b"\x1b[H", b"\x1b[1;1H", b"\x1b[2J")  # (what may be a clear's first half)
HALF = re.compile(b"|".join(re.escape(h) for h in HALVES))
LINE_ENDS = re.compile(rb"[\n\r\x0b\x0c]")
NOT_SHOWN = bytes(range(0x20)) + b"\x7f" + bytes(range(0x80, 0xc0))  # (controls, and UTF-8's continuing bytes)
ALT = {b"47", b"1047", b"1049"}
CORE = {signal.SIGQUIT, signal.SIGILL, signal.SIGTRAP, signal.SIGABRT, signal.SIGBUS, signal.SIGFPE, signal.SIGSEGV,
        signal.SIGSYS}  # (not raised again here: a crash report for Python, not the program)


def relayed(argv, path=None):
    """argv run through this relay. As it is when its program isn't found on path (starting it fails as it did)."""
    import shutil
    if not argv or not sys.executable or shutil.which(argv[0], path=path) is None:
        return list(argv)
    return [sys.executable, "-I", "-S", os.path.abspath(__file__), "--", *argv]


def width(text):
    return len(text.translate(None, NOT_SHOWN))


class Clears:
    """What a program writes, its clears done as above. Where the cursor is, and the lowest row anything's been put on
    (low), are followed well enough for that: rows written, wrapped and moved to; erased, inserted and scrolled."""

    def __init__(self, rows, columns):
        self.held, self.alt, self.region, self.saved, self.primary = b"", False, False, (0, 0), None
        self.row = self.col = self.low = 0
        self.bare = True  # (nothing's been put on row low)
        self.size(rows, columns)

    def size(self, rows, columns):
        self.rows, self.columns = max(1, rows or 24), max(1, columns or 80)
        self.long = re.compile(rb"[^\n\r\x0b\x0c]{%d,}" % (self.columns + 1))
        self.moved()

    def take(self, data, hold=True):
        """data as the terminal's to get it (a sequence that may not have ended, or a clear's first half, held)."""
        data, self.held = self.held + data, b""
        if b"\x1b" not in data:
            self.text(data)
            return data
        found = list(TOKEN.finditer(data))
        if hold:
            cut, i = len(data), data.rfind(b"\x1b")
            if (not found or found[-1].end() <= i) and len(data) - i < HOLD_MOST and UNENDED.fullmatch(data, i):
                cut = i
            if found and found[-1].end() == cut and HALF.fullmatch(found[-1][0]):
                cut = found.pop().start()
            data, self.held = data[:cut], data[cut:]
        out, at = [], 0
        for m in found:
            if m.start() > at:
                self.text(data[at:m.start()])
                out.append(data[at:m.start()])
            out.append(self.sequence(m))
            at = m.end()
        if at < len(data):
            self.text(data[at:])
            out.append(data[at:])
        return b"".join(out)

    def flush(self):
        """What's held, as it is (no more came)."""
        return self.take(b"", hold=False) if self.held else b""

    def moved(self, below=0):
        self.row, self.col = min(max(self.row, 0), self.rows - 1), max(self.col, 0)
        low = min(max(self.low + below, self.row), self.rows - 1)
        self.bare = (self.bare or low > self.low) and not below
        self.low = low

    def wraps(self, col):
        return max(0, (col - 1) // self.columns)

    def text(self, data):
        if not data:
            return
        last = max(data.rfind(b"\n"), data.rfind(b"\r"), data.rfind(b"\x0b"), data.rfind(b"\x0c"))
        if last < 0:
            shown = width(data)
            col = self.col + shown
            self.row, self.col = self.row + self.wraps(col) - self.wraps(self.col), col
            self.moved()
            self.bare = self.bare and not (shown and self.row == self.low)
            return
        first = LINE_ENDS.search(data).start()
        col = self.col + width(data[:first])
        down = self.wraps(col) - self.wraps(self.col) + data.count(b"\n") + data.count(b"\x0b") + data.count(b"\x0c")
        if last - first > self.columns:
            down += sum(self.wraps(width(m[0])) for m in self.long.finditer(data, first, last))
        self.row += down
        self.col = width(data[last + 1:])
        self.row += self.wraps(self.col)
        self.moved()
        self.bare = self.bare and not (self.row == self.low and (self.col or data[last] == 13))

    def sequence(self, m):
        said = m[0]
        if m["clear"]:
            if self.alt or self.region:
                self.row = self.col = 0
                self.low = 0
                return said
            last = self.low - self.bare  # (the lowest row with anything on it)
            self.row = self.col = self.low = 0
            self.bare = True
            return (b"\x1b[%d;1H" % (last + 1) + b"\n" * self.rows if last >= 0 else b"") + b"\x1b[H\x1b[J"
        if m["f"] == b"m":
            return said
        if m["f"] is None:
            if said == b"\x1bc":
                held, _ = self.held, self.__init__(self.rows, self.columns)
                self.held = held
            elif said == b"\x1b7":
                self.saved = (self.row, self.col)
            elif said == b"\x1b8":
                self.row, self.col = self.saved
            elif said in (b"\x1bD", b"\x1bE"):
                self.row, self.col = self.row + 1, 0 if said == b"\x1bE" else self.col
            elif said == b"\x1bM":
                if self.row == 0:
                    return self.moved(1) or said
                self.row -= 1
            self.moved()
            return said
        p, f = m["p"], m["f"]
        if p == b"?" and f in b"hl" and ALT & set(m["n"].split(b";")):
            if f == b"h" and not self.alt:
                self.alt, self.primary = True, (self.row, self.col, self.low, self.bare)
            elif f == b"l" and self.alt:
                self.alt, (self.row, self.col, self.low, self.bare) = False, self.primary
            return said
        if p:
            return said
        try:
            n = [int(x or 0) for x in m["n"].replace(b":", b";").split(b";")]
        except ValueError:
            return said
        a, b = n[0], n[1] if len(n) > 1 else 0
        one = a or 1
        below = 0
        if f in b"Hf":
            self.row, self.col = one - 1, (b or 1) - 1
        elif f == b"d":
            self.row = one - 1
        elif f in b"G`":
            self.col = one - 1
        elif f in b"A":
            self.row -= one
        elif f in b"Be":
            self.row += one
        elif f in b"EF":
            self.row, self.col = self.row + (one if f == b"E" else -one), 0
        elif f in b"Ca":
            self.col += one
        elif f == b"D":
            self.col -= one
        elif f == b"J" and a in (0, 2):
            self.low, self.bare = self.row, False
        elif f == b"L" and self.low >= self.row:
            below = one
        elif f == b"M" and self.low >= self.row:
            below = -min(one, self.low - self.row)
        elif f == b"S":
            below = -min(one, self.low)
        elif f == b"T":
            below = one
        elif f == b"r":
            self.region = not (a <= 1 and (b == 0 or b >= self.rows))
            self.row = self.col = 0
        elif f == b"s" and not m["n"]:
            self.saved = (self.row, self.col)
        elif f == b"u" and not m["n"]:
            self.row, self.col = self.saved
        self.moved(below)
        return said


def winsize(fd):
    return fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8)


def main(argv):
    pid, child = None, None
    clears = Clears(24, 80)

    def resized(*_):
        try:
            size = winsize(0)
        except OSError:
            return
        rows, columns = struct.unpack("HHHH", size)[:2]
        clears.size(rows, columns)
        if pid:  # (not in its program's process, before that's started)
            try:
                fcntl.ioctl(child, termios.TIOCSWINSZ, size)
            except OSError:
                pass

    def hang_up(*_):  # (as the terminal's closing does: its program's process group sent SIGHUP)
        if pid:
            try:
                os.killpg(pid, signal.SIGHUP)
            except OSError:
                pass

    signal.signal(signal.SIGWINCH, resized)
    resized()
    try:
        mode = termios.tcgetattr(0)
        mode[0] |= IUTF8
    except termios.error:
        mode = None
    pid, child = pty.fork()
    if pid == 0:  # (its pty as the terminal's was set: line editing's keys, UTF-8 for its erasing; its size)
        for s in (signal.SIGPIPE, signal.SIGXFSZ, signal.SIGWINCH):
            signal.signal(s, signal.SIG_DFL)
        try:
            if mode:
                termios.tcsetattr(0, termios.TCSANOW, mode)
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", clears.rows, clears.columns, 0, 0))
        except (termios.error, OSError):
            pass
        try:
            os.execvp(argv[0], argv)
        except OSError as e:
            os.write(2, f"{argv[0]}: {e.strerror}\r\n".encode())
        os._exit(127)
    resized()
    try:
        tty.setraw(0)
    except termios.error:
        pass
    wake, woken = os.pipe()
    for fd in (0, 1, child, wake, woken):
        os.set_blocking(fd, False)
    signal.set_wakeup_fd(woken)
    signal.signal(signal.SIGCHLD, lambda *_: None)
    for s in (signal.SIGHUP, signal.SIGTERM):
        signal.signal(s, hang_up)
    to_child, to_outer, reading, outer, status = bytearray(), bytearray(), True, True, None

    def read_child():
        nonlocal reading
        try:
            data = os.read(child, 65536)
        except BlockingIOError:
            return False
        except OSError:
            data = b""
        if not data:
            reading = False
        to_outer.extend(clears.take(data, hold=reading) if data or clears.held else b"")
        return bool(data)

    while reading or to_outer:
        rd = [wake] + ([0] if outer and len(to_child) < BUFFER else [])
        if reading and len(to_outer) < BUFFER:
            rd.append(child)
        wr = ([child] if to_child and reading else []) + ([1] if to_outer and outer else [])
        r, w, _ = select.select(rd, wr, [], HOLD_S if clears.held else None)
        if not r and not w:
            to_outer.extend(clears.flush())
        if wake in r:
            while True:
                try:
                    if not os.read(wake, 512):
                        break
                except OSError:
                    break
            if status is None:
                done, status = os.waitpid(pid, os.WNOHANG)
                if done != pid:
                    status = None
                else:  # (its program's ended: what it wrote last sent, though something it started holds its pty)
                    while reading and read_child():
                        pass
                    reading = False
                    to_outer.extend(clears.flush())
                    continue
        if child in r:
            read_child()
        if 0 in r:
            try:
                data = os.read(0, 65536)
            except BlockingIOError:
                data = None
            except OSError:
                data = b""
            if data == b"":  # (the terminal's gone: its program's pty hung up too)
                outer, reading = False, False
                to_outer.clear()
                hang_up()
            elif data:
                to_child.extend(data)
        if child in w:
            try:
                del to_child[:os.write(child, to_child)]
            except BlockingIOError:
                pass
            except OSError:
                to_child.clear()
        if 1 in w:
            try:
                del to_outer[:os.write(1, to_outer)]
            except BlockingIOError:
                pass
            except OSError:
                outer = False
        if not outer:
            to_outer.clear()
    os.close(child)
    if status is None:
        status = os.waitpid(pid, 0)[1]
    if os.WIFSIGNALED(status):
        sig = os.WTERMSIG(status)
        if sig not in CORE:
            signal.signal(sig, signal.SIG_DFL)
            signal.pthread_sigmask(signal.SIG_UNBLOCK, [sig])
            os.kill(os.getpid(), sig)
        sys.exit(128 + sig)
    sys.exit(os.waitstatus_to_exitcode(status))


if __name__ == "__main__":
    main(sys.argv[2:] if sys.argv[1:2] == ["--"] else sys.argv[1:])
