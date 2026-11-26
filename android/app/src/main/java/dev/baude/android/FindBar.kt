package dev.baude.android

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.isShiftPressed
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp

/** Find in a chat (chatparts.FindBar): its bar over the transcript, shown by a chat's ⋮ menu; its matches shaded. */
object Find {
    val open = mutableStateMapOf<String, Boolean>()
    val query = mutableStateMapOf<String, String>()
    val at = mutableStateMapOf<String, Int>()
    const val MOST = 2000

    fun key(chat: Chat) = "${chat.hostId}/${chat.sid}"
}

/** What's being found in the chat drawn (empty: nothing), the line that has the match stepped to, and the line being
 * drawn. */
val LocalFind = compositionLocalOf { "" }
val LocalFindCurrent = compositionLocalOf<Line?> { null }
val LocalFindLine = compositionLocalOf<Line?> { null }

/** Where a match is: the line it's in. */
class Match(val line: Line, val lineIndex: Int)

private fun texts(chat: Chat, line: Line): List<String> = when (line.kind) {
    "you", "bot", "note", "error" -> listOf(line.text)
    "gitnote" -> listOf(runCatching { org.json.JSONObject(line.text).optString("text") }.getOrDefault(""))
    "call" -> chat.calls.byId[line.text]?.let { call ->
        (if (call.inputOpen) listOf(call.input.toString()) else emptyList()) + (if (call.outputOpen) call.outputs.map { it.text } else emptyList())
    }.orEmpty()
    else -> emptyList()
}

/** Every place the query is, in the order it stands (any case), at most MOST. */
fun findMatches(chat: Chat, query: String): List<Match> {
    if (query.isBlank()) return emptyList()
    val out = ArrayList<Match>()
    for ((i, line) in chat.lines.withIndex()) for (t in texts(chat, line)) {
        var from = 0
        while (out.size < Find.MOST) {
            val j = t.indexOf(query, from, ignoreCase = true)
            if (j < 0) break
            out.add(Match(line, i))
            from = j + query.length
        }
    }
    return out
}

/** The rows a chat's list has before its lines: the spinner, while there's more before them to read (Mesh.readEarlier). */
fun earlierRows(chat: Chat): Int = if (chat.earlier.value) 1 else 0

/** The list item a line is drawn in (drawn: a run of calls is one). */
fun itemIndex(chat: Chat, lineIndex: Int): Int {
    var items = 0
    var i = 0
    val lines = chat.lines
    while (i < lines.size && i <= lineIndex) {
        val call = if (lines[i].kind == "call") chat.calls.byId[lines[i].text] else null
        if (call == null || call.name in MESH_CHAT_TOOLS) { items++; i++; continue }
        items++
        while (i < lines.size && lines[i].kind == "call" && chat.calls.byId[lines[i].text]?.let { it.name !in MESH_CHAT_TOOLS } == true) i++
    }
    return items - 1
}

/** A text with what's being found in it shaded: every match, the stepped-to line's more. */
@Composable
fun AnnotatedString.found(): AnnotatedString {
    val q = LocalFind.current
    if (q.isBlank()) return this
    val c = LocalBaude.current
    val current = LocalFindCurrent.current != null && LocalFindCurrent.current === LocalFindLine.current
    val b = AnnotatedString.Builder(this)
    var from = 0
    while (true) {
        val j = text.indexOf(q, from, ignoreCase = true)
        if (j < 0) break
        b.addStyle(SpanStyle(background = c.accent.copy(alpha = if (current) 0.5f else 0.25f)), j, j + q.length)
        from = j + q.length
    }
    return b.toAnnotatedString()
}

/** The bar: a field, how many and which, previous and next, and close. */
@Composable
fun FindBar(chat: Chat, matches: List<Match>, modifier: Modifier = Modifier) {
    val c = LocalBaude.current
    val key = Find.key(chat)
    val q = Find.query[key].orEmpty()
    val at = (Find.at[key] ?: 0).coerceIn(0, maxOf(0, matches.size - 1))
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { focus.requestFocus() }
    fun step(d: Int) { if (matches.isNotEmpty()) Find.at[key] = (at + d + matches.size) % matches.size }
    fun close() { Find.open[key] = false }
    androidx.activity.compose.BackHandler { close() }
    Row(modifier.fillMaxWidth().background(c.frame).padding(6.dp), horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.weight(1f).background(c.fg.copy(alpha = 0.08f), androidx.compose.foundation.shape.RoundedCornerShape(8.dp))
            .padding(horizontal = 10.dp, vertical = 8.dp)) {
            if (q.isEmpty()) Text(Wording.Find.placeholder, style = Type.body, color = c.dim)
            BasicTextField(q, { Find.query[key] = it; Find.at[key] = 0 }, singleLine = true, textStyle = Type.body.copy(color = c.fg),
                cursorBrush = SolidColor(c.accentBg), keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                keyboardActions = KeyboardActions(onSearch = { step(1) }),
                modifier = Modifier.fillMaxWidth().focusRequester(focus).onPreviewKeyEvent { e ->
                    if (e.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
                    when (e.key) {
                        Key.Enter -> { step(if (e.isShiftPressed) -1 else 1); true }
                        Key.Escape -> { close(); true }
                        else -> false
                    }
                })
        }
        val count = when {
            q.isBlank() -> ""
            matches.isEmpty() -> Wording.Find.noMatches
            matches.size >= Find.MOST -> Wording.Find.matchOfMore(at = at + 1, total = matches.size)
            else -> Wording.Find.matchOf(at = at + 1, total = matches.size)
        }
        Text(count, style = Type.caption, color = c.dim, maxLines = 1)
        IconButton(Adwaita.GoUp, Wording.Find.previous) { step(-1) }
        IconButton(Adwaita.GoDown, Wording.Find.next) { step(1) }
        IconButton(Adwaita.WindowClose, Wording.Images.close) { close() }
    }
}
