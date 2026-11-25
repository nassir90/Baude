package dev.baude.android

import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEvent
import androidx.compose.ui.input.key.isAltPressed
import androidx.compose.ui.input.key.isCtrlPressed
import androidx.compose.ui.input.key.isMetaPressed
import androidx.compose.ui.input.key.isShiftPressed
import androidx.compose.ui.input.key.key
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.ParagraphStyle
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLayoutResult
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.OffsetMapping
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.input.TransformedText
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextIndent
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.TextUnit

// The box a message is written in: its markdown shown as it's written (composerview.ComposerMarkdown). Bullets are kept
// as "•" while editing and turned back into "-" by toMarkdown as the message is sent.

const val BULLET = '•'
const val SUB_BULLET = '◦'

/** • at the top level, ◦ one level in, alternating further down. */
fun bulletFor(depth: Int) = if (depth % 2 == 0) BULLET else SUB_BULLET

private val ITEM = Regex("""^(\s*)([-*+•◦]|\d+[.)])\s+(.*)$""")
private val LIST_ITEM = Regex("""^(\s*)([-*+•◦]|\d+[.)])\s""")
private val FENCE_LINE = Regex("""^\s*(```|~~~)\s*(\S*)""")
private val HEADING = Regex("""^(#{1,6})\s+""")
private val LINE_LEAD = java.util.regex.Pattern.compile("""[ \t]*(?:(?:[-*+•◦]|\d+[.)])[ \t]+(?:\[[ xX]\][ \t]+)?|#{1,6}[ \t]+|>[ \t]?)?""")
private val WRAP_MARKS = mapOf('*' to "**", '`' to "`", '/' to "*")

/** A tab counted as up to the next multiple of four (str.expandtabs(4)). */
private fun width(s: String): Int {
    var col = 0
    for (ch in s) col += if (ch == '\t') 4 - col % 4 else 1
    return col
}

// ---------------------------------------------------------------------------------------------------- fences --

/** Where the code blocks are in text being written, of its lines: (first line, last line, closed), in order
 * (markdownview.fence_spans). A fence that a later bare one closes goes to it. One nothing closes has only the lines
 * right after it, up to a blank one. With an odd number, one's left over: one on a line being typed on if there is,
 * else the one that leaves the fewest lines in code. */
fun fenceSpans(lines: List<String>, tildes: Boolean = true, typing: Set<Int> = emptySet()): List<Triple<Int, Int, Boolean>> {
    val fences = ArrayList<Pair<Int, Boolean>>() // (line, bare)
    for ((n, line) in lines.withIndex()) {
        if ("```" in line || tildes && "~~~" in line) {
            val m = FENCE_LINE.find(line)
            if (m != null && (tildes || m.groupValues[1] == "```")) fences.add(n to m.groupValues[2].isEmpty())
        }
    }
    fun paired(some: List<Pair<Int, Boolean>>): Pair<List<Pair<Int, Int>>, List<Int>> {
        val closed = ArrayList<Pair<Int, Int>>()
        val loose = ArrayList<Int>()
        var i = 0
        while (i < some.size) {
            if (i + 1 < some.size && some[i + 1].second) { closed.add(some[i].first to some[i + 1].first); i += 2 }
            else { loose.add(some[i].first); i++ }
        }
        return closed to loose
    }
    var (closed, loose) = paired(fences)
    if (fences.size % 2 == 1 && fences.size <= 64) {
        fun without(j: Int): Pair<List<Pair<Int, Int>>, List<Int>> {
            val (kept, left) = paired(fences.filterIndexed { i, _ -> i != j })
            return kept to (left + fences[j].first)
        }
        val here = fences.indices.filter { fences[it].first in typing }
        val found = if (here.isNotEmpty()) without(here.last()) else fences.indices.reversed().map { without(it) }
            .minWithOrNull(compareBy({ it.second.size }, { f -> f.first.sumOf { it.second - it.first } }))!!
        closed = found.first
        loose = found.second
    }
    val atFence = fences.map { it.first }.toSet()
    val spans = closed.map { Triple(it.first, it.second, true) }.toMutableList()
    for (first in loose) {
        var last = first
        while (last + 1 < lines.size && lines[last + 1].isNotBlank() && last + 1 !in atFence) last++
        spans.add(Triple(first, last, false))
    }
    return spans.sortedWith(compareBy({ it.first }, { it.second }, { it.third }))
}

/** What kind of line each is: (inside a code fence, a fence line itself). */
class LineKind(val code: Boolean, val fence: Boolean)

fun lineKinds(lines: List<String>, caretLine: Int): List<LineKind> {
    val kinds = HashMap<Int, LineKind>()
    for ((first, last, closed) in fenceSpans(lines, tildes = false, typing = setOf(caretLine))) {
        for (n in first + 1..last) kinds[n] = LineKind(true, false)
        kinds[first] = LineKind(false, true)
        if (closed) kinds[last] = LineKind(true, true)
    }
    return lines.indices.map { kinds[it] ?: LineKind(false, false) }
}

private fun lineOf(text: String, offset: Int) = text.substring(0, offset.coerceIn(0, text.length)).count { it == '\n' }

private fun lineStart(text: String, offset: Int) = text.lastIndexOf('\n', offset.coerceIn(0, text.length) - 1) + 1

/** Editor text as markdown to send: bullets go back to "-" (code blocks untouched). */
fun toMarkdown(text: String): String {
    val lines = text.split("\n")
    val code = fenceSpans(lines, tildes = false).flatMap { (a, b, _) -> a..b }.toSet()
    return lines.withIndex().joinToString("\n") { (n, line) ->
        if (n in code) line else line.replace(Regex("^(\\s*)[•◦](?= )"), "$1-")
    }
}

// ------------------------------------------------------------------------------------------------------ edits --

/** What the box keeps between edits: a wrap's run, left selected so another can go round it. */
class ComposerState {
    var justWrapped: TextRange? = null
}

/** The box's text after an edit, as the desktop's buffer handlers make it (make_bullets, newline, wrap_selection): the
 * value the field changed to, made over. */
fun composerEdit(old: TextFieldValue, new: TextFieldValue, st: ComposerState): TextFieldValue {
    var v = new
    if (old.text != new.text) {
        val sel = old.selection
        val ch = if (new.text.length == old.text.length - (sel.max - sel.min) + 1 && !sel.collapsed)
            new.text[sel.min].takeIf { it in WRAP_MARKS && new.text == old.text.replaceRange(sel.min, sel.max, it.toString()) } else null
        if (ch != null) wrapSelection(old, ch, st)?.let { v = it }
        else if (isNewline(old, new)) enter(old)?.let { v = it }
    }
    return makeBullets(v)
}

/** A single new line put in at the caret (what Enter does). */
fun isNewline(old: TextFieldValue, new: TextFieldValue): Boolean {
    val s = old.selection
    return s.collapsed && new.text.length == old.text.length + 1 &&
        new.text == old.text.substring(0, s.start) + "\n" + old.text.substring(s.start)
}

/** The code block the caret's in (on an opening fence, or inside): (first line, last line, closed), else null. */
private fun blockHere(text: String, caret: Int): Triple<Int, Int, Boolean>? {
    val line = lineOf(text, caret)
    return fenceSpans(text.split("\n"), tildes = false, typing = setOf(line)).firstOrNull { (first, last, closed) ->
        first <= line && (if (closed) line < last else line <= last)
    }
}

/** Enter's handling in a box (not sending): in a code block a plain new line, and an unclosed block gets its closing
 * fence; on a list item the next item (an empty one drops its marker). Null: a plain new line (the field's own). */
private fun enter(old: TextFieldValue): TextFieldValue? {
    val c = old.selection.start
    val text = old.text
    val block = blockHere(text, c)
    if (block != null) {
        if (block.third) return null
        val t = text.substring(0, c) + "\n" + text.substring(c)
        val lines = t.split("\n")
        var end = 0
        for (i in 0..block.second + 1) end += lines[i].length + 1
        end -= 1
        return TextFieldValue(t.substring(0, end) + "\n```" + t.substring(end), TextRange(c + 1))
    }
    val start = lineStart(text, c)
    val m = ITEM.find(text.substring(start, c)) ?: return null
    if (m.groupValues[3].isBlank()) return TextFieldValue(text.substring(0, start) + text.substring(c), TextRange(start))
    var marker = m.groupValues[2]
    if (marker[0].isDigit()) marker = "${marker.dropLast(1).toInt() + 1}${marker.last()}"
    else if (marker in setOf("-", "*", "+")) marker = bulletFor(width(m.groupValues[1]) / 2).toString()
    val put = "\n${m.groupValues[1]}$marker "
    return TextFieldValue(text.substring(0, c) + put + text.substring(c), TextRange(c + put.length))
}

/** "- ", "* " or "+ " starting a line becomes a real bullet: • at the top level, ◦ one level in (made again when a line
 * is indented or outdented). The same length, so the caret stays. */
fun makeBullets(v: TextFieldValue): TextFieldValue {
    val text = v.text
    if (text.none { it == '-' || it == '*' || it == '+' || it == BULLET || it == SUB_BULLET }) return v
    val lines = text.split("\n")
    val kinds = lineKinds(lines, lineOf(text, v.selection.end))
    var out: StringBuilder? = null
    var at = 0
    for ((n, line) in lines.withIndex()) {
        val m = if (kinds[n].code || kinds[n].fence) null else Regex("^(\\s*)([-*+•◦]) ").find(line)
        if (m != null) {
            val want = bulletFor(width(m.groupValues[1]) / 2)
            if (m.groupValues[2][0] != want) {
                if (out == null) out = StringBuilder(text)
                out[at + m.groupValues[1].length] = want
            }
        }
        at += line.length + 1
    }
    return if (out == null) v else v.copy(text = out.toString())
}

/** A selection, and *, ` or / typed: what's selected made bold, code or italic, as markdown has them, each of its
 * lines on its own (a list's marker, a box, a heading's #s left out), and still selected so another can go round it
 * too; marked so already: taken off (composerview.wrap_selection). Null: nothing to do. */
private fun wrapSelection(old: TextFieldValue, ch: Char, st: ComposerState): TextFieldValue? {
    val mark = WRAP_MARKS[ch] ?: return null
    val full = old.text
    val a = old.selection.min
    val b = old.selection.max
    val n = mark.length
    if ("\n" !in full.substring(a, b)) {
        fun run(i: Int, step: Int): Int {
            var k = 0
            while (k < 8 && i + step * k in full.indices && full[i + step * k] == mark[0]) k++
            return k
        }
        val left = run(a - 1, -1)
        val right = run(b, 1)
        val marked = when (mark) { "`" -> left >= 1 && right >= 1; "**" -> left >= 2 && right >= 2; else -> left in setOf(1, 3) && right in setOf(1, 3) }
        if (marked) {
            val t = full.substring(0, a - n) + full.substring(a, b) + full.substring(b + n)
            st.justWrapped = TextRange(a - n, b - n)
            return TextFieldValue(t, TextRange(a - n, b - n))
        }
    }
    val pieces = ArrayList<Pair<Int, Int>>()
    var at = a
    for (line in full.substring(a, b).split("\n")) {
        var s = at
        var e = at + line.length
        at = e + 1
        if (s == lineStart(full, s)) {
            val m = LINE_LEAD.matcher(full).region(s, full.length)
            if (m.lookingAt()) s = maxOf(s, minOf(e, m.end()))
        }
        while (s < e && full[s].isWhitespace()) s++
        while (e > s && full[e - 1].isWhitespace()) e--
        if (s < e) pieces.add(s to e)
    }
    if (pieces.isEmpty()) return null
    val sb = StringBuilder(full)
    for ((s, e) in pieces.reversed()) { sb.insert(e, mark); sb.insert(s, mark) }
    val first = pieces.first().first + n
    val last = pieces.last().second + n * (2 * pieces.size - 1)
    st.justWrapped = TextRange(first, last)
    return TextFieldValue(sb.toString(), TextRange(first, last))
}

/** Left or Right on its own with what a wrap just formatted still selected: the caret outside its marks, all that are
 * round it, so what's typed next is plain. Null: not that. */
private fun leaveWrapped(v: TextFieldValue, right: Boolean, st: ComposerState): TextFieldValue? {
    val j = st.justWrapped ?: return null
    if (v.selection.collapsed || v.selection.min != j.min || v.selection.max != j.max) return null
    st.justWrapped = null
    var a = v.selection.min
    var b = v.selection.max
    val full = v.text
    while (true) {
        val mark = listOf("**", "`", "*").firstOrNull { full.substring(maxOf(a - it.length, 0), a) == it && full.startsWith(it, b) } ?: break
        a -= mark.length
        b += mark.length
    }
    return v.copy(selection = TextRange(if (right) b else a))
}

/** Tab (step 1) or Shift+Tab (-1) on the lines the caret or the selection is on: a list item in or out a level, a line
 * inside a code block four spaces. Null: none was there (the focus moves). */
private fun indent(v: TextFieldValue, step: Int): TextFieldValue? {
    val text = v.text
    val lines = text.split("\n")
    val first = lineOf(text, v.selection.min)
    var last = lineOf(text, v.selection.max)
    if (last > first && v.selection.max == lineStart(text, v.selection.max)) last--
    val kinds = lineKinds(lines, lineOf(text, v.selection.end))
    class Move(val n: Int, val lead: Int, val unit: Int, val marker: String?)
    val moves = ArrayList<Move>()
    for ((n, line) in lines.withIndex()) {
        if (n !in first..last || kinds[n].fence) continue
        val m = if (kinds[n].code) null else LIST_ITEM.find(line)
        if (kinds[n].code || m != null) {
            val lead = line.length - line.trimStart(' ', '\t').length
            moves.add(Move(n, lead, if (m == null) 4 else if (m.groupValues[2][0] in "-*+•◦") 2 else m.groupValues[2].length + 1, m?.groupValues?.get(2)))
        }
    }
    if (moves.isEmpty()) return null
    val out = ArrayList(lines)
    val starts = IntArray(lines.size)
    var o = 0
    for (i in lines.indices) { starts[i] = o; o += lines[i].length + 1 }
    // (offsets after each line's change: how far each line's start moved)
    val delta = IntArray(lines.size)
    for (m in moves) {
        val line = lines[m.n]
        val w = width(line.substring(0, m.lead))
        val new = if (step > 0) w + m.unit else maxOf(0, w - m.unit)
        if (new == w) continue
        var rest = line.substring(m.lead)
        if (m.marker != null && m.marker[0] in "•◦") rest = bulletFor(new / 2) + rest.substring(1)
        out[m.n] = " ".repeat(new) + rest
        delta[m.n] = new - m.lead
    }
    fun shifted(off: Int): Int {
        val line = lineOf(text, off)
        var d = 0
        for (i in 0 until line) d += delta[i]
        val into = off - starts[line]
        val lead = moves.firstOrNull { it.n == line }?.lead ?: 0
        return off + d + if (delta[line] != 0 && into >= lead) delta[line] else if (delta[line] != 0) maxOf(delta[line], -into) else 0
    }
    return TextFieldValue(out.joinToString("\n"), TextRange(shifted(v.selection.start), shifted(v.selection.end)))
}

/** The list item the line at is in, with all under it, swapped with its sibling above (by -1) or below (1): the nearest
 * item that way at its level (composerview.swap_list_item). (the new lines, the first and past the last line replaced,
 * where the line at is now), or null. */
fun swapListItem(lines: List<String>, at: Int, by: Int): Triple<List<String>, Pair<Int, Int>, Int>? {
    val fenced = lines.take(at + 1).count { Regex("^\\s*(```|~~~)").containsMatchIn(it) } % 2 == 1
    fun item(i: Int) = LIST_ITEM.find(lines[i])
    fun indent(i: Int) = width(item(i)!!.groupValues[1])
    fun on(i: Int) = (lines[i].startsWith(" ") || lines[i].startsWith("\t")) && lines[i].isNotBlank() && item(i) == null
    fun past(i: Int): Int {
        var end = i + 1
        while (end < lines.size && (on(end) || item(end) != null && indent(end) > indent(i))) end++
        return end
    }
    var start = at
    while (start >= 0 && on(start)) start--
    if (fenced || start < 0 || item(start) == null) return null
    val end = past(start)
    val one: Int
    val two: Int
    val three: Int
    if (by > 0) {
        if (end >= lines.size || item(end) == null || indent(end) != indent(start)) return null
        one = start; two = end; three = past(end)
    } else {
        var above = start - 1
        while (above >= 0 && (on(above) || item(above) != null && indent(above) > indent(start))) above--
        if (above < 0 || item(above) == null || indent(above) != indent(start)) return null
        one = above; two = start; three = end
    }
    val first = lines.subList(one, two).toMutableList()
    val second = lines.subList(two, three).toMutableList()
    val m1 = item(one)!!
    val m2 = item(two)!!
    val n1 = m1.groupValues[2]
    val n2 = m2.groupValues[2]
    if (n1[0].isDigit() && n2[0].isDigit()) { // (each takes the other's number: they stay in order)
        val i1 = m1.groupValues[1].length
        val i2 = m2.groupValues[1].length
        first[0] = first[0].substring(0, i1) + n2 + first[0].substring(i1 + n1.length)
        second[0] = second[0].substring(0, i2) + n1 + second[0].substring(i2 + n2.length)
    }
    val moved = at - start + if (by < 0) one else one + second.size
    return Triple(second + first, one to three, moved)
}

/** Alt and Up or Down: the list item the caret's in moved past its sibling that way. Null: not in an item. */
private fun moveItem(v: TextFieldValue, by: Int): TextFieldValue? {
    val text = v.text
    val lines = text.split("\n")
    val line = lineOf(text, v.selection.end)
    val column = v.selection.end - lineStart(text, v.selection.end)
    var at = line
    while (at >= 0 && (lines[at].startsWith(" ") || lines[at].startsWith("\t")) && lines[at].isNotBlank() && !LIST_ITEM.containsMatchIn(lines[at])) at--
    if (at < 0 || !LIST_ITEM.containsMatchIn(lines[at])) return null
    val (new, range, moved) = swapListItem(lines, line, by) ?: return v // (no sibling that way: the key's still this)
    val all = lines.take(range.first) + new + lines.drop(range.second)
    var caret = 0
    for (i in 0 until moved) caret += all[i].length + 1
    caret += minOf(column, all[moved].length)
    return TextFieldValue(all.joinToString("\n"), TextRange(caret))
}

/** The box's own keys (before the field gets them): Shift+Enter a plain line break, Tab and Shift+Tab a list item or
 * code line in or out, Alt+Up and Alt+Down a list item moved, Left and Right out of a run just wrapped. The field's
 * new value if the key was one of these, else null. */
fun composerKey(e: KeyEvent, v: TextFieldValue, st: ComposerState): TextFieldValue? {
    val plain = !e.isCtrlPressed && !e.isMetaPressed && !e.isAltPressed && !e.isShiftPressed
    return when {
        e.key == Key.Enter && e.isShiftPressed && !e.isCtrlPressed && !e.isMetaPressed -> {
            val s = v.selection
            TextFieldValue(v.text.replaceRange(s.min, s.max, "\n"), TextRange(s.min + 1))
        }
        e.key == Key.Tab && !e.isCtrlPressed && !e.isMetaPressed && !e.isAltPressed -> indent(v, if (e.isShiftPressed) -1 else 1)
        e.isAltPressed && !e.isCtrlPressed && !e.isMetaPressed && (e.key == Key.DirectionUp || e.key == Key.DirectionDown) ->
            moveItem(v, if (e.key == Key.DirectionUp) -1 else 1)
        plain && (e.key == Key.DirectionLeft || e.key == Key.DirectionRight) -> leaveWrapped(v, e.key == Key.DirectionRight, st)
        else -> null
    }
}

// ------------------------------------------------------------------------------------------------------ the look --

/** The text styled as it will read (ComposerMarkdown._restyle_tags): headings at the chat's sizes, bullets, "> " quotes
 * (a sent quote's rule, its text set in past it and faded), bold, italic and `code`, fenced code shaded; the marks
 * (#, **, `) shown dim on the caret's line and hidden on the rest (a quote's unseen); the @names the box knows, and the
 * @ being searched, in their colour. */
class ComposerLook(
    private val caretLine: Int, private val known: (String) -> Boolean, private val atSearch: IntRange?,
    private val fg: Color, private val ref: Color, private val refTint: Color,
    private val h1: TextUnit, private val h2: TextUnit, private val mono: SpanStyle, private val quoteIndent: TextUnit,
) : VisualTransformation {
    private class Built(val text: AnnotatedString, val removed: List<IntArray>, val blocks: List<IntRange>,
                        val quotes: List<IntRange>)

    private var built: Built? = null

    private fun toT(o: Int): Int {
        val b = built ?: return o
        var r = o
        for (x in b.removed) if (x[1] <= o) r -= x[1] - x[0] else if (x[0] < o) r -= o - x[0] else break
        return r
    }

    private fun toO(t: Int): Int {
        val b = built ?: return t
        var shift = 0
        for (x in b.removed) { if (t > x[0] - shift) shift += x[1] - x[0] else break }
        return t + shift
    }

    /** Where an offset of the text is in what's shown. */
    fun mapped(offset: Int): Int = toT(offset)

    override fun filter(text: AnnotatedString): TransformedText {
        val b = build(text.text)
        built = b
        val mapping = object : OffsetMapping {
            override fun originalToTransformed(offset: Int) = toT(offset.coerceIn(0, text.length)).coerceIn(0, b.text.length)
            override fun transformedToOriginal(offset: Int) = toO(offset.coerceIn(0, b.text.length)).coerceIn(0, text.length)
        }
        return TransformedText(b.text, mapping)
    }

    private fun build(text: String): Built {
        val lines = text.split("\n")
        val kinds = lineKinds(lines, caretLine)
        val starts = IntArray(lines.size)
        var o = 0
        for (i in lines.indices) { starts[i] = o; o += lines[i].length + 1 }
        val removed = ArrayList<IntArray>()
        val quotes = ArrayList<IntRange>()
        val styles = ArrayList<Triple<SpanStyle, Int, Int>>()
        val dim = SpanStyle(color = fg.copy(alpha = fg.alpha * 0.4f))
        val bold = SpanStyle(fontWeight = FontWeight.Bold)
        fun hide(a: Int, b: Int) { if (b > a) { if (caretLine == lineOf(text, a)) styles.add(Triple(dim, a, b)) else removed.add(intArrayOf(a, b)) } }
        for ((n, line) in lines.withIndex()) {
            val s = starts[n]
            val e = s + line.length
            if (kinds[n].fence || kinds[n].code) {
                styles.add(Triple(if (kinds[n].fence) mono.copy(color = fg.copy(alpha = fg.alpha * 0.45f)) else mono, s, e))
                continue
            }
            val h = HEADING.find(line)
            if (h != null) {
                val level = minOf(h.groupValues[1].length, 3)
                styles.add(Triple(bold.copy(fontSize = when (level) { 1 -> h1; 2 -> h2; else -> TextUnit.Unspecified }), s, e))
                hide(s, s + h.range.last + 1)
            } else if (line.startsWith(">")) {
                styles.add(Triple(SpanStyle(color = fg.copy(alpha = fg.alpha * Tokens.Markdown.quoteOpacity.toFloat())), s, e))
                quotes.add(if (quotes.isNotEmpty() && quotes.last().last == s - 1) quotes.removeAt(quotes.lastIndex).first..e else s..e)
                hide(s, s + if (line.startsWith("> ")) 2 else 1)
            } else {
                val m = ITEM.find(line)
                if (m != null) {
                    val r = m.groups[2]!!.range
                    styles.add(Triple(bold.copy(color = fg.copy(alpha = fg.alpha * 0.7f)), s + r.first, s + r.last + 1))
                }
            }
            for ((rx, style, mark) in INLINE) {
                for (m in rx.findAll(line)) {
                    val a = s + m.range.first
                    val z = s + m.range.last + 1
                    styles.add(Triple(when (style) {
                        "inline" -> mono.copy(background = fg.copy(alpha = fg.alpha * 0.10f))
                        "bold" -> bold
                        else -> SpanStyle(fontStyle = FontStyle.Italic)
                    }, a, z))
                    hide(a, a + mark)
                    hide(z - mark, z)
                }
            }
        }
        val refStyle = SpanStyle(color = ref, fontWeight = FontWeight.SemiBold, background = refTint)
        for (m in MentionList.REF_RE.findAll(text)) if (known(m.groupValues[1].ifEmpty { m.groupValues[2] })) styles.add(Triple(refStyle, m.range.first, m.range.last + 1))
        if (atSearch != null) styles.add(Triple(refStyle, atSearch.first, minOf(atSearch.last + 1, text.length)))
        // (what's hidden is taken out; what's styled is moved with it)
        removed.sortBy { it[0] }
        val merged = ArrayList<IntArray>()
        for (r in removed) if (merged.isNotEmpty() && r[0] <= merged.last()[1]) merged.last()[1] = maxOf(merged.last()[1], r[1]) else merged.add(r.copyOf())
        val sb = StringBuilder()
        var at = 0
        for (r in merged) { sb.append(text, at, r[0]); at = r[1] }
        sb.append(text, at, text.length)
        fun move(off: Int): Int { var x = off; for (r in merged) if (r[1] <= off) x -= r[1] - r[0] else if (r[0] < off) x -= off - r[0] else break; return x }
        // (a run of quoted lines is a paragraph of its own, set in as a sent quote's text; a paragraph starts a line, so
        // the line breaks either side of it are drawn as nothing (U+200B), each kept at its place so offsets don't move)
        for (r in quotes) for (at in listOf(move(r.first) - 1, move(r.last))) if (at in sb.indices && sb[at] == '\n') sb.setCharAt(at, '\u200B')
        val out = AnnotatedString.Builder(sb.toString())
        for (r in quotes) out.addStyle(ParagraphStyle(textIndent = TextIndent(quoteIndent, quoteIndent)), move(r.first),
            minOf(move(r.last) + 1, sb.length))
        for ((style, a, z) in styles) { val ta = move(a); val tz = move(z); if (tz > ta) out.addStyle(style, ta, tz) }
        val blocks = fenceSpans(lines, tildes = false, typing = setOf(caretLine)).map { (a, z, _) -> starts[a]..(starts[z] + lines[z].length) }
        return Built(out.toAnnotatedString(), merged, blocks, quotes)
    }

    /** One rounded shade behind each code block, and a rule at the text's left (left) beside each run of quoted lines,
     * from the text's lines. */
    fun drawBlocks(scope: DrawScope, layout: TextLayoutResult?, color: Color, radius: Dp, inset: Dp, rule: Color, left: Dp) {
        val l = layout ?: return
        val b = built ?: return
        if (l.layoutInput.text.length != b.text.length) return
        with(scope) {
            for (r in b.blocks) {
                val a = toT(r.first).coerceIn(0, b.text.length)
                val z = toT(r.last).coerceIn(a, b.text.length)
                val top = l.getLineTop(l.getLineForOffset(a))
                val bottom = l.getLineBottom(l.getLineForOffset(z))
                drawRoundRect(color, Offset(inset.toPx(), top), Size(size.width - 2 * inset.toPx(), bottom - top),
                    androidx.compose.ui.geometry.CornerRadius(radius.toPx()))
            }
            for (r in b.quotes) {
                val top = l.getLineTop(l.getLineForOffset(toT(r.first).coerceIn(0, b.text.length)))
                val bottom = l.getLineBottom(l.getLineForOffset(toT(r.last).coerceIn(0, b.text.length)))
                drawRect(rule, Offset(left.toPx(), top), Size(Tokens.Markdown.quoteRule.toPx(), bottom - top))
            }
        }
    }

    private companion object {
        val INLINE = listOf(
            Triple(Regex("`[^`\\n]+`"), "inline", 1),
            Triple(Regex("\\*\\*[^*\\n]+\\*\\*"), "bold", 2),
            Triple(Regex("(?<![\\w*])\\*(?!\\s)[^*\\n]+(?<!\\s)\\*(?![\\w*])"), "italic", 1),
        )
    }
}
