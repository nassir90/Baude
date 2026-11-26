package dev.baude.android

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/** Home (kubehome.KubeHome): the whole stack's CPU and memory as the host added them up: a card each, Requested, Used
 * and Limits a bar each against what the nodes can give pods, how they went under them; then the same by namespace (a
 * namespace's name a link: only its objects), and by node on a cluster of more than one. */
@Composable
fun KubeHome(st: KubeState, s: Msg.KubeSummary) {
    val history = st.history
    val minutes = s.minutes?.toInt() ?: 0
    val every = s.every?.toDouble() ?: 10.0
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)) {
        val at = history.map { h -> h.at?.toDouble() ?: 0.0 }
        s.cpu?.let { KubeCard(it, KubeSamples(at, history.map { h -> h.cpuUsed?.toDouble() }, history.map { h -> h.cpuRequests?.toDouble() }, minutes, every)) }
        s.memory?.let { KubeCard(it, KubeSamples(at, history.map { h -> h.memoryUsed?.toDouble() }, history.map { h -> h.memoryRequests?.toDouble() }, minutes, every)) }
        KubeShares(st, s.namespaceColumns.orEmpty(), s.namespaces.orEmpty(), link = true)
        if (s.nodes.orEmpty().size > 1) KubeShares(st, s.nodeColumns.orEmpty(), s.nodes.orEmpty(), link = false)
    }
}

/** One resource's card: its name and what the nodes can give pods; Requested, Used and Limits, each a bar of that (full
 * and orange past it), its amount and share; how it went. */
@Composable
private fun KubeCard(use: Msg.KubeUse, samples: KubeSamples) {
    val c = LocalBaude.current
    val cells = use.cells.orEmpty()
    val shares = use.shares.orEmpty()
    val alloc = use.allocatable?.toDouble()
    Column(Modifier.fillMaxWidth().border(1.dp, c.rule, RoundedCornerShape(10.dp)).padding(horizontal = 14.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(use.title.orEmpty(), style = Type.title, color = c.fg, modifier = Modifier.weight(1f))
            Text(cells.getOrElse(3) { "" }, style = Type.body, color = c.dim, modifier = Modifier.tip(Wording.Kube.allocatableTip))
        }
        val measures = listOf(
            Triple(Wording.Kube.requested to Wording.Kube.requestedTip, use.requests?.toDouble(), c.accentBg),
            Triple(Wording.Kube.used to Wording.Kube.usedTip, use.used?.toDouble(), c.success),
            Triple(Wording.Kube.limits to Wording.Kube.limitsTip, use.limits?.toDouble(), c.fg.copy(alpha = c.fg.alpha * 0.45f)))
        measures.forEachIndexed { i, (words, value, colour) ->
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(words.first, style = Type.body, color = c.fg, maxLines = 1, modifier = Modifier.width(80.dp).tip(words.second))
                Box(Modifier.weight(1f)) {
                    if (alloc != null && alloc > 0) {
                        val over = (value ?: 0.0) > alloc
                        Bar(((value ?: 0.0) / alloc).coerceIn(0.0, 1.0).toFloat(), if (over) c.warning else colour, c.fg.copy(alpha = 0.1f))
                    }
                }
                Text(cells.getOrElse(i) { "" }, style = Type.body, color = c.fg, textAlign = TextAlign.End, maxLines = 1,
                    modifier = Modifier.width(68.dp))
                Text(shares.getOrElse(i) { "" }, style = Type.body, color = c.dim, textAlign = TextAlign.End, maxLines = 1,
                    modifier = Modifier.width(38.dp))
            }
        }
        Spark(samples, c.success, c.accentBg, Modifier.tip(Wording.Kube.historyTip(samples.minutes)))
    }
}

/** A bar: its fill (a share, 0 to 1) over its track, 10 high and round. */
@Composable
private fun Bar(fill: Float, colour: Color, track: Color) {
    Box(Modifier.fillMaxWidth().height(10.dp).clip(RoundedCornerShape(5.dp)).background(track)) {
        if (fill > 0f) Box(Modifier.fillMaxWidth(fill).fillMaxHeight().clip(RoundedCornerShape(5.dp)).background(colour))
    }
}

/** A resource's history: each sample's time (seconds since 1970), what was used and requested then, and how far back it
 * goes and how often it's sampled. */
class KubeSamples(val at: List<Double>, val used: List<Double?>, val requested: List<Double?>, val minutes: Int, val every: Double)

/** How a resource went (kubehome.KubeSpark): used a line, requested dashed, each sample at its time across the last
 * `minutes` (to the newest), so a time with none is a gap; scaled to the most either reached; nothing with fewer than
 * two samples. */
@Composable
private fun Spark(s: KubeSamples, usedColour: Color, requestedColour: Color, modifier: Modifier) {
    val lines = listOf(s.requested to requestedColour, s.used to usedColour).filter { (v, _) -> v.count { it != null } >= 2 }
    if (lines.isEmpty()) return
    val top = (lines.flatMap { it.first }.filterNotNull().maxOrNull() ?: 0.0).let { if (it > 0) it * 1.15 else 1.0 }
    val span = 60.0 * (if (s.minutes > 0) s.minutes else 30)
    val right = s.at.lastOrNull() ?: 0.0
    Canvas(modifier.fillMaxWidth().padding(top = 4.dp).height(34.dp)) {
        for ((values, colour) in lines) {
            val path = Path()
            var before: Double? = null
            values.forEachIndexed { i, v ->
                if (v == null) {
                    before = null
                    return@forEachIndexed
                }
                val at = s.at[i]
                val x = 1f + (size.width - 2f) * (1.0 - (right - at) / span).toFloat()
                val y = size.height - 2f - (size.height - 4f) * (v / top).toFloat()
                val b = before
                if (b != null && at - b <= 2 * s.every) path.lineTo(x, y) else path.moveTo(x, y)
                before = at
            }
            val dashed = values === s.requested
            drawPath(path, colour.copy(alpha = 0.9f), style = Stroke(width = 1.5.dp.toPx(),
                pathEffect = if (dashed) PathEffect.dashPathEffect(floatArrayOf(3.dp.toPx(), 3.dp.toPx())) else null))
        }
    }
}

/** A table of namespaces' or nodes' parts (kubehome.KubeShares), as the host laid it out, scrolling sideways when it's
 * wider than the screen; the rows any cell of which has the filter's text in it. */
@Composable
private fun KubeShares(st: KubeState, columns: List<String>, shares: List<Msg.KubeShare>, link: Boolean) {
    val c = LocalBaude.current
    val q = st.filter.trim().lowercase()
    val shown = shares.filter { s -> q.isEmpty() || s.cells.orEmpty().any { q in it.lowercase() } }
    val widths: List<Dp> = columns.indices.map { if (it == 0) 110.dp else 92.dp }
    Column(Modifier.fillMaxWidth().padding(top = 6.dp).horizontalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row {
            columns.forEachIndexed { i, title ->
                Text(title, style = Type.folder, color = c.dim, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    textAlign = if (i == 0) TextAlign.Start else TextAlign.End, modifier = Modifier.width(widths[i]).padding(end = 8.dp))
            }
        }
        for (s in shown) Row {
            s.cells.orEmpty().forEachIndexed { i, text ->
                val w = Modifier.width(widths.getOrElse(i) { 92.dp }).padding(end = 8.dp)
                if (i == 0 && link) Text(text, style = Type.body, color = c.accent, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    modifier = w.tip(Wording.Kube.onlyNamespace(text)) { st.setNamespace(s.name) })
                else Text(text, style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    textAlign = if (i == 0) TextAlign.Start else TextAlign.End, modifier = w)
            }
        }
    }
}
