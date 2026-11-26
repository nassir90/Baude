package dev.baude.android

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.border
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import org.json.JSONArray
import org.json.JSONObject

/** A call of Meta's as its card has it: its id (once claude's said it), its name, what it was given, how it stands and
 * what came back; the actions it took and what it showed, drawn under it. ran: its tool has run here. */
class MetaCall(val key: String, id: String?, name: String, input: JSONObject) {
    var id by mutableStateOf(id)
    var name by mutableStateOf(name)
    var input by mutableStateOf(input)
    var state by mutableStateOf("running")
    var result by mutableStateOf<String?>(null)
    var inputOpen by mutableStateOf(false)
    var outputOpen by mutableStateOf(false)
    val actions = mutableStateListOf<String>()
    val shown = mutableStateListOf<JSONObject>()
    var ran = false

    fun json(): JSONObject = JSONObject().put("key", key).put("id", id ?: JSONObject.NULL).put("name", name).put("input", input)
        .put("state", state).put("result", result ?: JSONObject.NULL).put("actions", JSONArray(actions.toList()))
        .put("shown", JSONArray(shown.toList())).put("ran", ran)

    companion object {
        fun of(o: JSONObject) = MetaCall(o.optString("key"), o.str("id"), o.optString("name"),
            o.optJSONObject("input") ?: JSONObject()).also { c ->
            c.state = o.optString("state", "done")
            c.result = o.str("result")
            c.ran = o.optBoolean("ran")
            c.actions.addAll(o.optJSONArray("actions").strings())
            c.shown.addAll(o.optJSONArray("shown").list())
        }
    }
}

/** A revert Meta makes itself: whether it's offered, and what making it does with the text that's sent. */
class OwnRevert(val can: () -> Boolean, val commit: (Reverting, String) -> Unit)

private val CLAUDE_TOOLS = listOf("Read", "Grep", "Glob", "Bash", "Edit", "Write", "WebFetch", "WebSearch")

/** A call's name as its card says it (meta.tool_title): Claude Code's as they are, Meta's own and the mesh's in words. */
fun metaToolTitle(name: String): String {
    val n = name.removePrefix("mcp__meta__").removePrefix("mcp__mesh__")
    return if (n in CLAUDE_TOOLS) n else if (n.isNotEmpty()) n.replace('_', ' ').lowercase().replaceFirstChar(Char::uppercase)
    else Wording.Meta.toolChanges
}

private fun pythonStr(v: Any?): String = when (v) {
    is Boolean -> if (v) "True" else "False"
    is JSONArray -> (0 until v.length()).joinToString(", ") { pythonStr(v.opt(it)) }
    else -> v.toString()
}

private fun firstLine(text: String) = text.lines().firstOrNull { it.isNotBlank() }.orEmpty()

/** What a call was for, in a line (meta.tool_summary). */
fun metaToolSummary(name: String, input: JSONObject): String {
    val n = name.removePrefix("mcp__meta__")
    fun s(k: String) = if (input.isNull(k)) "" else input.opt(k)?.toString().orEmpty()
    return when (n) {
        "Read", "Edit", "Write" -> s("file_path").trimEnd('/').substringAfterLast('/')
        "Grep", "Glob" -> s("pattern")
        "Bash" -> firstLine(s("command"))
        "WebFetch" -> s("url")
        "WebSearch" -> s("query")
        "run_python" -> firstLine(s("code"))
        "set_setting" -> "${s("name")} → ${saidValue(input.opt("value"))}"
        "show" -> listOf(s("what"), pythonStr(input.optJSONArray("items") ?: JSONArray())).filter { it.isNotEmpty() }.joinToString(" · ")
        else -> input.keys().asSequence().map { input.opt(it) }
            .filter { it != null && it != JSONObject.NULL && it != "" && !(it is JSONArray && it.length() == 0) }
            .joinToString(" · ") { pythonStr(it) }.take(200)
    }
}

/** A mark's colour (metalook.MARK_COLORS; highlight: the accent's). */
@Composable
fun markColor(how: String): Color = when (how) {
    "added" -> Tokens.Meta.added
    "removed" -> Tokens.Meta.removed
    "moved" -> Tokens.Meta.moved
    "archived" -> Tokens.Meta.archived
    "edited" -> Tokens.Meta.edited
    else -> LocalBaude.current.accentBg
}

/** A name highlighted as its mark is (paint_label): its colour behind it, struck through if it's gone; plain when
 * it's not live (kept, or undone) or has no mark. */
@Composable
fun markedName(name: String, how: String?, live: Boolean): AnnotatedString {
    if (how == null || !live) return AnnotatedString(name)
    val color = markColor(how).copy(alpha = Tokens.Meta.markAlpha.toFloat())
    return buildAnnotatedString {
        withStyle(SpanStyle(background = color, textDecoration = if (how == "removed") TextDecoration.LineThrough else null)) { append(name) }
    }
}

/** A batch's button: an icon, 24 square, flat. */
@Composable
fun BatchButton(icon: ImageVector, label: String, onClick: () -> Unit) {
    val c = LocalBaude.current
    Box(Modifier.size(24.dp).clip(RoundedCornerShape(6.dp)).clickable(onClick = onClick).semantics { contentDescription = label },
        contentAlignment = Alignment.Center) {
        Icon(icon, label, tint = c.fg, modifier = Modifier.size(16.dp.icon))
    }
}

/** A call's actions, one batch (batch_controls): Keep while any done one's marked, Undo for them all, Redo once
 * they're undone. */
@Composable
fun BatchControls(batch: List<Action>) {
    val done = batch.filter { !it.undone.value }
    Row(horizontalArrangement = Arrangement.spacedBy(2.dp), verticalAlignment = Alignment.CenterVertically) {
        if (done.any { !it.kept.value }) BatchButton(Adwaita.ObjectSelect, Wording.Meta.keep) { MetaTools.keep(done) }
        if (done.isNotEmpty()) BatchButton(Adwaita.EditUndo, Wording.Common.undo) { MetaTools.undoMany(batch, byUser = true) }
        else BatchButton(Adwaita.EditRedo, Wording.Common.redo) { MetaTools.redoMany(batch, byUser = true) }
    }
}

private val slide = tween<androidx.compose.ui.unit.IntSize>(120)

/** A call of Meta's, drawn (call_widget, add_shown): its card, or the box of what it changed, or its setting's row; and
 * what it showed, under it. What an earlier call of the same reply drew of a batch isn't drawn again. */
@Composable
fun MetaCallLine(key: String) {
    val call = Meta.calls[key] ?: return
    val lines = Meta.chat.lines
    val at = lines.indexOfFirst { it.kind == "metacall" && it.text == key }
    val earlier = HashSet<String>()
    var i = at - 1
    while (i >= 0 && lines[i].kind != "you") {
        if (lines[i].kind == "metacall") Meta.calls[lines[i].text]?.let { earlier.addAll(it.actions) }
        i--
    }
    val ids = call.actions.toList()
    val fresh = ids.filter { it !in earlier }
    val base = call.name.removePrefix("mcp__meta__")
    var held: JSONObject? = null
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (ids.isEmpty() || fresh.isNotEmpty()) {
            val batch = MetaTools.actions.filter { it.id in fresh }
            val spec = call.shown.firstOrNull()
            when {
                call.state != "error" && base == "set_setting" && spec != null && MetaSet.ALL.containsKey(spec.optString("setting")) -> {
                    held = spec
                    MetaSettingBox(MetaSet.ALL.getValue(spec.optString("setting")), batch, spec)
                }
                base == "show" || base == "open_form" || base == "fill_form" -> {}
                batch.any { it.marks.isNotEmpty() } -> ChangeBox(batch)
                else -> MetaToolCard(call, batch)
            }
        }
        for (spec in call.shown) if (spec !== held) MetaShown(spec)
    }
}

/** A call's card (tool_card): its name, Input and Output to open, what it was for, a spinner while it runs; red if it
 * failed or was cut short; its batch's controls. */
@Composable
fun MetaToolCard(call: MetaCall, batch: List<Action>) {
    val outer = LocalBaude.current
    val bad = call.state == "error" || call.state == "interrupted"
    val c = if (bad) outer.copy(fg = outer.destructive) else outer
    val base = call.name.removePrefix("mcp__meta__")
    val code = base == "run_python"
    val input = call.input
    val result = call.result.orEmpty()
    var summary = metaToolSummary(call.name, input)
    if (call.state == "interrupted") summary = if (summary.isNotEmpty()) Wording.Meta.callInterrupted(call = summary) else Wording.Meta.interrupted
    val undone = batch.isNotEmpty() && batch.all { it.undone.value }
    if (undone) summary = Wording.MetaActions.undone(action = summary)
    CompositionLocalProvider(LocalBaude provides c) {
        Column(Modifier.fillMaxWidth().border(1.dp, if (bad) c.destructive else c.rule, RoundedCornerShape(Gap.cardRadius))
            .padding(horizontal = Gap.cardH, vertical = Gap.cardV).then(
                if (batch.isNotEmpty()) Modifier.tip(batch.joinToString("\n") { it.said }) else Modifier)) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(metaToolTitle(call.name), style = Type.body.copy(fontWeight = Tokens.Font.bold), color = c.fg, maxLines = 1)
                if (input.length() > 0 || result.isNotEmpty()) Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    if (input.length() > 0) Toggle(if (code) Wording.Meta.code else Wording.Meta.input, call.inputOpen) {
                        call.inputOpen = !call.inputOpen
                    }
                    if (result.isNotEmpty()) Toggle(if (call.state == "error") Wording.Meta.error else Wording.Meta.output,
                        call.outputOpen) { call.outputOpen = !call.outputOpen }
                }
                Text(summary, style = Type.body, color = if (undone) c.dim else c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f))
                if (batch.isNotEmpty()) BatchControls(batch)
                if (call.state == "running") Spinner()
            }
            AnimatedVisibility(call.inputOpen && input.length() > 0, enter = expandVertically(slide), exit = shrinkVertically(slide)) {
                SelectionContainer {
                    Column(Modifier.padding(top = 6.dp)) {
                        if (code) CodeBlock(input.optString("code"), "python")
                        else CodeBlock(input.toString(1).replace("\\/", "/"), "json")
                    }
                }
            }
            AnimatedVisibility(call.outputOpen && result.isNotEmpty(), enter = expandVertically(slide), exit = shrinkVertically(slide)) {
                SelectionContainer { Column(Modifier.padding(top = 6.dp)) { CodeBlock(result, "output") } }
            }
        }
    }
}

/** The box of what a call changed in the left bar (change_box, outline): one line of what was done and its batch's
 * controls; under it, a flat item for each chat it changed, highlighted as its mark is, with its note. */
@Composable
fun ChangeBox(batch: List<Action>) {
    val c = LocalBaude.current
    Mesh.tick.value
    val caption = batch.joinToString("; ") { it.said }
    val undone = batch.all { it.undone.value }
    Column(Modifier.fillMaxWidth().border(1.dp, c.rule, RoundedCornerShape(Gap.cardRadius))
        .padding(horizontal = Gap.cardH, vertical = Gap.cardV).tip(batch.joinToString("\n") { it.said }),
        verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(if (undone) Wording.MetaActions.undone(action = caption) else caption, style = Type.body,
                color = if (undone) c.dim else c.fg, modifier = Modifier.weight(1f))
            BatchControls(batch)
        }
        for (a in batch) for (m in a.marks) {
            val key = m.optString(1)
            val h = key.substringBeforeLast('/')
            val sid = key.substringAfterLast('/')
            val s = Mesh.session(h, sid)
            val name = if (s != null) chatName(s) ?: Wording.Common.untitled else sid.take(8)
            val note = m.optString(3)
            Row(Modifier.clip(RoundedCornerShape(4.dp)).then(
                if (s != null) Modifier.clickable { Meta.opener(h, sid) } else Modifier)
                .tip(Wording.Meta.openItem(name = name)).padding(horizontal = 4.dp, vertical = 1.dp),
                horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(markedName(name, m.optString(2), !a.undone.value && !a.kept.value), style = Type.body, color = c.fg, maxLines = 1,
                    overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
                if (note.isNotEmpty()) Text(note, style = Type.body.copy(fontSize = Tokens.Font.body * Tokens.Font.small),
                    color = c.fg.copy(alpha = 0.75f))
            }
        }
    }
}

/** A setting Meta changed (setting_box): the setting's own row, in Meta's look, saying what it was, with its batch's
 * controls; changed there, Meta's told with the next message. */
@Composable
fun MetaSettingBox(s: MetaSetting, batch: List<Action>, spec: JSONObject? = null) {
    val a = batch.firstOrNull()
    val fallback = a?.let {
        if (!it.undone.value) Wording.MetaActions.was(value = saidValue(it.data.opt("from")))
        else Wording.MetaActions.setUndone(value = saidValue(it.data.opt("to")))
    }
    MetaBoxed(listOf(Unit), if (a != null) Modifier.tip(Wording.MetaActions.byMeta(action = a.said)) else Modifier) {
        val only = spec?.optJSONArray("hosts")?.let { a -> (0 until a.length()).map { a.optString(it) } }
        if (only != null && s.hostRows != null) s.hostRows.invoke(only)
        else s.row(fallback, if (batch.isEmpty()) null else ({ BatchControls(batch) }))
    }
}
