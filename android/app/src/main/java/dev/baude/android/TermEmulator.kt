package dev.baude.android

/** A terminal's screen as xterm keeps it (what VTE does for Multiplex on the desktop): the bytes a program writes, its
 * escapes followed (cursor moves, erasing, colours, scroll regions, the alternate screen, the title), into a grid of
 * cells and the lines scrolled off the top. reply is what the terminal answers back (where the cursor is, what it is). */
class TermEmulator(columns: Int, rows: Int, private val reply: (ByteArray) -> Unit = {}) {
    /** A line of cells: each one's character (0: blank; WIDE_TAIL: the right half of a wide one before it), colours
     * (DEFAULT, 0–255, or TRUE|rgb) and look (BOLD…). wrapped: it went on into the next line. */
    class Line(n: Int) {
        var ch = IntArray(n)
        var fg = IntArray(n) { DEFAULT }
        var bg = IntArray(n) { DEFAULT }
        var look = ByteArray(n)
        var wrapped = false

        fun size() = ch.size

        fun resize(n: Int) {
            if (n == ch.size) return
            val old = ch.size
            ch = ch.copyOf(n)
            fg = fg.copyOf(n).also { if (n > old) it.fill(DEFAULT, old, n) }
            bg = bg.copyOf(n).also { if (n > old) it.fill(DEFAULT, old, n) }
            look = look.copyOf(n)
        }

        fun clear(from: Int, to: Int, bgc: Int) {
            for (i in from.coerceAtLeast(0) until to.coerceAtMost(ch.size)) {
                ch[i] = 0; fg[i] = DEFAULT; bg[i] = bgc; look[i] = 0
            }
        }

        fun text(): String = buildString {
            for (c in ch) if (c != WIDE_TAIL) appendCodePoint(if (c == 0) ' '.code else c)
        }.trimEnd()
    }

    var columns = columns.coerceAtLeast(1); private set
    var rows = rows.coerceAtLeast(1); private set
    private var main = Array(this.rows) { Line(this.columns) }
    private var alt = Array(this.rows) { Line(this.columns) }
    var altScreen = false; private set
    /** The lines scrolled off the top of the main screen, oldest first (SCROLLBACK at most). */
    val scrollback = ArrayDeque<Line>()
    val screen get() = if (altScreen) alt else main

    var cursorRow = 0; private set
    var cursorCol = 0; private set
    var cursorShown = true; private set
    var appCursorKeys = false; private set
    var bracketedPaste = false; private set
    var title: String? = null; private set
    /** Bumped whenever what's shown changes. */
    var changes = 0L; private set

    private var wrapNext = false
    private var autowrap = true
    private var originMode = false
    private var insertMode = false
    private var top = 0
    private var bottom = this.rows - 1
    private var fg = DEFAULT
    private var bg = DEFAULT
    private var look = 0
    private var tabs = BooleanArray(this.columns) { it % 8 == 0 }
    private var g0Lines = false
    private var g1Lines = false
    private var shifted = false
    private var lastChar = 0

    private data class Saved(val row: Int, val col: Int, val fg: Int, val bg: Int, val look: Int, val wrap: Boolean,
                             val origin: Boolean, val g0: Boolean, val g1: Boolean, val shifted: Boolean)
    private var savedMain: Saved? = null
    private var savedAlt: Saved? = null

    // ---- the bytes, decoded (UTF-8 across writes) and parsed
    private var state = GROUND
    private val params = IntArray(32)
    private val subs = BooleanArray(32) // (a parameter that came after a colon: 38:2:r:g:b)
    private var nParams = 0
    private var paramStarted = false
    private var prefix = 0 // ('?', '>', '=' or 0: a CSI's private marker)
    private var inter = 0  // (a CSI's or ESC's intermediate byte)
    private val osc = StringBuilder()
    private var utfLeft = 0
    private var utfCp = 0

    fun feed(data: ByteArray, from: Int = 0, to: Int = data.size) {
        for (i in from until to) {
            val b = data[i].toInt() and 0xff
            if (utfLeft > 0) {
                if (b and 0xC0 == 0x80) {
                    utfCp = (utfCp shl 6) or (b and 0x3F)
                    if (--utfLeft == 0) take(utfCp)
                    continue
                }
                utfLeft = 0
                take(0xFFFD)
            }
            when {
                b < 0x80 -> take(b)
                b and 0xE0 == 0xC0 -> { utfCp = b and 0x1F; utfLeft = 1 }
                b and 0xF0 == 0xE0 -> { utfCp = b and 0x0F; utfLeft = 2 }
                b and 0xF8 == 0xF0 -> { utfCp = b and 0x07; utfLeft = 3 }
                else -> take(0xFFFD)
            }
        }
        changes++
    }

    fun feed(text: String) = feed(text.toByteArray(Charsets.UTF_8))

    private fun take(c: Int) {
        if (state == OSC_STRING || state == OSC_ESC) return oscChar(c)
        if (state == IGNORE_STRING || state == IGNORE_ESC) {
            state = when {
                c == 0x07 -> GROUND
                c == 0x1B -> IGNORE_ESC
                state == IGNORE_ESC && c == '\\'.code -> GROUND
                else -> IGNORE_STRING
            }
            return
        }
        if (c < 0x20 || c == 0x7F) return control(c)
        when (state) {
            GROUND -> print(c)
            ESCAPE -> escape(c)
            ESCAPE_INTER -> escapeInter(c)
            CSI -> csi(c)
        }
    }

    private fun control(c: Int) {
        when (c) {
            0x1B -> { state = ESCAPE; inter = 0 }
            0x07, 0x00, 0x7F -> {}
            0x08 -> { if (cursorCol > 0) cursorCol--; wrapNext = false }
            0x09 -> tab(1)
            0x0A, 0x0B, 0x0C -> index()
            0x0D -> { cursorCol = 0; wrapNext = false }
            0x0E -> shifted = true
            0x0F -> shifted = false
            0x18, 0x1A -> state = GROUND
        }
    }

    // ---- printing
    private fun print(c0: Int) {
        var c = c0
        if (if (shifted) g1Lines else g0Lines) c = LINES[c] ?: c
        val w = width(c)
        if (w == 0) return // (a combining mark: kept with nothing here)
        if (wrapNext) {
            if (autowrap) {
                screen[cursorRow].wrapped = true
                cursorCol = 0
                index()
            }
            wrapNext = false
        }
        if (w == 2 && cursorCol == columns - 1) { // (no room for both halves: on at the next line's start)
            if (autowrap) {
                screen[cursorRow].clear(cursorCol, columns, bg)
                screen[cursorRow].wrapped = true
                cursorCol = 0
                index()
            } else return
        }
        val line = screen[cursorRow]
        if (insertMode) shiftRight(line, cursorCol, w)
        put(line, cursorCol, c)
        if (w == 2 && cursorCol + 1 < columns) put(line, cursorCol + 1, WIDE_TAIL)
        lastChar = c
        if (cursorCol + w >= columns) {
            cursorCol = columns - 1
            wrapNext = true
        } else cursorCol += w
    }

    private fun put(line: Line, at: Int, c: Int) {
        line.ch[at] = c; line.fg[at] = fg; line.bg[at] = bg; line.look[at] = look.toByte()
    }

    private fun shiftRight(line: Line, at: Int, n: Int) {
        for (i in columns - 1 downTo at + n) {
            line.ch[i] = line.ch[i - n]; line.fg[i] = line.fg[i - n]; line.bg[i] = line.bg[i - n]; line.look[i] = line.look[i - n]
        }
        line.clear(at, (at + n).coerceAtMost(columns), bg)
    }

    // ---- moving and scrolling
    private fun tab(n: Int) {
        repeat(n) {
            var i = cursorCol + 1
            while (i < columns - 1 && !tabs[i]) i++
            cursorCol = i.coerceAtMost(columns - 1)
        }
        wrapNext = false
    }

    private fun backTab(n: Int) {
        repeat(n) {
            var i = cursorCol - 1
            while (i > 0 && !tabs[i]) i--
            cursorCol = i.coerceAtLeast(0)
        }
        wrapNext = false
    }

    /** LF: down a line, the region scrolled up at its foot. */
    private fun index() {
        if (cursorRow == bottom) scrollUp(1) else if (cursorRow < rows - 1) cursorRow++
    }

    private fun reverseIndex() {
        if (cursorRow == top) scrollDown(1) else if (cursorRow > 0) cursorRow--
    }

    private fun blank() = Line(columns).also { it.bg.fill(bg) }

    private fun scrollUp(n: Int) {
        val s = screen
        repeat(n.coerceAtMost(bottom - top + 1)) {
            val gone = s[top]
            for (i in top until bottom) s[i] = s[i + 1]
            s[bottom] = blank()
            if (!altScreen && top == 0) {
                scrollback.addLast(gone)
                if (scrollback.size > SCROLLBACK) scrollback.removeFirst()
            }
        }
    }

    private fun scrollDown(n: Int) {
        val s = screen
        repeat(n.coerceAtMost(bottom - top + 1)) {
            for (i in bottom downTo top + 1) s[i] = s[i - 1]
            s[top] = blank()
        }
    }

    private fun moveTo(row: Int, col: Int) {
        val (lo, hi) = if (originMode) top to bottom else 0 to rows - 1
        cursorRow = ((if (originMode) top else 0) + row).coerceIn(lo, hi)
        cursorCol = col.coerceIn(0, columns - 1)
        wrapNext = false
    }

    // ---- escapes
    private fun escape(c: Int) {
        state = GROUND
        when (c.toChar()) {
            '[' -> { state = CSI; nParams = 0; paramStarted = false; prefix = 0; inter = 0; params.fill(0); subs.fill(false) }
            ']' -> { state = OSC_STRING; osc.setLength(0) }
            'P', 'X', '^', '_' -> state = IGNORE_STRING
            '(', ')', '*', '+', '#', ' ', '%' -> { state = ESCAPE_INTER; inter = c }
            '7' -> save()
            '8' -> restore()
            'D' -> index()
            'E' -> { cursorCol = 0; index() }
            'M' -> reverseIndex()
            'H' -> tabs[cursorCol] = true
            'c' -> reset()
            '=', '>', '\\' -> {}
        }
    }

    private fun escapeInter(c: Int) {
        state = GROUND
        val lines = c == '0'.code
        when (inter.toChar()) {
            '(' -> g0Lines = lines
            ')' -> g1Lines = lines
        }
    }

    private fun oscChar(c: Int) {
        when {
            c == 0x07 -> endOsc()
            c == 0x1B -> state = OSC_ESC
            state == OSC_ESC -> { if (c == '\\'.code) endOsc() else { state = OSC_STRING; osc.appendCodePoint(c) } }
            osc.length < 4096 -> osc.appendCodePoint(c)
        }
    }

    private fun endOsc() {
        state = GROUND
        val s = osc.toString()
        val semi = s.indexOf(';')
        if (semi > 0 && s.substring(0, semi) in setOf("0", "2")) title = s.substring(semi + 1)
    }

    private fun csi(c: Int) {
        when {
            c in '0'.code..'9'.code -> {
                if (nParams == 0) nParams = 1
                val i = nParams - 1
                if (i < params.size) params[i] = (params[i] * 10 + (c - '0'.code)).coerceAtMost(65535)
                paramStarted = true
            }
            c == ';'.code || c == ':'.code -> {
                if (nParams == 0) nParams = 1
                if (nParams < params.size) { subs[nParams] = c == ':'.code; nParams++ }
            }
            c in 0x3C..0x3F -> prefix = c
            c in 0x20..0x2F -> inter = c
            c in 0x40..0x7E -> { state = GROUND; dispatch(c.toChar()) }
            else -> state = GROUND
        }
    }

    private fun p(i: Int, default: Int = 0): Int = if (i < nParams && params[i] != 0) params[i] else default

    private fun dispatch(f: Char) {
        if (prefix == '?'.code) {
            when (f) {
                'h', 'l' -> for (i in 0 until nParams.coerceAtLeast(1)) privateMode(params[i], f == 'h')
            }
            return
        }
        if (prefix == '>'.code) {
            if (f == 'c') reply("\u001b[>1;10;0c")
            return
        }
        if (inter == '!'.code && f == 'p') return softReset()
        if (inter != 0) return // (DECSCUSR's ' q' and the like: nothing here)
        val s = screen
        when (f) {
            '@' -> shiftRight(s[cursorRow], cursorCol, p(0, 1).coerceAtMost(columns - cursorCol))
            'A' -> { cursorRow = (cursorRow - p(0, 1)).coerceAtLeast(if (cursorRow >= top) top else 0); wrapNext = false }
            'B', 'e' -> { cursorRow = (cursorRow + p(0, 1)).coerceAtMost(if (cursorRow <= bottom) bottom else rows - 1); wrapNext = false }
            'C', 'a' -> { cursorCol = (cursorCol + p(0, 1)).coerceAtMost(columns - 1); wrapNext = false }
            'D' -> { cursorCol = (cursorCol - p(0, 1)).coerceAtLeast(0); wrapNext = false }
            'E' -> { cursorRow = (cursorRow + p(0, 1)).coerceAtMost(bottom); cursorCol = 0; wrapNext = false }
            'F' -> { cursorRow = (cursorRow - p(0, 1)).coerceAtLeast(top); cursorCol = 0; wrapNext = false }
            'G', '`' -> { cursorCol = (p(0, 1) - 1).coerceIn(0, columns - 1); wrapNext = false }
            'H', 'f' -> moveTo(p(0, 1) - 1, p(1, 1) - 1)
            'I' -> tab(p(0, 1))
            'Z' -> backTab(p(0, 1))
            'J' -> when (p(0)) {
                0 -> { s[cursorRow].clear(cursorCol, columns, bg); for (r in cursorRow + 1 until rows) s[r] = blank() }
                1 -> { for (r in 0 until cursorRow) s[r] = blank(); s[cursorRow].clear(0, cursorCol + 1, bg) }
                2 -> for (r in 0 until rows) s[r] = blank()
                3 -> scrollback.clear()
            }
            'K' -> when (p(0)) {
                0 -> s[cursorRow].clear(cursorCol, columns, bg)
                1 -> s[cursorRow].clear(0, cursorCol + 1, bg)
                2 -> s[cursorRow].clear(0, columns, bg)
            }
            'L' -> if (cursorRow in top..bottom) {
                val keep = bottom; val t = top
                top = cursorRow; scrollDown(p(0, 1)); top = t; bottom = keep
                cursorCol = 0
            }
            'M' -> if (cursorRow in top..bottom) {
                val t = top
                top = cursorRow
                val alt0 = altScreen
                altScreen = true // (lines deleted aren't scrolled into the scrollback)
                scrollUp(p(0, 1))
                altScreen = alt0
                top = t
                cursorCol = 0
            }
            'P' -> {
                val n = p(0, 1).coerceAtMost(columns - cursorCol)
                val l = s[cursorRow]
                for (i in cursorCol until columns - n) {
                    l.ch[i] = l.ch[i + n]; l.fg[i] = l.fg[i + n]; l.bg[i] = l.bg[i + n]; l.look[i] = l.look[i + n]
                }
                l.clear(columns - n, columns, bg)
            }
            'S' -> scrollUp(p(0, 1))
            'T' -> scrollDown(p(0, 1))
            'X' -> s[cursorRow].clear(cursorCol, cursorCol + p(0, 1), bg)
            'b' -> if (lastChar != 0) repeat(p(0, 1).coerceAtMost(columns * rows)) { print(lastChar) }
            'c' -> reply("\u001b[?62;22c")
            'd' -> moveTo(p(0, 1) - 1, cursorCol)
            'g' -> when (p(0)) { 0 -> tabs[cursorCol] = false; 3 -> tabs.fill(false) }
            'h', 'l' -> for (i in 0 until nParams) if (params[i] == 4) insertMode = f == 'h'
            'm' -> sgr()
            'n' -> when (p(0)) {
                5 -> reply("\u001b[0n")
                6 -> reply("\u001b[${cursorRow - (if (originMode) top else 0) + 1};${cursorCol + 1}R")
            }
            'r' -> {
                val t = p(0, 1) - 1
                val b = p(1, rows) - 1
                if (t < b && b < rows) { top = t; bottom = b }
                else { top = 0; bottom = rows - 1 }
                moveTo(0, 0)
            }
            's' -> save()
            'u' -> restore()
        }
    }

    private fun privateMode(n: Int, on: Boolean) {
        when (n) {
            1 -> appCursorKeys = on
            6 -> { originMode = on; moveTo(0, 0) }
            7 -> autowrap = on
            25 -> cursorShown = on
            47, 1047 -> setAlt(on, clear = n == 1047 && !on)
            1048 -> if (on) save() else restore()
            1049 -> if (on) { save(); setAlt(true, clear = true) } else { setAlt(false, clear = false); restore() }
            2004 -> bracketedPaste = on
        }
    }

    private fun setAlt(on: Boolean, clear: Boolean) {
        if (on == altScreen) { if (on && clear) for (r in 0 until rows) alt[r] = blank(); return }
        if (on) for (r in 0 until rows) alt[r] = blank()
        altScreen = on
    }

    private fun sgr() {
        if (nParams == 0) { fg = DEFAULT; bg = DEFAULT; look = 0; return }
        var i = 0
        while (i < nParams) {
            val n = params[i]
            when (n) {
                0 -> { fg = DEFAULT; bg = DEFAULT; look = 0 }
                1 -> look = look or BOLD
                2 -> look = look or DIM
                3 -> look = look or ITALIC
                4 -> look = if (i + 1 < nParams && subs[i + 1] && params[i + 1] == 0) look and UNDERLINE.inv() else look or UNDERLINE
                7 -> look = look or INVERSE
                8 -> look = look or HIDDEN
                9 -> look = look or STRIKE
                21 -> look = look or UNDERLINE
                22 -> look = look and (BOLD or DIM).inv()
                23 -> look = look and ITALIC.inv()
                24 -> look = look and UNDERLINE.inv()
                27 -> look = look and INVERSE.inv()
                28 -> look = look and HIDDEN.inv()
                29 -> look = look and STRIKE.inv()
                in 30..37 -> fg = n - 30
                39 -> fg = DEFAULT
                in 40..47 -> bg = n - 40
                49 -> bg = DEFAULT
                in 90..97 -> fg = n - 90 + 8
                in 100..107 -> bg = n - 100 + 8
                38, 48 -> {
                    var colour = DEFAULT
                    when (params.getOrElse(i + 1) { 0 }) {
                        5 -> { colour = params.getOrElse(i + 2) { 0 }.coerceIn(0, 255); i += 2 }
                        2 -> {
                            // (38;2;r;g;b, or 38:2::r:g:b with a colour space's place left empty)
                            val skip = if (i + 5 < nParams && subs[i + 2] && subs[i + 5]) 1 else 0
                            val r = params.getOrElse(i + 2 + skip) { 0 }
                            val g = params.getOrElse(i + 3 + skip) { 0 }
                            val b = params.getOrElse(i + 4 + skip) { 0 }
                            colour = TRUE or ((r and 255) shl 16) or ((g and 255) shl 8) or (b and 255)
                            i += 4 + skip
                        }
                    }
                    if (n == 38) fg = colour else bg = colour
                }
            }
            i++
            while (i < nParams && subs[i] && params[i - 1] != 38 && params[i - 1] != 48) i++ // (a sub-parameter not read)
        }
    }

    private fun save() {
        val s = Saved(cursorRow, cursorCol, fg, bg, look, wrapNext, originMode, g0Lines, g1Lines, shifted)
        if (altScreen) savedAlt = s else savedMain = s
    }

    private fun restore() {
        val s = (if (altScreen) savedAlt else savedMain) ?: Saved(0, 0, DEFAULT, DEFAULT, 0, false, false, false, false, false)
        cursorRow = s.row.coerceIn(0, rows - 1); cursorCol = s.col.coerceIn(0, columns - 1)
        fg = s.fg; bg = s.bg; look = s.look; wrapNext = s.wrap; originMode = s.origin
        g0Lines = s.g0; g1Lines = s.g1; shifted = s.shifted
    }

    private fun softReset() {
        cursorShown = true; insertMode = false; originMode = false; autowrap = true; appCursorKeys = false
        top = 0; bottom = rows - 1; fg = DEFAULT; bg = DEFAULT; look = 0; g0Lines = false; g1Lines = false; shifted = false
        savedMain = null; savedAlt = null
    }

    /** As it was made: blank, the scrollback gone. */
    fun reset() {
        softReset()
        main = Array(rows) { Line(columns) }
        alt = Array(rows) { Line(columns) }
        altScreen = false
        scrollback.clear()
        cursorRow = 0; cursorCol = 0; wrapNext = false; bracketedPaste = false; title = null; lastChar = 0
        tabs = BooleanArray(columns) { it % 8 == 0 }
        state = GROUND; utfLeft = 0
        changes++
    }

    /** Another size: lines cut or padded to it (a scrollback line keeps its own width till it's back on screen);
     * fewer rows push the top ones into the scrollback, so the cursor's line stays in sight, and more bring them back. */
    fun resize(newColumns: Int, newRows: Int) {
        val c = newColumns.coerceAtLeast(1)
        val r = newRows.coerceAtLeast(1)
        if (c == columns && r == rows) return
        for (l in main) l.resize(c)
        for (l in alt) l.resize(c)
        fun fit(lines: Array<Line>, keepBack: Boolean): Pair<Array<Line>, Int> {
            val list = lines.toMutableList()
            var shift = 0
            // (rows taken: blank ones below the cursor first, then from the top)
            while (list.size > r) {
                val last = list.size - 1
                if (last > cursorRow && list[last].ch.all { it == 0 }) list.removeAt(last)
                else { val gone = list.removeAt(0); if (keepBack) scrollback.addLast(gone); shift++ }
            }
            while (list.size < r) {
                if (keepBack && scrollback.isNotEmpty()) { list.add(0, scrollback.removeLast().also { it.resize(c) }); shift-- }
                else list.add(Line(c))
            }
            return list.toTypedArray() to shift
        }
        val (m, shiftMain) = fit(main, true)
        val (a, shiftAlt) = fit(alt, false)
        main = m; alt = a
        val shift = if (altScreen) shiftAlt else shiftMain
        while (scrollback.size > SCROLLBACK) scrollback.removeFirst()
        columns = c
        rows = r
        cursorRow = (cursorRow - shift).coerceIn(0, rows - 1)
        cursorCol = cursorCol.coerceIn(0, columns - 1)
        top = 0; bottom = rows - 1
        tabs = BooleanArray(columns) { it % 8 == 0 }
        wrapNext = false
        changes++
    }

    /** What's on screen as text, a line each (the scrollback's too, first, with all). */
    fun text(all: Boolean = false): String =
        ((if (all && !altScreen) scrollback.toList() else emptyList()) + screen.toList()).joinToString("\n") { it.text() }.trimEnd()

    private fun reply(s: String) = reply(s.toByteArray(Charsets.UTF_8))

    companion object {
        const val SCROLLBACK = 10000 // (multiplexterm.SCROLLBACK: the lines kept above the screen)
        const val DEFAULT = -1
        const val TRUE = 0x1000000
        const val WIDE_TAIL = -2
        const val BOLD = 1; const val DIM = 2; const val ITALIC = 4; const val UNDERLINE = 8
        const val INVERSE = 16; const val HIDDEN = 32; const val STRIKE = 64

        private const val GROUND = 0; private const val ESCAPE = 1; private const val ESCAPE_INTER = 2
        private const val CSI = 3; private const val OSC_STRING = 4; private const val OSC_ESC = 5
        private const val IGNORE_STRING = 6; private const val IGNORE_ESC = 7

        /** DEC's line-drawing set (ESC ( 0), as the box characters it stands for. */
        private val LINES: Map<Int, Int> = mapOf(
            '`' to '◆', 'a' to '▒', 'f' to '°', 'g' to '±', 'j' to '┘', 'k' to '┐', 'l' to '┌', 'm' to '└', 'n' to '┼',
            'o' to '⎺', 'p' to '⎻', 'q' to '─', 'r' to '⎼', 's' to '⎽', 't' to '├', 'u' to '┤', 'v' to '┴', 'w' to '┬',
            'x' to '│', 'y' to '≤', 'z' to '≥', '{' to 'π', '|' to '≠', '}' to '£', '~' to '·',
        ).entries.associate { it.key.code to it.value.code }

        /** How many cells a character takes: 0 for a combining mark, 2 for East Asian wide ones and emoji. */
        fun width(c: Int): Int = when {
            c == 0x200B || c == 0x200D || c in 0xFE00..0xFE0F -> 0
            Character.getType(c).let { it == Character.NON_SPACING_MARK.toInt() || it == Character.ENCLOSING_MARK.toInt() } -> 0
            c in 0x1100..0x115F || c in 0x2E80..0x303E || c in 0x3041..0x33FF || c in 0x3400..0x4DBF ||
                c in 0x4E00..0x9FFF || c in 0xA000..0xA4CF || c in 0xAC00..0xD7A3 || c in 0xF900..0xFAFF ||
                c in 0xFE30..0xFE4F || c in 0xFF00..0xFF60 || c in 0xFFE0..0xFFE6 || c in 0x1F300..0x1F64F ||
                c in 0x1F900..0x1F9FF || c in 0x20000..0x3FFFD -> 2
            else -> 1
        }

        /** The 256 colours' RGB: the 16 (VTE's own), the 6×6×6 cube, the greys. */
        fun indexed(n: Int): Int = when {
            n < 16 -> BASE[n]
            n < 232 -> {
                val i = n - 16
                fun v(x: Int) = if (x == 0) 0 else 55 + x * 40
                (v(i / 36) shl 16) or (v(i / 6 % 6) shl 8) or v(i % 6)
            }
            else -> (8 + (n - 232) * 10).let { (it shl 16) or (it shl 8) or it }
        }

        private val BASE = intArrayOf(0x000000, 0xaa0000, 0x00aa00, 0xaa5500, 0x0000aa, 0xaa00aa, 0x00aaaa, 0xaaaaaa,
            0x555555, 0xff5555, 0x55ff55, 0xffff55, 0x5555ff, 0xff55ff, 0x55ffff, 0xffffff)
    }
}
