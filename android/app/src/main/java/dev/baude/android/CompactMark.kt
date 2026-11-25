package dev.baude.android

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.layout.onPlaced
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.layout.positionInParent
import kotlinx.coroutines.launch
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import org.json.JSONObject

/** Which compaction marks have their summary opened, by the mark's uuid (chatparts.CompactMark's fold), and where each
 * one's heading is in its item, in px (SummaryPin). */
object CompactFolds {
    val open = mutableStateMapOf<String, Boolean>()
    val headTop = HashMap<String, Float>()
}

private val TOOLS_BEFORE = Regex("""<tools-used-before-summary>(.*?)</tools-used-before-summary>""", RegexOption.DOT_MATCHES_ALL)
private val EMPTY_REMINDER = Regex("""<system-reminder>\s*</system-reminder>""")
private val BLANKS = Regex("""\n{3,}""")

/** A compaction's summary as it's read (transcripts.summary_tools): its text without the list of tools the
 * conversation had used that Claude Code puts in it (only that block, out of its system-reminder), and those tools' names. */
fun summaryTools(text: String): Pair<String, List<String>> {
    val names = ArrayList<String>()
    val rest = TOOLS_BEFORE.replace(text) { m ->
        names.addAll(m.groupValues[1].split(",").map { it.trim() }.filter { it.isNotEmpty() })
        ""
    }
    return BLANKS.replace(EMPTY_REMINDER.replace(rest, ""), "\n\n").trim() to names
}

/** The lazy item key of a line whose summary can be opened (drawn keys it so SummaryPin finds it), or null. */
fun summaryKey(line: Line): String? = line.uuid?.takeIf {
    line.kind == "compactsum" || (line.kind == "compact" && line.text.contains("\"summary\""))
}?.let { "summary:$it" }

/** The compaction marks in a chat's lines: a "compact" line's JSON is {meta, summary?} once its transcript says it, or
 * {live: true, state: running | done | failed | unfinished, ...} while claude's stream does
 * (chatparts.compact_said). */
object CompactMarks {
    private fun live(line: Line): Boolean = line.kind == "compact" && try {
        JSONObject(line.text).optBoolean("live")
    } catch (_: Exception) { false }

    private fun liveAt(chat: Chat): Int = chat.lines.indexOfLast { live(it) }

    /** A line taken out of the chat's lines, the ends after it one nearer. */
    private fun remove(chat: Chat, i: Int) {
        chat.lines.removeAt(i)
        chat.ends.replaceAll { _, v -> if (v > i) v - 1 else v }
    }

    /** The marks claude's stream drew, gone: its transcript's has come. */
    fun dropLive(chat: Chat) {
        while (true) remove(chat, liveAt(chat).takeIf { it >= 0 } ?: return)
    }

    /** The summary a compaction wrote, under the mark before it that has none; else on its own. */
    fun addSummary(chat: Chat, line: Line) {
        val i = chat.lines.indexOfLast { it.kind != "note" }
        val last = chat.lines.getOrNull(i)
        if (last != null && last.kind == "compact" && !live(last)) {
            val o = JSONObject(last.text)
            if (!o.has("summary")) {
                chat.lines[i] = last.copy(text = o.put("summary", line.text).toString())
                return
            }
        }
        chat.lines.add(line)
    }

    private fun set(chat: Chat, state: JSONObject) {
        val i = liveAt(chat)
        val line = Line("compact", state.put("live", true).toString())
        if (i >= 0) chat.lines[i] = line else chat.lines.add(line)
    }

    /** What claude's stream says of a compaction (compact_said): a status, or its boundary. */
    fun heard(chat: Chat, m: JSONObject) {
        val sub = m.optString("subtype")
        if (sub == "compact_boundary") {
            set(chat, JSONObject().put("state", "done").put("meta", m.optJSONObject("compact_metadata") ?: JSONObject()))
            return
        }
        if (sub != "status") return
        if (m.optString("status") == "compacting") {
            val n = (m.opt("compact_output_chars") as? Number)?.toInt()
            val o = JSONObject().put("state", "running")
            if (n != null) o.put("written", n)
            set(chat, o)
            return
        }
        if (!m.isNull("status")) return
        when (m.optString("compact_result")) {
            "failed" -> set(chat, JSONObject().put("state", "failed").put("error", m.opt("compact_error").takeIf { it is String }))
            "success" -> {}
            else -> if (!m.has("permissionMode")) liveAt(chat).takeIf { it >= 0 }?.let {
                if (JSONObject(chat.lines[it].text).optString("state") == "running") remove(chat, it)
            }
        }
    }

    /** The turn ended: a compaction still running didn't finish (compact_ended). */
    fun turnEnded(chat: Chat) {
        val i = liveAt(chat)
        if (i >= 0 && JSONObject(chat.lines[i].text).optString("state") == "running")
            chat.lines[i] = Line("compact", JSONObject().put("live", true).put("state", "unfinished").toString())
    }
}

/** Where claude compacted the conversation (chatparts.CompactMark): a quiet line between two rules, what it did said in
 * it (or that it's running, failed or didn't finish), and the summary it wrote folded under it. */
@Composable
fun CompactMark(line: Line) {
    val c = LocalBaude.current
    val o = try { JSONObject(line.text) } catch (_: Exception) { JSONObject() }
    val state = if (o.optBoolean("live")) o.optString("state") else "done"
    val bad = state == "failed" || state == "unfinished"
    val text = when (state) {
        "running" -> o.optInt("written", 0).let { if (it > 0) Wording.Compaction.runningWritten(written = it) else Wording.Compaction.running }
        "failed" -> o.optString("error").ifBlank { null }?.let { Wording.Compaction.failedBecause(error = it) } ?: Wording.Compaction.failed
        "unfinished" -> Wording.Compaction.unfinished
        else -> compactText(o.optJSONObject("meta"))
    }
    Column(Modifier.fillMaxWidth().padding(vertical = Tokens.Compact.margin), verticalArrangement = Arrangement.spacedBy(2.dp)) {
        androidx.compose.foundation.layout.BoxWithConstraints(Modifier.fillMaxWidth()) {
            val most = maxWidth * 0.75f
            Row(Modifier.fillMaxWidth().semantics { contentDescription = Wording.Compaction.tooltip },
                horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
                HorizontalDivider(Modifier.weight(1f), color = c.rule)
                Row(Modifier.widthIn(max = most), verticalAlignment = Alignment.CenterVertically) {
                    SelectionContainer(Modifier.weight(1f, fill = false)) {
                        Text(text, style = Type.caption, color = if (bad) c.destructive else c.dim, textAlign = TextAlign.Center)
                    }
                    if (state == "running") Row(Modifier.padding(start = 2.dp)) { WorkingDots(true) }
                }
                HorizontalDivider(Modifier.weight(1f), color = c.rule)
            }
        }
        o.optString("summary").ifBlank { null }?.let { SummaryFold(it, line.uuid ?: "") }
    }
}

/** A summary a compaction wrote, with no mark of its own before it. */
@Composable
fun LoneSummary(line: Line) {
    Column(Modifier.fillMaxWidth().padding(vertical = Tokens.Compact.margin)) { SummaryFold(line.text, line.uuid ?: "") }
}

/** The heading that opens a summary (Gtk.Expander: its arrow and "Summary of the earlier conversation"), folded to start
 * with; opened, its text as a reply's. */
@Composable
private fun SummaryFold(summary: String, key: String) {
    val c = LocalBaude.current
    val open = CompactFolds.open[key] == true
    Column(Modifier.fillMaxWidth().onPlaced { CompactFolds.headTop[key] = it.positionInParent().y }) {
        SummaryHeading(open) { CompactFolds.open[key] = !open }
        if (open) {
            val (text, tools) = remember(summary) { summaryTools(summary) }
            Column(Modifier.padding(start = Tokens.Compact.indent, bottom = 6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                if (tools.isNotEmpty()) SelectionContainer {
                    Text(Wording.Compaction.toolsBefore(count = tools.size), style = Type.caption, color = c.dim)
                }
                Prose(text)
            }
        }
    }
}

/** A summary's heading (Gtk.Expander's): its arrow and "Summary of the earlier conversation". */
@Composable
private fun SummaryHeading(open: Boolean, modifier: Modifier = Modifier, toggle: () -> Unit) {
    val c = LocalBaude.current
    Row(modifier.clickable { toggle() }, verticalAlignment = Alignment.CenterVertically) {
        Icon(if (open) Adwaita.PanDown else Adwaita.PanEnd, null, tint = c.dim, modifier = Modifier.size(16.dp.icon))
        Text(Wording.Compaction.summary, style = Type.caption, color = c.dim)
    }
}

/** An open summary's heading pinned at the top of the chat (chatparts.SummaryPin) while its own heading's scrolled
 * above the top and the summary's still in view; tapped, the summary folds and the chat's scrolled back to its mark.
 * start: the chat's side padding, where its lines start. */
@Composable
fun SummaryPin(list: LazyListState, start: androidx.compose.ui.unit.Dp, modifier: Modifier = Modifier) {
    val c = LocalBaude.current
    val scope = rememberCoroutineScope()
    var height by remember { mutableStateOf(0) }
    val pinned by remember(list) {
        derivedStateOf {
            val info = list.layoutInfo
            info.visibleItemsInfo.firstNotNullOfOrNull { item ->
                val uuid = (item.key as? String)?.removePrefix("summary:")?.takeIf { it != item.key } ?: return@firstNotNullOfOrNull null
                if (CompactFolds.open[uuid] != true) return@firstNotNullOfOrNull null
                val top = item.offset + info.beforeContentPadding // (where the item's drawn in the list)
                val head = top + (CompactFolds.headTop[uuid] ?: 0f)
                if (head < 0 && top + item.size > maxOf(height, 1)) uuid to item.index else null
            }
        }
    }
    val (uuid, index) = pinned ?: return
    Column(modifier.fillMaxWidth().background(c.inside).onSizeChanged { height = it.height }) {
        SummaryHeading(true, Modifier.padding(start = start, top = 2.dp, bottom = 4.dp)) {
            CompactFolds.open[uuid] = false
            scope.launch { list.scrollToItem(index) } // (back to its mark, at the chat's top)
        }
        HorizontalDivider(color = c.rule)
    }
}
