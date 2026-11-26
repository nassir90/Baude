package dev.baude.android

import android.widget.Toast
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.layout.layoutId
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.clipRect
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import org.json.JSONObject
import java.util.Locale
import kotlin.math.floor

/** A control's tooltip words: its description, and shown by a long-press (there's no hover on a phone); a tap does
 * onClick, if it has one. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun Modifier.tip(text: String, onClick: (() -> Unit)? = null): Modifier {
    val ctx = LocalContext.current
    val show = { Toast.makeText(ctx, text, Toast.LENGTH_LONG).show() }
    val tipped = this.semantics { contentDescription = text }
    return if (onClick != null) tipped.combinedClickable(onClick = onClick, onLongClick = show)
    else tipped.pointerInput(text) { detectTapGestures(onLongPress = { show() }) }
}

/** The step of dots that wait something out (WorkingDots): 1, 2, 3 and again, every working-dots duration. */
@Composable
fun dotStep(): Int {
    var n by remember { mutableStateOf(1) }
    LaunchedEffect(Unit) {
        while (true) {
            delay(Tokens.Motion.workingDotsMs.toLong())
            n = n % 3 + 1
        }
    }
    return n
}

/** A mark's n lit of three, the rest drawn see-through so what's after them doesn't move. */
fun dotsLit(mark: String, n: Int): AnnotatedString = buildAnnotatedString {
    append(mark.repeat(n))
    withStyle(SpanStyle(color = Color.Transparent)) { append(mark.repeat(3 - n)) }
}

/** A turn running: •, ••, ••• and again; nothing, and no room, when it isn't (widgets.WorkingDots). */
@Composable
fun WorkingDots(working: Boolean) {
    if (!working) return
    val c = LocalBaude.current
    val n = dotStep()
    Text(dotsLit("•", n), style = Type.caption, color = c.fg.copy(alpha = c.fg.alpha * 0.75f), maxLines = 1,
        modifier = Modifier.tip(Wording.Common.working))
}

/** The chat's context as its meter says it (chatparts.ContextUsage): the label, the bar's count and fill (none: no
 * bar), what its tooltip says. */
class ContextShown(val label: String, val text: String?, val value: Float, val tip: String, val over: Boolean)

/** The window of a model on a host, as far as it's known before its session says (meta.window): what a provider
 * says of its own, else what a session last said, else the program's. */
fun contextWindow(hostId: String, model: String?): Int? {
    val m = model?.takeIf { it != "default" } ?: defaultModel(hostId) ?: return saidWindow("default@$hostId")
    val program = program(m)
    if (program != "claude") copiesOf(hostId, m).firstOrNull { it.value == m }?.m?.window?.toInt()?.takeIf { it > 0 }
        ?.let { return it }
    saidWindow("$m@$hostId")?.let { return it }
    return when (program) {
        "claude" -> if ("[1m]" in m) 1_000_000 else 200_000
        "codex" -> 200_000
        "pi" -> 32_768
        else -> 4_096
    }
}

private fun windows(): JSONObject = try {
    JSONObject(Mesh.app.getSharedPreferences("context_windows", 0).getString("windows", null) ?: "{}")
} catch (_: Exception) { JSONObject() }

private fun saidWindow(key: String): Int? = windows().optInt(key, 0).takeIf { it > 0 }

/** A session on model (null: the host's default) said its window: kept for the next session's before it says
 * (modelslib.remember_window). */
fun rememberWindow(hostId: String, model: String?, tokens: Int) {
    val key = if (model == null || model == "default") "default@$hostId" else "$model@$hostId"
    if (tokens <= 0 || saidWindow(key) == tokens) return
    Mesh.app.getSharedPreferences("context_windows", 0).edit().putString("windows", windows().put(key, tokens).toString()).apply()
}

private fun usedOf(u: JSONObject) = if (u.has("totalTokens") && !u.isNull("totalTokens")) u.optInt("totalTokens") else null

fun contextShown(chat: Chat, model: String? = Mesh.session(chat.hostId, chat.sid)?.model?.ifBlank { null },
                 usage: JSONObject? = Mesh.session(chat.hostId, chat.sid)?.context?.json()): ContextShown {
    val at = chat.hostId
    val u = usage
    val total = u?.optInt("maxTokens", 0) ?: 0
    if (u == null) {
        val window = contextWindow(at, model)
        return ContextShown(Wording.ContextUsage.label,
            window?.let { Wording.ContextUsage.windowOnly(total = fmtTokens(it)) } ?: Wording.ContextUsage.nothing, 0f,
            window?.let { Wording.ContextUsage.windowKnown(total = it) } ?: Wording.ContextUsage.tooltip, false)
    }
    val used = usedOf(u)
    if (total <= 0) return ContextShown(
        used?.let { Wording.ContextUsage.usedNoLimit(used = fmtTokens(it)) } ?: Wording.ContextUsage.unknownNoLimit,
        null, 0f, Wording.ContextUsage.tooltip, false)
    val n = used ?: 0
    val lines = u.optJSONArray("categories").list().filter { it.optInt("tokens") > 0 && it.optString("kind") != "deferred" }
        .joinToString("\n") { "${it.optString("name")}: ${fmtTokens(it.optInt("tokens"))}" }
    return ContextShown(Wording.ContextUsage.label,
        Wording.ContextUsage.count(used = fmtTokens(n), total = fmtTokens(total)), minOf(1f, n.toFloat() / total),
        Wording.ContextUsage.usagePercent(percent = "${Math.round(n * 100.0 / total)}%", categories = lines), n > total)
}

/** Context Exhausted (show_context): the window's outgrown or used up (Codex can compact before its next turn), and
 * no turn running. */
fun exhausted(chat: Chat): Boolean {
    val model = Mesh.session(chat.hostId, chat.sid)?.model?.ifBlank { null }
    val over = contextShown(chat).over && program(model) != "codex"
    return (over || chat.chrome.outgrown.value) && !isWorking(chat)
}

/** Whether a model runs on your own machines, at no cost a token (providers.runs_free). */
private fun runsFree(model: String?): Boolean {
    val pid = model?.let(::providerOf) ?: return false
    if (pid in setOf("apple", "mlx", "ollama") || pid.startsWith("agent/")) return true
    return pid.startsWith("openai/mesh-") && Regex("mesh-[a-z0-9]{1,8}-(ollama|mlx|apple)").matches(pid.removePrefix("openai/"))
}

/** The context's word and its bar (chatparts.ContextUsage): the count drawn in the bar, in the window's colour over the
 * fill and in the text's after it; the fill the accent, amber past 60 %, red past 85 %. */
@Composable
fun ContextMeter(shown: ContextShown, full: Boolean, text: Color) {
    val c = LocalBaude.current
    Row(Modifier.tip(shown.tip), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        if (shown.text == null) Text(shown.label, style = Type.caption, color = text, maxLines = 1, overflow = TextOverflow.Ellipsis)
        val count = shown.text ?: return@Row
        val value = if (full) 1f else shown.value
        val fill = when {
            value > 0.85f -> c.error
            value > 0.6f -> c.warning
            else -> c.accentBg
        }
        val small = Type.caption.copy(fontSize = Type.caption.fontSize * 0.8f, fontFeatureSettings = "tnum")
        // (the fill's edge, and each colour's span of the count, in the bar's own width: the count's centred in it)
        fun DrawScope.edge() = floor(size.width * value)
        fun DrawScope.fillFrom() = if (layoutDirection == LayoutDirection.Rtl) size.width - edge() else 0f
        Box(Modifier.width(Tokens.Gap.contextMeter).height(Tokens.Gap.contextMeterH)
            .clip(RoundedCornerShape(Tokens.Radius.contextMeter)).background(c.fg.copy(alpha = 0.10f))
            .drawBehind { drawRect(fill, Offset(fillFrom(), 0f), Size(edge(), size.height)) },
            contentAlignment = Alignment.Center) {
            for (onFill in listOf(true, false)) Text(count, style = small, color = if (onFill) c.window else c.fg,
                maxLines = 1, overflow = TextOverflow.Ellipsis, textAlign = TextAlign.Center,
                modifier = Modifier.fillMaxWidth().drawWithContent {
                    val from = fillFrom()
                    val to = from + edge()
                    if (onFill) clipRect(left = from, right = to) { this@drawWithContent.drawContent() }
                    else {
                        clipRect(right = from) { this@drawWithContent.drawContent() }
                        clipRect(left = to) { this@drawWithContent.drawContent() }
                    }
                }.padding(horizontal = 3.dp))
        }
    }
}

/** The thin bar under a chat's box (BaudeWindow's status bar): the working dots, the chat's host, its folder and last
 * turn, the background tasks button, what it's spent, the context meter. */
@Composable
fun StatusBar(chat: Chat, openPage: (String) -> Unit) {
    val c = LocalBaude.current
    val ch = chat.chrome
    Mesh.tick.value
    val at = chat.hostId
    val session = Mesh.session(at, chat.sid)
    val working = isWorking(chat)
    val window = session?.context?.maxTokens?.toInt()
    LaunchedEffect(window, session?.model) { // (its window kept for the model's next session: rememberWindow)
        if (window != null) rememberWindow(at, session?.model?.ifBlank { null }, window)
    }
    LaunchedEffect(working) { // (a turn over: an interrupt's wait and the API's retrying let go of)
        if (!working) {
            ch.interrupting = 0
            ch.retrying.value = null
        }
    }
    val text = c.fg.copy(alpha = c.fg.alpha * 0.75f)
    val address = Mesh.hosts.firstOrNull { it.id == at }?.address
    val running = runningTasks(ch)
    val agents = running.count { it.optString("task_type") == "local_agent" }
    val procs = running.size - agents
    val model = session?.model?.ifBlank { null }
    val cost = ch.cost.value
    val shown = contextShown(chat)
    StatusRow(Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 4.dp)) {
        if (working) Box(Modifier.layoutId("dots")) { WorkingDots(working) }
        Box(Modifier.layoutId("host")) {
            Text(Mesh.label(at), style = Type.mono.copy(fontSize = Type.caption.fontSize), color = text, maxLines = 1,
                modifier = Modifier.clip(RoundedCornerShape(4.dp)).background(c.fg.copy(alpha = 0.10f)).padding(horizontal = 5.dp)
                    .tip((if (address != null) Wording.StatusBar.hostTipAt(address = address) else Wording.StatusBar.hostTip) +
                        (session?.account?.name?.let { "\n" + Wording.Subscriptions.accountLine(name = it) } ?: "")))
        }
        val cwd = session?.cwd.orEmpty()
        Box(Modifier.layoutId("folder")) {
            Text(shortPath(at, cwd) + "   " + ch.lastTurn.value, style = Type.caption, color = text, maxLines = 1,
                overflow = TextOverflow.Ellipsis, modifier = Modifier.tip(cwd))
        }
        if (running.isNotEmpty()) {
            val parts = listOfNotNull(if (procs > 0) Wording.StatusBar.processes(n = procs) else null,
                if (agents > 0) Wording.StatusBar.subagents(n = agents) else null)
            val what = running.joinToString("\n") { it.str("description") ?: it.optString("task_id") }
            Box(Modifier.layoutId("tasks")) {
                Text(Wording.StatusBar.running(tasks = parts.joinToString(" · ")),
                    style = Type.caption.copy(fontSize = Type.caption.fontSize * 0.95f), color = text, maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.padding(horizontal = 8.dp).tip(what + "\n" + Wording.StatusBar.openPanel) {
                        openPage(if (agents > 0) "agents" else "processes")
                    })
            }
        }
        Box(Modifier.layoutId("cost")) {
            Text(if (cost == 0.0 && runsFree(model)) Wording.StatusBar.costLocal
                else Wording.StatusBar.cost(cost = String.format(Locale.ROOT, "%.4f", cost)),
                style = Type.caption, color = text, maxLines = 1, modifier = Modifier.tip(Wording.StatusBar.costTip))
        }
        Box(Modifier.layoutId("context")) { ContextMeter(shown, ch.outgrown.value, text) }
    }
}

/** The status bar's row: parts 12 apart, never wider than the bar, the cost and the context meter at its right.
 * What doesn't fit gives way: the folder and the turn's figures first (they have what room is left, and none when
 * it's short), then the host, the working dots, the tasks button; the cost and the meter never do. */
@Composable
private fun StatusRow(modifier: Modifier, content: @Composable () -> Unit) {
    androidx.compose.ui.layout.Layout(content, modifier) { measurables, k ->
        val gap = 12.dp.roundToPx()
        val loose = androidx.compose.ui.unit.Constraints()
        val by = measurables.associateBy { it.layoutId as String }
        val kept = by.filterKeys { it != "folder" && it != "tasks" }.mapValues { it.value.measure(loose) }.toMutableMap()
        val tasksWide = by["tasks"]?.maxIntrinsicWidth(Int.MAX_VALUE) ?: 0 // (it shortens rather than goes)
        val count = { kept.size + if (by["tasks"] != null) 1 else 0 }
        fun width() = kept.values.sumOf { it.width } + tasksWide + gap * (count() - 1)
        for (id in listOf("host", "dots")) if (width() > k.maxWidth) kept.remove(id)
        val others = kept.values.sumOf { it.width } + gap * kept.size
        val tasks = by["tasks"]?.measure(androidx.compose.ui.unit.Constraints(
            maxWidth = minOf(tasksWide, (k.maxWidth - others).coerceAtLeast(0))))
        val room = k.maxWidth - others - (tasks?.width ?: 0) - gap * 2
        val folder = by["folder"]?.takeIf { room >= 40.dp.roundToPx() }
            ?.measure(androidx.compose.ui.unit.Constraints(maxWidth = room))
        val left = listOf("dots", "host").mapNotNull { kept[it] } + listOfNotNull(folder) + listOfNotNull(tasks)
        val right = listOfNotNull(kept["cost"], kept["context"])
        val h = (left + right).maxOfOrNull { it.height } ?: 0
        layout(k.maxWidth, h) {
            var x = 0
            for (p in left) {
                p.placeRelative(x, (h - p.height) / 2)
                x += p.width + gap
            }
            x = k.maxWidth - right.sumOf { it.width } - gap * (right.size - 1)
            for (p in right) {
                p.placeRelative(x, (h - p.height) / 2)
                x += p.width + gap
            }
        }
    }
}
