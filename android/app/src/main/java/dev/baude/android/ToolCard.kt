package dev.baude.android

import android.content.Intent
import android.graphics.BitmapFactory
import android.net.Uri
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.Image
import androidx.compose.foundation.border
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.util.Base64

private fun JSONObject.said(key: String): String = (opt(key) as? String).orEmpty()

/** A 16dp ring with an arc going round it, in the text's colour (widgets.Spinner). */
@Composable
fun Spinner() {
    val c = LocalBaude.current
    CircularProgressIndicator(Modifier.size(16.dp), color = c.fg, trackColor = c.fg.copy(alpha = 0.15f), strokeWidth = 2.dp)
}

/** A small button after a card's name (.tool-toggle): pressed while what it opens is open. */
@Composable
fun Toggle(label: String, on: Boolean, onClick: () -> Unit) {
    val c = LocalBaude.current
    Box(Modifier.clip(RoundedCornerShape(4.dp)).background(c.fg.copy(alpha = if (on) 0.30f else 0.10f))
        .clickable(role = Role.Button, onClick = onClick).padding(horizontal = 7.dp, vertical = 1.dp)) {
        Text(label, style = Type.body.copy(fontSize = Tokens.Font.body * 0.85f, lineHeight = Tokens.Font.bodyLine * 0.85f,
            fontWeight = Tokens.Font.bold), color = c.fg)
    }
}

private val slide = tween<androidx.compose.ui.unit.IntSize>(120)

/** One tool call as Baude's card has it (ToolCard): its name, toggles that open what it was given and what came back,
 * its summary and, while it runs, a spinner; under that its views, its pictures, and, asked, its permission buttons
 * (answer). A call that failed or was cut short is red all through. view: opens its subagent's page. */
@Composable
fun ToolCard(call: Call, ask: Ask?, answer: @Composable (Ask) -> Unit, view: ((Call) -> Unit)?) {
    val outer = LocalBaude.current
    val state = call.state
    val bad = state == "error" || state == "interrupted"
    val c = if (bad) outer.copy(fg = outer.destructive) else outer
    val asking = ask != null
    val input = call.input
    fun set(k: String) = input.opt(k).let { it is Number && it.toDouble() != 0.0 || it is String && it.isNotEmpty() || it == true }
    val plainRead = call.name == "Read" && !set("offset") && !set("limit") && !set("pages")
    val inputShown = !plainRead
    val outputShown = call.outputs.isNotEmpty() && (!call.subagent || state == "error")
    val inputOpen = (call.inputOpen || asking && call.base != "AskUserQuestion") && inputShown
    CompositionLocalProvider(LocalBaude provides c) {
        Column(Modifier.fillMaxWidth().border(1.dp, if (asking) c.warning else if (bad) c.destructive else c.rule,
            RoundedCornerShape(Gap.cardRadius)).padding(horizontal = Gap.cardH, vertical = Gap.cardV)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(toolTitle(call.name), Modifier.weight(1f), style = Type.body.copy(fontWeight = Tokens.Font.bold),
                    color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis)
                if (inputShown || outputShown || call.subagent) Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    if (inputShown) Toggle(when (call.base) {
                        "Bash", "BashOutput", "PowerShell" -> Wording.Common.command
                        "Task", "Agent" -> Wording.Common.prompt
                        "Edit", "MultiEdit" -> Wording.ToolCalls.diff
                        else -> Wording.Meta.input
                    }, inputOpen) { call.inputOpen = !inputOpen }
                    if (call.subagent) Toggle(Wording.Common.view, false) { view?.invoke(call) }
                    if (outputShown) Toggle(if (state == "error") Wording.Meta.error else Wording.Meta.output, call.outputOpen) {
                        call.outputOpen = !call.outputOpen
                    }
                }
                if (!asking && (state == "running" || state == "background")) Spinner()
            }
            val detail = header(call, c)
            if (detail.isNotEmpty()) SelectionContainer {
                Text(detail, style = Type.body, color = c.fg)
            }
            AnimatedVisibility(inputOpen, enter = expandVertically(slide), exit = shrinkVertically(slide)) {
                SelectionContainer {
                    Column(Modifier.fillMaxWidth().padding(top = 6.dp, bottom = 2.dp)) { InputView(call.name, input) }
                }
            }
            AnimatedVisibility(call.outputOpen && outputShown, enter = expandVertically(slide), exit = shrinkVertically(slide)) {
                SelectionContainer {
                    Column(Modifier.fillMaxWidth().padding(top = 6.dp, bottom = 2.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        for (out in call.outputs.toList()) {
                            if (out.markdown) Prose(out.text)
                            else CodeBlock(out.text, out.tag, path = if (out.numbered) input.said("file_path") else "", numbered = out.numbered)
                        }
                    }
                }
            }
            if (call.images.isNotEmpty()) Media(call.id, call.images.toList())
            if (ask != null) Box(Modifier.padding(top = 8.dp)) { answer(ask) }
        }
    }
}

/** The line after a card's toggles (ToolCard.refresh_header): what it's for, and what's become of it, dimmed after a
 * dot. */
private fun header(call: Call, c: BaudeColors): AnnotatedString {
    val said = summary(call.name, call.input)
    val dim = SpanStyle(color = c.fg.copy(alpha = c.fg.alpha * 0.55f))
    val marks = listOfNotNull(when (call.state) {
        "background" -> Wording.ToolCalls.runningInBackground
        "interrupted" -> Wording.Meta.interrupted
        else -> null
    }, if (call.decision == "denied") Wording.ToolCalls.denied else null)
    return buildAnnotatedString {
        if (call.base in setOf("Grep", "Glob") && said.isNotEmpty() &&
            said == call.input.said("pattern").trim().lines().firstOrNull().orEmpty().take(120))
            withStyle(SpanStyle(fontFamily = FontFamily.Monospace)) { append(said) }
        else append(said)
        marks.forEachIndexed { i, m ->
            append(if (i == 0 && said.isEmpty()) "" else "  ")
            withStyle(dim) { append("· $m") }
        }
    }
}

/** A picture decoded (bmp null: it can't be). */
private class Pic(val bmp: ImageBitmap?)

private val pics = android.util.LruCache<String, Pic>(24)
private val sizes = android.util.LruCache<String, IntArray>(256)

/** A picture's size from the head of its base64 (its header), [0, 0] if it isn't one. */
private fun sizeOf(data: String): IntArray = sizes.get(data) ?: run {
    val o = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    try {
        val head = Base64.getDecoder().decode(data.take(65536))
        BitmapFactory.decodeByteArray(head, 0, head.size, o)
    } catch (_: Exception) {}
    intArrayOf(maxOf(o.outWidth, 0), maxOf(o.outHeight, 0)).also { sizes.put(data, it) }
}

/** The pictures a call returned, right under its line (6dp below), each as it is up to 480dp on its long side: its
 * place kept from its header before it's decoded (once, off the main thread, to the size shown). */
@Composable
private fun Media(id: String, pictures: List<String>) {
    val density = androidx.compose.ui.platform.LocalDensity.current.density
    Column(Modifier.fillMaxWidth().padding(top = 6.dp, bottom = 2.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        for ((index, data) in pictures.withIndex()) {
            val (w, h) = sizeOf(data)
            val got by produceState<Pic?>(pics.get(data), data) {
                if (value != null) return@produceState
                value = withContext(Dispatchers.Default) {
                    val bytes = try { Base64.getDecoder().decode(data) } catch (_: Exception) { ByteArray(0) }
                    val o = BitmapFactory.Options().apply {
                        var n = 1
                        while (maxOf(w, h) / (n * 2) >= 480 * density) n *= 2
                        inSampleSize = n
                    }
                    Pic(BitmapFactory.decodeByteArray(bytes, 0, bytes.size, o)?.asImageBitmap()).also { pics.put(data, it) }
                }
            }
            val bitmap = got?.bmp
            if (w > 0 && h > 0) {
                val k = minOf(1f, 480f / maxOf(w, h))
                ImageTouch("call:$id:$index", Modifier.widthIn(max = (w * k).dp).aspectRatio(w / h.toFloat()).clip(RoundedCornerShape(6.dp))) {
                    if (bitmap != null) Image(bitmap, null, Modifier.fillMaxSize())
                }
            }
            if (got != null && bitmap == null) SelectionContainer { Text(Wording.ToolCalls.imageNotDecoded, style = Type.body, color = LocalBaude.current.fg) }
        }
    }
}

// --------------------------------------------------------------------------------------------------- input views --

/** A call's input as its card shows it (tool_input_view): by kind of tool, never as raw JSON. */
@Composable
fun InputView(name: String, input: JSONObject) {
    val c = LocalBaude.current
    if (input.length() == 0) return
    val base = name.substringAfterLast("__")
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        when {
            base in setOf("Bash", "BashOutput", "PowerShell") && input.has("command") -> {
                val shell = if (base == "PowerShell") "powershell" else "bash"
                CodeBlock(input.said("command"), shell, syntax = shell)
                KvRows(input, setOf("command", "description"))
            }
            base == "Codex file changes" -> {
                for (change in input.optJSONArray("changes").objects()) {
                    PathLabel(change.said("path"))
                    CodeBlock(change.said("diff"), "diff")
                }
                KvRows(input, setOf("changes"))
            }
            base == "Edit" && input.has("file_path") -> {
                PathLabel(input.said("file_path"), if (input.optBoolean("replace_all")) Wording.ToolCalls.allOccurrences else "")
                Hunk(input.said("old_string"), input.said("new_string"), input.said("file_path"))
            }
            base == "MultiEdit" && input.has("file_path") -> {
                val edits = input.optJSONArray("edits").objects()
                PathLabel(input.said("file_path"), Wording.ToolCalls.edits(n = edits.size))
                for (e in edits) Hunk(e.said("old_string"), e.said("new_string"), input.said("file_path"))
            }
            base == "Write" && input.has("file_path") -> {
                val path = input.said("file_path")
                val (count, shown) = remember(input) {
                    val lines = splitLines(input.said("content"))
                    lines.size to lines.take(2000).joinToString("\n") +
                        if (lines.size > 2000) "\n" + Wording.ToolCalls.moreLines(n = lines.size - 2000) else ""
                }
                Text(Wording.ToolCalls.lineCount(n = count), style = Type.caption, color = LocalBaude.current.fg)
                CodeBlock(shown, LANG_BY_EXT[path.substringAfterLast('.', "").let { if (it.isEmpty()) "" else ".$it" }].orEmpty(), path = path)
            }
            base == "Read" && input.has("file_path") -> {
                val offset = (input.opt("offset") as? Number)?.toInt() ?: 0
                val limit = (input.opt("limit") as? Number)?.toInt() ?: 0
                val span = if (offset != 0 || limit != 0) {
                    val start = if (offset == 0) 1 else offset
                    if (limit != 0) Wording.CodeView.lines(start = start, end = start + limit - 1)
                    else Wording.ToolCalls.fromLine(start = start)
                } else ""
                PathLabel(input.said("file_path"), span)
                KvRows(input, setOf("file_path", "offset", "limit"))
            }
            base in setOf("Grep", "Glob") && input.has("pattern") -> {
                val grep = base == "Grep"
                CodeBlock(input.said("pattern"), if (grep) "regex" else "glob", syntax = if (grep) "regex" else null)
                KvRows(input, setOf("pattern"))
            }
            base == "WebFetch" && input.has("url") -> {
                val ctx = LocalContext.current
                val url = input.said("url")
                Text(url, style = Type.body, color = c.accent, textDecoration = TextDecoration.Underline,
                    modifier = Modifier.clickable {
                        try { ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
                        catch (_: Exception) {}
                    })
                if (input.said("prompt").isNotEmpty()) Prose(input.said("prompt"))
            }
            base in setOf("Task", "Agent") && input.has("prompt") -> {
                Prose(input.said("prompt"))
                KvRows(input, setOf("prompt", "description"))
            }
            base == "AskUserQuestion" && input.optJSONArray("questions") != null -> {
                val answers = input.optJSONObject("answers") ?: JSONObject()
                for (q in input.optJSONArray("questions").objects()) {
                    Text(q.said("question"), style = Type.body.copy(fontWeight = Tokens.Font.bold), color = c.fg)
                    val options = q.optJSONArray("options")
                    for (i in 0 until (options?.length() ?: 0)) {
                        val o = options!!.get(i)
                        val label = if (o is JSONObject) o.optString("label") else o.toString()
                        val about = (o as? JSONObject)?.said("description").orEmpty()
                        Text(buildAnnotatedString {
                            append("  •  $label")
                            if (about.isNotEmpty()) withStyle(SpanStyle(color = c.fg.copy(alpha = c.fg.alpha * 0.6f))) { append("  $about") }
                        }, style = Type.body, color = c.fg)
                    }
                    if (answers.has(q.said("question"))) Text("  →  ${answers.opt(q.said("question"))}", style = Type.body, color = c.fg)
                }
            }
            base == "TodoWrite" && input.optJSONArray("todos") != null -> {
                for (t in input.optJSONArray("todos").objects()) {
                    val done = t.said("status") == "completed"
                    val mark = when (t.said("status")) { "completed" -> "☑"; "in_progress" -> "◐"; else -> "☐" }
                    val text = t.said("content").ifEmpty { t.said("activeForm") }
                    Text(buildAnnotatedString {
                        append("$mark  ")
                        if (done) withStyle(SpanStyle(textDecoration = TextDecoration.LineThrough)) { append(text) } else append(text)
                    }, style = Type.body, color = c.fg)
                }
            }
            base == "ToolSearch" && McpNames.selected(input.said("query")) != null -> {
                for (n in McpNames.selected(input.said("query")).orEmpty()) Text(toolTitle(n), style = Type.body, color = c.fg)
                KvRows(input, setOf("query"))
            }
            else -> KvRows(input)
        }
    }
}

private fun JSONArray?.objects(): List<JSONObject> =
    if (this == null) emptyList() else (0 until length()).mapNotNull { optJSONObject(it) }

/** An edit's change: its old lines on red, then its new on green, coloured as the file's code. */
@Composable
private fun Hunk(old: String, new: String, path: String) = CodeBlock(new, path = path, diff = diffBlock(old, new))

/** A path in monospace, and after it, dimmed, what's said of it (PathLabel). */
@Composable
fun PathLabel(path: String, extra: String = "") {
    val c = LocalBaude.current
    Text(buildAnnotatedString {
        withStyle(SpanStyle(fontFamily = FontFamily.Monospace, color = c.fg.copy(alpha = c.fg.alpha * 0.85f))) { append(path) }
        if (extra.isNotEmpty()) withStyle(SpanStyle(color = c.fg.copy(alpha = c.fg.alpha * 0.6f))) { append("  $extra") }
    }, style = Type.body, color = c.fg)
}

private fun blank(v: Any?) = v == null || v === JSONObject.NULL || v == "" ||
    v is JSONArray && v.length() == 0 || v is JSONObject && v.length() == 0

private fun rowsOf(obj: JSONObject, skip: Set<String>): List<Pair<String, Any>> =
    obj.keys().asSequence().filter { it !in skip }.mapNotNull { k -> obj.opt(k)?.takeUnless(::blank)?.let { k to it } }.toList()

/** An object's fields as labelled rows (_kv_rows): the key's words dimmed at the left, the value beside it; none if
 * it has nothing to show. */
@Composable
fun KvRows(obj: JSONObject, skip: Set<String> = emptySet(), depth: Int = 0) {
    val rows = rowsOf(obj, skip)
    if (rows.isNotEmpty()) KvGrid(rows, depth)
}

@Composable
private fun KvGrid(rows: List<Pair<String, Any>>, depth: Int) {
    val c = LocalBaude.current
    val density = androidx.compose.ui.platform.LocalDensity.current
    val gap = with(density) { 12.dp.roundToPx() }
    val rowGap = with(density) { 4.dp.roundToPx() }
    Layout(content = {
        for ((k, v) in rows) {
            Text(humanize(k), style = Type.body, color = c.dim)
            Box { ValueView(v, depth) }
        }
    }, modifier = Modifier.fillMaxWidth()) { measurables, constraints ->
        val loose = Constraints(maxWidth = constraints.maxWidth)
        val keys = measurables.filterIndexed { i, _ -> i % 2 == 0 }.map { it.measure(loose) }
        val keyW = keys.maxOf { it.width }
        val valueW = (constraints.maxWidth - keyW - gap).coerceAtLeast(0)
        val values = measurables.filterIndexed { i, _ -> i % 2 == 1 }.map { it.measure(Constraints(maxWidth = valueW)) }
        val heights = keys.indices.map { maxOf(keys[it].height, values[it].height) }
        layout(constraints.maxWidth, heights.sum() + rowGap * (rows.size - 1)) {
            var y = 0
            for (i in keys.indices) {
                keys[i].placeRelative(keyW - keys[i].width, y)
                values[i].placeRelative(keyW + gap, y)
                y += heights[i] + rowGap
            }
        }
    }
}

/** A readable view of any JSON value (_value_widget): nested objects become rows. */
@Composable
private fun ValueView(v: Any, depth: Int) {
    val c = LocalBaude.current
    fun scalar(x: Any?) = x is String || x is Number || x is Boolean
    fun text(x: Any) = if (x is Boolean) (if (x) "True" else "False") else x.toString()
    val items = (v as? JSONArray)?.let { a -> (0 until a.length()).map { a.get(it) } }
    when {
        v is String && "\n" in v -> CodeBlock(v)
        v is Boolean -> Text(if (v) Wording.ToolCalls.yes else Wording.ToolCalls.no, style = Type.body, color = c.fg)
        v is String || v is Number -> Text(v.toString(), style = Type.body, color = c.fg)
        items != null && items.all(::scalar) -> Text(items.joinToString(", ") { text(it) }, style = Type.body, color = c.fg)
        items != null && items.all { it is JSONObject && it.length() == 1 } ->
            Text(items.joinToString(", ") { o -> (o as JSONObject).let { text(it.opt(it.keys().next())) } }, style = Type.body, color = c.fg)
        v is JSONObject && depth < 3 -> rowsOf(v, emptySet()).let { rows ->
            if (rows.isEmpty()) Text("—", style = Type.body, color = c.fg) else KvGrid(rows, depth + 1)
        }
        items != null && depth < 3 -> Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            for (x in items) Box { ValueView(x, depth + 1) }
        }
        else -> Text(pyJson(v), style = Type.body.copy(fontFamily = FontFamily.Monospace), color = c.fg)
    }
}

/** A value as Python's json.dumps(v, ensure_ascii=False) writes it. */
private fun pyJson(v: Any?): String = when (v) {
    null, JSONObject.NULL -> "null"
    is String -> buildString {
        append('"')
        for (ch in v) when {
            ch == '"' -> append("\\\"")
            ch == '\\' -> append("\\\\")
            ch == '\n' -> append("\\n")
            ch == '\r' -> append("\\r")
            ch == '\t' -> append("\\t")
            ch == '\b' -> append("\\b")
            ch == '\u000c' -> append("\\f")
            ch < ' ' -> append(String.format("\\u%04x", ch.code))
            else -> append(ch)
        }
        append('"')
    }
    is JSONObject -> v.keys().asSequence().joinToString(", ", "{", "}") { pyJson(it) + ": " + pyJson(v.opt(it)) }
    is JSONArray -> (0 until v.length()).joinToString(", ", "[", "]") { pyJson(v.get(it)) }
    else -> v.toString()
}

// -------------------------------------------------------------------------------------------------- group & lines --

/** Several calls in a row folded into a line, › 5 tool calls, 1 failed (a spinner after it while one runs); opened,
 * their cards under it (ToolGroup). It opens itself for a call with pictures or one asking you. */
@Composable
fun ToolGroup(group: List<Call>, calls: ChatCalls, asking: (Call) -> Ask?, answer: @Composable (Ask) -> Unit, view: ((Call) -> Unit)?) {
    val c = LocalBaude.current
    val key = group.first().id
    val open = calls.opened[key] == true
    val why = group.filter { it.images.isNotEmpty() || asking(it) != null }.joinToString { it.id + if (asking(it) != null) "?" else "" }
    LaunchedEffect(why) { // (opened once for each call that has pictures or asks; folded by you, it stays so)
        if (why.isNotEmpty() && calls.autoOpened[key] != why) {
            calls.autoOpened[key] = why
            calls.opened[key] = true
        }
    }
    val failed = group.count { it.state == "error" }
    val cut = group.count { it.state == "interrupted" }
    val running = group.any { asking(it) == null && (it.state == "running" || it.state == "background") }
    Column(Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
        Row(Modifier.clickable { calls.opened[key] = !open }.padding(vertical = 2.dp), horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically) {
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp), verticalAlignment = Alignment.CenterVertically) {
                Icon(if (open) Adwaita.PanDown else Adwaita.PanEnd, null, tint = c.fg.copy(alpha = 0.75f), modifier = Modifier.size(16.dp.icon))
                Text(Wording.ToolCalls.count(n = group.size) + (if (failed > 0) Wording.ToolCalls.failed(n = failed) else "") +
                    (if (cut > 0) Wording.ToolCalls.interrupted(n = cut) else ""), style = Type.body, color = c.fg.copy(alpha = 0.8f))
            }
            if (running) Spinner()
        }
        AnimatedVisibility(open, enter = expandVertically(slide), exit = shrinkVertically(slide)) {
            Column(Modifier.fillMaxWidth().padding(top = 4.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                for (call in group) ToolCard(call, asking(call), answer, view)
            }
        }
    }
}

/** The ending of a background task whose call isn't drawn (task_note_line): what it was, which, how it ended. */
@Composable
fun TaskNoteLine(json: String) {
    val c = LocalBaude.current
    val said = taskSaid(JSONObject(json))
    SelectionContainer {
        Text(buildAnnotatedString {
            withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { append(said.what) }
            append(" · ${said.desc} — ${said.ending}")
        }, style = Type.body, color = if (said.failed) c.destructive else c.dim, modifier = Modifier.fillMaxWidth())
    }
}

/** A chat's lines as items: runs of two or more calls as a group, a lone call as a card, every other line as line
 * draws it. A mesh call's card isn't grouped; it ends a run. asking: the permission it waits on, drawn with answer. */
fun LazyListScope.drawn(lines: List<Line>, calls: ChatCalls, asking: (Call) -> Ask? = { null }, answer: @Composable (Ask) -> Unit = {},
                        view: ((Call) -> Unit)? = null, line: @Composable (Line) -> Unit) {
    var i = 0
    val keyed = HashSet<String>()
    while (i < lines.size) {
        val l = lines[i]
        val call = if (l.kind == "call") calls.byId[l.text] else null
        if (call == null) {
            item(key = summaryKey(l)?.takeIf { keyed.add(it) }) { line(l) } // (a summary keyed, once: SummaryPin)
            i++
            continue
        }
        if (call.name in MESH_CHAT_TOOLS) {
            item { ToolCard(call, asking(call), answer, view) }
            i++
            continue
        }
        val run = ArrayList<Call>()
        while (i < lines.size && lines[i].kind == "call") {
            val next = calls.byId[lines[i].text] ?: break
            if (next.name in MESH_CHAT_TOOLS) break
            run.add(next)
            i++
        }
        if (run.size >= 2) item { ToolGroup(run, calls, asking, answer, view) } else item { ToolCard(run[0], asking(run[0]), answer, view) }
    }
}

/** Where call id is drawn, as drawn counts items: its item's index, and the group's key (as ToolGroup keys
 * calls.opened) when that item is a group. */
fun itemOf(lines: List<Line>, calls: ChatCalls, id: String): Pair<Int, String?>? {
    var item = 0
    var i = 0
    while (i < lines.size) {
        val call = if (lines[i].kind == "call") calls.byId[lines[i].text] else null
        if (call == null) {
            item++
            i++
            continue
        }
        if (call.name in MESH_CHAT_TOOLS) {
            if (call.id == id) return item to null
            item++
            i++
            continue
        }
        val run = ArrayList<Call>()
        while (i < lines.size && lines[i].kind == "call") {
            val next = calls.byId[lines[i].text] ?: break
            if (next.name in MESH_CHAT_TOOLS) break
            run.add(next)
            i++
        }
        if (run.any { it.id == id }) return item to (if (run.size >= 2) run.first().id else null)
        item++
    }
    return null
}
