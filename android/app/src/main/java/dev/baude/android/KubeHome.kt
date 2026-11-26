package dev.baude.android

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipRect
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.layout
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.abs
import kotlin.math.roundToInt

/** Home (kubehome.KubeHome): the whole stack's CPU and memory as the host added them up: a card each, Requested, Used
 * and Limits a bar each against the most of them and what the nodes can give pods, how they went under them; then the
 * same by namespace (a namespace's name a link: only its objects), and by node on a cluster of more than one, each
 * table sorted by a column tapped, its first ten and an "Other (n)" row of the rest. */
@Composable
fun KubeHome(st: KubeState, s: Msg.KubeSummary) {
    val history = st.history
    val minutes = s.minutes?.toInt() ?: 0
    val every = s.every?.toDouble() ?: 10.0
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)) {
        val at = history.map { h -> h.at?.toDouble() ?: 0.0 }
        s.cpu?.let { KubeCard(it, KubeSamples(at, history.map { h -> h.cpuUsed?.toDouble() }, history.map { h -> h.cpuRequests?.toDouble() }, minutes, every), memory = false) }
        s.memory?.let { KubeCard(it, KubeSamples(at, history.map { h -> h.memoryUsed?.toDouble() }, history.map { h -> h.memoryRequests?.toDouble() }, minutes, every), memory = true) }
        val sortedBy = s.sortedBy?.toInt()
        val shown = s.shown?.toInt() ?: 10
        val metrics = s.metrics == true
        KubeShares(st, s.namespaceColumns.orEmpty(), s.namespaces.orEmpty(), link = true, sortedBy, shown, metrics)
        if (s.nodes.orEmpty().size > 1) KubeShares(st, s.nodeColumns.orEmpty(), s.nodes.orEmpty(), link = false, sortedBy, shown, metrics)
    }
}

/** One resource's card: its name and what the nodes can give pods; Requested, Used and Limits, each a bar (Bar) of the
 * most of them and allocatable, its amount and share of allocatable; past allocatable, how far the most over is
 * ("Limits 2.3× allocatable"); how it went. */
@Composable
private fun KubeCard(use: Msg.KubeUse, samples: KubeSamples, memory: Boolean) {
    val c = LocalBaude.current
    val cells = use.cells.orEmpty()
    val shares = use.shares.orEmpty()
    val alloc = use.allocatable?.toDouble()
    val limitsColour = c.fg.copy(alpha = c.fg.alpha * 0.45f)
    val measures = listOf(
        Triple(Wording.Kube.requested to Wording.Kube.requestedTip, use.requests?.toDouble(), c.accentBg),
        Triple(Wording.Kube.used to Wording.Kube.usedTip, use.used?.toDouble(), c.success),
        Triple(Wording.Kube.limits to Wording.Kube.limitsTip, use.limits?.toDouble(), limitsColour))
    val scale = (listOf(alloc ?: 0.0) + measures.map { it.second ?: 0.0 }).max()
    Column(Modifier.fillMaxWidth().border(1.dp, c.rule, RoundedCornerShape(10.dp)).padding(horizontal = 14.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(use.title.orEmpty(), style = Type.title, color = c.fg, modifier = Modifier.weight(1f))
            Text(cells.getOrElse(3) { "" }, style = Type.body, color = c.dim, modifier = Modifier.tip(Wording.Kube.allocatableTip))
        }
        measures.forEachIndexed { i, (words, value, colour) ->
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(words.first, style = Type.body, color = c.fg, maxLines = 1, modifier = Modifier.width(80.dp).tip(words.second))
                Box(Modifier.weight(1f)) {
                    if (alloc != null && alloc > 0) Bar(value ?: 0.0, alloc, scale, colour, c.warning, c.fg)
                }
                Text(cells.getOrElse(i) { "" }, style = Type.body, color = c.fg, textAlign = TextAlign.End, maxLines = 1,
                    modifier = Modifier.width(68.dp))
                Text(shares.getOrElse(i) { "" }, style = Type.body, color = c.dim, textAlign = TextAlign.End, maxLines = 1,
                    modifier = Modifier.width(38.dp))
            }
        }
        if (alloc != null && alloc > 0) {
            val over = measures.mapIndexedNotNull { i, m -> m.second?.takeIf { it > alloc }?.let { it to i } }.maxByOrNull { it.first }
            if (over != null) Text(Wording.Kube.overAllocatable(measures[over.second].first.first, "%.1f".format(over.first / alloc)),
                style = Type.body, color = c.warning)
        }
        Spark(samples, alloc, use.limits?.toDouble(), if (memory) ::kubeBytes else ::kubeCores, c.success, c.accentBg, limitsColour, c.fg)
    }
}

/** A measure's bar: its track the card's scale; the measure filled in its colour up to allocatable, any part past it a
 * segment of its own in orange, and a tick at allocatable when the scale goes past it (kubehome.KubeBar). */
@Composable
private fun Bar(value: Double, alloc: Double, scale: Double, colour: Color, over: Color, fg: Color) {
    Canvas(Modifier.fillMaxWidth().height(16.dp)) {
        val h = 10.dp.toPx()
        val top = (size.height - h) / 2
        val r = androidx.compose.ui.geometry.CornerRadius(h / 2, h / 2)
        val w = size.width
        drawRoundRect(fg.copy(alpha = 0.1f), Offset(0f, top), androidx.compose.ui.geometry.Size(w, h), r)
        val within = (minOf(value, alloc) / scale * w).toFloat()
        val past = (maxOf(0.0, value - alloc) / scale * w).toFloat()
        if (within >= 1f || past >= 1f) {
            val path = Path().apply {
                addRoundRect(androidx.compose.ui.geometry.RoundRect(0f, top, within + past, top + h, r))
            }
            drawPath(path, colour)
            if (past >= 1f) { // (past allocatable: orange, square where it meets the rest)
                clipRect(left = within) { drawPath(path, over) }
            }
        }
        if (scale > alloc * 1.001) {
            val x = (alloc / scale * w).toFloat()
            drawRect(fg.copy(alpha = 0.75f), Offset(x - 1.dp.toPx(), 0f), androidx.compose.ui.geometry.Size(2.dp.toPx(), size.height))
        }
    }
}

/** A resource's history: each sample's time (seconds since 1970), what was used and requested then, and how far back it
 * goes and how often it's sampled. */
class KubeSamples(val at: List<Double>, val used: List<Double?>, val requested: List<Double?>, val minutes: Int, val every: Double)

/** A core count as Home shows it (kubedata.kube_fmt_cores). */
fun kubeCores(n: Double?): String = if (n == null) "N/A" else "%.2f".format(n)

/** A byte count as Home shows it (kubedata.kube_fmt_bytes). */
fun kubeBytes(n: Double?): String {
    if (n == null) return "N/A"
    var v = n
    for (unit in listOf("B", "KiB", "MiB", "GiB", "TiB")) {
        if (v < 1024 || unit == "TiB") return if (unit == "B") "%.0f%s".format(v, unit) else "%.1f%s".format(v, unit)
        v /= 1024
    }
    return ""
}

/** The value a history's top stands for (kubehome.spark_top): allocatable (and a little) while what's used or requested
 * reaches a quarter of it; under that, the most they reached (and a little), allocatable then out of view. */
fun sparkTop(peak: Double, allocatable: Double?): Double =
    if (allocatable != null && allocatable > 0 && peak >= allocatable / 4) maxOf(peak, allocatable) * 1.08
    else if (peak > 0) peak * 1.15 else 1.0

/** How a resource went (kubehome.KubeSpark): used a line, requested dashed, each sample at its time across the last
 * `minutes` (to the newest), so a time with none is a gap; allocatable dotted and the limits dashed across, as now, each
 * while in view (sparkTop); nothing with fewer than two samples. A finger on it (held or dragged) marks the sample
 * nearest, its time, used, requested, limits and allocatable in a bubble above. */
@Composable
private fun Spark(s: KubeSamples, allocatable: Double?, limits: Double?, fmt: (Double?) -> String, usedColour: Color,
                  requestedColour: Color, limitsColour: Color, fg: Color) {
    val lines = listOf(s.requested to requestedColour, s.used to usedColour).filter { (v, _) -> v.count { it != null } >= 2 }
    if (lines.isEmpty()) return
    val top = sparkTop(lines.flatMap { it.first }.filterNotNull().maxOrNull() ?: 0.0, allocatable)
    val span = 60.0 * (if (s.minutes > 0) s.minutes else 30)
    val right = s.at.lastOrNull() ?: 0.0
    var width by remember { mutableStateOf(0) }
    var hover by remember { mutableStateOf<Int?>(null) }
    var dragging by remember { mutableStateOf(false) }
    fun xOf(at: Double, w: Float) = 1f + (w - 2f) * (1.0 - (right - at) / span).toFloat()
    fun nearest(x: Float): Int? {
        if (width <= 2) return null
        val at = right - span * (1.0 - (x - 1.0) / (width - 2.0))
        val i = s.at.indices.filter { i -> lines.any { it.first.getOrNull(i) != null } }.minByOrNull { abs(s.at[it] - at) } ?: return null
        return i.takeIf { abs(s.at[i] - at) <= 2 * s.every }
    }
    val near by rememberUpdatedState(::nearest) // (the gestures run on across new samples)
    Box(Modifier.fillMaxWidth().padding(top = 4.dp)) {
        Canvas(Modifier.fillMaxWidth().height(48.dp).onSizeChanged { width = it.width }
            .semantics { contentDescription = Wording.Kube.historyTip(s.minutes) }
            .pointerInput(Unit) { // (held: the drag's, once it moves sideways, keeps it till the finger's lifted)
                detectTapGestures(onPress = { p -> hover = near(p.x); tryAwaitRelease(); if (!dragging) hover = null })
            }
            .pointerInput(Unit) {
                detectHorizontalDragGestures(onDragStart = { dragging = true; hover = near(it.x) },
                    onDragEnd = { dragging = false; hover = null }, onDragCancel = { dragging = false; hover = null }) { change, _ ->
                    change.consume()
                    hover = near(change.position.x)
                }
            }) {
            fun y(v: Double) = size.height - 2f - (size.height - 4f) * (v / top).toFloat()
            for ((value, colour, dash, w) in listOf(
                Quad(allocatable, fg.copy(alpha = 0.6f), floatArrayOf(1.dp.toPx(), 3.dp.toPx()), 1.2.dp.toPx()),
                Quad(limits, limitsColour, floatArrayOf(6.dp.toPx(), 3.dp.toPx()), 1.5.dp.toPx()))) {
                if (value != null && value > 0 && value <= top) drawLine(colour, Offset(1f, y(value)), Offset(size.width - 1f, y(value)),
                    strokeWidth = w, pathEffect = PathEffect.dashPathEffect(dash))
            }
            for ((values, colour) in lines) {
                val path = Path()
                var before: Double? = null
                values.forEachIndexed { i, v ->
                    if (v == null) {
                        before = null
                        return@forEachIndexed
                    }
                    val at = s.at[i]
                    val x = xOf(at, size.width)
                    val b = before
                    if (b != null && at - b <= 2 * s.every) path.lineTo(x, y(v)) else path.moveTo(x, y(v))
                    before = at
                }
                val dashed = values === s.requested
                drawPath(path, colour.copy(alpha = 0.9f), style = Stroke(width = 1.5.dp.toPx(),
                    pathEffect = if (dashed) PathEffect.dashPathEffect(floatArrayOf(3.dp.toPx(), 3.dp.toPx())) else null))
            }
            hover?.let { i -> // (the sample under the finger: a line down, a dot on each line)
                val x = xOf(s.at[i], size.width)
                drawLine(fg.copy(alpha = 0.35f), Offset(x, 0f), Offset(x, size.height), strokeWidth = 1.dp.toPx())
                for ((values, colour) in lines) values.getOrNull(i)?.let { drawCircle(colour, 3.dp.toPx(), Offset(x, y(it))) }
            }
        }
        hover?.let { i ->
            val x = xOf(s.at[i], width.toFloat())
            Text(sparkValues(s.at[i], s.used.getOrNull(i), s.requested.getOrNull(i), limits, allocatable, fmt),
                style = Type.body, color = Color.White,
                modifier = Modifier.layout { m, c ->
                    val p = m.measure(c.copy(minWidth = 0, minHeight = 0))
                    layout(0, 0) { p.place((x - p.width / 2f).roundToInt().coerceIn(0, maxOf(0, width - p.width)), -p.height - 6.dp.roundToPx()) }
                }.background(Color.Black.copy(alpha = 0.88f), RoundedCornerShape(6.dp)).padding(horizontal = 10.dp, vertical = 6.dp))
        }
    }
}

private data class Quad(val value: Double?, val colour: Color, val dash: FloatArray, val width: Float)

/** A history sample's values (kubehome.spark_values): its time, then used, requested, limits and allocatable (the last
 * two as now). */
fun sparkValues(at: Double, used: Double?, requested: Double?, limits: Double?, allocatable: Double?, fmt: (Double?) -> String): String {
    fun say(n: Double?) = if (n == null) "–" else fmt(n)
    val time = java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.ROOT).format(java.util.Date((at * 1000).toLong()))
    return listOf(time, "${Wording.Kube.used}  ${say(used)}", "${Wording.Kube.requested}  ${say(requested)}",
        "${Wording.Kube.limits}  ${say(limits)}", "${Wording.Kube.allocatable}  ${say(allocatable)}").joinToString("\n")
}

/** A Home table's number as its column shows it (kubedata.kube_fmt_cell): none where there's none. */
private fun kubeCell(kind: String?, n: Double?, none: String): String = when {
    kind == "share" -> if (n == null) "–" else "${n.roundToInt()}%"
    kind == "count" -> (n ?: 0.0).toLong().toString()
    n == null -> none
    kind == "bytes" -> kubeBytes(n)
    else -> kubeCores(n)
}

/** What a column says where it has no number (kubedata._none): N/A for what's used without a metrics API, else –. */
private fun kubeNone(col: Msg.KubeShareColumn, metrics: Boolean) =
    if (!metrics && (col.kind == "cores" || col.kind == "bytes") && col.title.orEmpty().endsWith(" used")) "N/A" else "–"

private fun key(keys: JSONArray?, i: Int): Any? = keys?.opt(i)?.takeIf { it != JSONObject.NULL }

/** An "Other (n)" row: rows added up under columns (kubedata.kube_shares_other): each number's sum (none where none has
 * one), a share the share of the sums. */
private fun kubeOther(columns: List<Msg.KubeShareColumn>, rows: List<Msg.KubeShare>, title: String, metrics: Boolean): Msg.KubeShare {
    val keys = ArrayList<Double?>()
    keys.add(null)
    for (i in 1 until columns.size) {
        val vals = rows.mapNotNull { (key(it.keys, i) as? Number)?.toDouble() }
        keys.add(if (columns[i].kind == "share" || vals.isEmpty()) null else vals.sum())
    }
    columns.forEachIndexed { i, col ->
        val of = col.of.orEmpty()
        if (col.kind == "share" && of.size == 2) {
            val n = keys.getOrNull(of[0].toInt())
            val d = keys.getOrNull(of[1].toInt())
            keys[i] = if (n != null && d != null && d != 0.0) 100 * n / d else null
        }
    }
    return Msg.KubeShare(name = "", cells = listOf(title) + (1 until columns.size).map { kubeCell(columns[it].kind, keys[it], kubeNone(columns[it], metrics)) })
}

/** A table of namespaces' or nodes' parts (kubehome.KubeShares), as the host laid it out, scrolling sideways when it's
 * wider than the screen: the rows any cell of which has the filter's text in it, sorted by the column tapped (again:
 * the other way; at first the host's, most CPU used), the first `shown` and an "Other (n)" row of the rest. */
@Composable
private fun KubeShares(st: KubeState, columns: List<Msg.KubeShareColumn>, shares: List<Msg.KubeShare>, link: Boolean,
                       sortedBy: Int?, shown: Int, metrics: Boolean) {
    val c = LocalBaude.current
    var sort by remember(columns.map { it.title }) { mutableStateOf(sortedBy?.let { it to true }) }
    val q = st.filter.trim().lowercase()
    var rows = shares.filter { s -> q.isEmpty() || s.cells.orEmpty().any { q in it.lowercase() } }
    sort?.let { (col, desc) ->
        val (have, none) = rows.partition { key(it.keys, col) != null }
        val sorted = have.sortedWith { a, b ->
            val x = key(a.keys, col)
            val y = key(b.keys, col)
            if (x is Number && y is Number) x.toDouble().compareTo(y.toDouble()) else x.toString().compareTo(y.toString())
        }
        rows = (if (desc) sorted.reversed() else sorted) + none
    }
    val rest = if (rows.size > shown + 1) rows.drop(shown) else emptyList()
    val other = if (rest.isEmpty()) null else kubeOther(columns, rest, Wording.Kube.other(rest.size), metrics)
    rows = rows.take(rows.size - rest.size)
    val widths: List<Dp> = columns.indices.map { if (it == 0) 110.dp else 92.dp }
    Column(Modifier.fillMaxWidth().padding(top = 6.dp).horizontalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row {
            columns.forEachIndexed { i, col ->
                val mark = sort?.takeIf { it.first == i }?.let { if (it.second) " ▾" else " ▴" }.orEmpty()
                Text(col.title.orEmpty() + mark, style = Type.folder, color = c.dim, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    textAlign = if (i == 0) TextAlign.Start else TextAlign.End,
                    modifier = Modifier.width(widths[i]).clickable {
                        sort = if (sort?.first == i) i to !sort!!.second else i to (col.kind != "name")
                    }.padding(end = 8.dp))
            }
        }
        for (s in rows + listOfNotNull(other)) Row {
            s.cells.orEmpty().forEachIndexed { i, text ->
                val w = Modifier.width(widths.getOrElse(i) { 92.dp }).padding(end = 8.dp)
                if (i == 0 && link && s !== other) Text(text, style = Type.body, color = c.accent, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    modifier = w.tip(Wording.Kube.onlyNamespace(text)) { st.setNamespace(s.name) })
                else Text(text, style = Type.body, color = if (s === other && i == 0) c.dim else c.fg, maxLines = 1,
                    overflow = TextOverflow.Ellipsis, textAlign = if (i == 0) TextAlign.Start else TextAlign.End, modifier = w)
            }
        }
    }
}
