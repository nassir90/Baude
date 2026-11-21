package dev.baude.android

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import org.json.JSONArray
import org.json.JSONObject

private const val WIDEST = 320 // (a column no wider than this, of its widest cell)
private val FIXED = mapOf("Message" to 420, "Rules" to 320, "Involved Object" to 260) // (the desktop's, by title)
private val GROWS = setOf("Message", "Rules")

/** Two sort keys compared (-1, 0, 1), as kubeview.kube_order does: numbers (and lists of them) before text, nothing last;
 * a list against a number by their texts. */
fun kubeOrder(a: Any?, b: Any?): Int {
    val x = norm(a)
    val y = norm(b)
    if (x.first != y.first) return x.first.compareTo(y.first)
    return when (x.first) {
        2 -> 0
        1 -> x.third.compareTo(y.third).coerceIn(-1, 1)
        else -> {
            val p = x.second
            val q = y.second
            when {
                p is Double && q is Double -> p.compareTo(q)
                p is List<*> && q is List<*> -> {
                    var out = 0
                    for (i in 0 until minOf(p.size, q.size)) {
                        out = (p[i] as Double).compareTo(q[i] as Double)
                        if (out != 0) break
                    }
                    if (out != 0) out else p.size.compareTo(q.size)
                }
                else -> asText(p).compareTo(asText(q))
            }.coerceIn(-1, 1)
        }
    }
}

private fun num(d: Double) = if (d == Math.floor(d) && !d.isInfinite()) d.toLong().toString() else d.toString()

private fun asText(k: Any?): String = "(0, " + when (k) {
    is Double -> num(k)
    is List<*> -> if (k.size == 1) "(${num(k[0] as Double)},)" else k.joinToString(", ", "(", ")") { num(it as Double) }
    else -> ""
} + ", '')"

private fun norm(k: Any?): Triple<Int, Any?, String> = when {
    k == null || k == JSONObject.NULL || k == "" -> Triple(2, null, "")
    k is Boolean -> Triple(0, if (k) 1.0 else 0.0, "")
    k is Number -> Triple(0, k.toDouble(), "")
    k is JSONArray -> Triple(0, (0 until k.length()).map { (k.opt(it) as? Number)?.toDouble() ?: 0.0 }, "")
    else -> Triple(1, null, k.toString())
}

/** The rows to show: those in the namespaces shown and with the filter's text in a cell, in the order sorted by. */
fun kubeRows(st: KubeState): List<Msg.KubeRow> {
    val q = st.filter.trim().lowercase()
    val spaced = st.nsShown.isNotEmpty() && st.tablePage?.namespaced == true
    val kept = st.rows.filter { r ->
        (!spaced || r.namespace.orEmpty() in st.nsShown) && (q.isEmpty() || r.cells.orEmpty().any { q in it.lowercase() })
    }
    val i = st.sortCol
    val sorted = kept.sortedWith { a, b -> kubeOrder(a.keys?.opt(i), b.keys?.opt(i)) }
    return if (st.sortDesc) sorted.reversed() else sorted
}

/** A cell's text with what's filtered for found in it, black on yellow (find_label). */
private fun found(text: String, q: String): AnnotatedString {
    if (q.isEmpty()) return AnnotatedString(text)
    val low = text.lowercase()
    return buildAnnotatedString {
        append(text)
        var at = 0
        while (true) {
            val i = low.indexOf(q, at)
            if (i < 0) break
            addStyle(SpanStyle(background = Tokens.Find.match, color = Color.Black), i, i + q.length)
            at = i + q.length
        }
    }
}

/** A pod's containers, a square each: green running and ready, orange getting ready, green ringed with orange restarted
 * lately, red failed, grey done (KubeSquares). */
@Composable
private fun Squares(containers: List<Msg.KubeContainer>) {
    val c = LocalBaude.current
    val k = kubeColours()
    val side = k.square
    val gap = k.squareGap
    val n = containers.size
    Canvas(Modifier.width(side * n + gap * maxOf(0, n - 1)).height(side)) {
        val s = side.toPx()
        val round = CornerRadius(2.dp.toPx())
        containers.forEachIndexed { i, ct ->
            val at = Offset(i * (s + gap.toPx()), 0f)
            when (ct.look) {
                "starting" -> drawRoundRect(k.starting, at, Size(s, s), round)
                "bad" -> drawRoundRect(k.bad, at, Size(s, s), round)
                "done" -> drawRoundRect(c.fg.copy(alpha = 0.35f), at, Size(s, s), round)
                "restarted" -> {
                    drawRoundRect(k.ok, at, Size(s, s), round)
                    val w = 1.dp.toPx()
                    drawRoundRect(k.starting, at + Offset(w / 2, w / 2), Size(s - w, s - w), round, Stroke(w))
                }
                else -> drawRoundRect(k.ok, at, Size(s, s), round)
            }
        }
    }
}

/** A button in a pod's row: its icon with a little room (.kube-row-btn). */
@Composable
private fun RowButton(icon: androidx.compose.ui.graphics.vector.ImageVector, label: String, onClick: () -> Unit) {
    val c = LocalBaude.current
    Box(Modifier.clickable(onClick = onClick).padding(horizontal = 4.dp, vertical = 2.dp)) {
        Icon(icon, label, tint = c.fg, modifier = Modifier.size(16.dp.icon))
    }
}

/** A pod's last cell: its logs, and a menu to delete it or force-delete it. */
@Composable
private fun PodButtons(st: KubeState, row: Msg.KubeRow) {
    var open by remember { mutableStateOf(false) }
    Row(verticalAlignment = Alignment.CenterVertically) {
        RowButton(Adwaita.Logs, Wording.Kube.logs) { st.showLogs(row) }
        Box {
            RowButton(Adwaita.ViewMore, Wording.Common.deleteEllipsis) { open = true }
            Popover(open, { open = false }) {
                MenuChoice(Wording.Common.delete, false) { open = false; st.deleting = row to false }
                MenuChoice(Wording.Kube.forceDelete, false) { open = false; st.deleting = row to true }
            }
        }
    }
}

/** The table of a kind's objects (the ColumnView of kubeview): a header to sort by and resize, a row to select (twice:
 * a namespace's pods, a definition's objects); the columns scroll sideways together. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun KubeTable(st: KubeState, rows: List<Msg.KubeRow>) {
    val c = LocalBaude.current
    val k = kubeColours()
    val page = st.tablePage ?: return
    val cols = st.columns
    val pods = page.resource == "pods"
    val measure = rememberTextMeasurer()
    val density = LocalDensity.current
    val q = st.filter.trim().lowercase()
    val here = cols.indices.filter { cols[it].title != "Node" || st.nodes > 1 }
    val resized = remember(page.resource, cols.map { it.title }) { mutableStateMapOf<Int, Dp>() }
    val natural = remember(rows, cols) {
        fun wide(text: String, bold: Boolean) = with(density) {
            measure.measure(text, if (bold) Type.bold else Type.body, softWrap = false).size.width.toDp()
        }
        here.associateWith { i ->
            val col = cols[i]
            val title = col.title.orEmpty()
            val fixed = when (col.kind) {
                "name" -> k.nameWidth
                "namespace" -> k.namespaceWidth
                else -> FIXED[title]?.dp
            }
            fixed ?: run {
                var w = wide(title, true) + 12.dp
                if (col.kind == "containers") {
                    val most = rows.maxOfOrNull { it.containers.orEmpty().size } ?: 0
                    w = maxOf(w, k.square * most + k.squareGap * maxOf(0, most - 1) + 12.dp)
                } else {
                    for (r in rows.sortedByDescending { it.cells.orEmpty().getOrElse(i) { "" }.length }.take(5))
                        w = maxOf(w, wide(r.cells.orEmpty().getOrElse(i) { "" }, false) + 12.dp)
                }
                minOf(w, WIDEST.dp)
            }
        }
    }
    val buttons = 16.dp.icon * 2 + 20.dp
    BoxWithConstraints(Modifier.padding(0.dp)) {
        val view = maxWidth
        val grows = here.filter { cols[it].kind == "name" || cols[it].title in GROWS }
        val base = here.associateWith { resized[it] ?: natural.getValue(it) }
        val room = view - base.values.fold(0.dp) { a, b -> a + b } - (if (pods) buttons else 0.dp)
        val extra = if (room > 0.dp && grows.isNotEmpty()) room / grows.size else 0.dp
        fun wide(i: Int) = base.getValue(i) + if (i in grows && i !in resized) extra else 0.dp
        val total = here.fold(0.dp) { a, i -> a + wide(i) } + if (pods) buttons else 0.dp
        val scroll = rememberScrollState()
        Column {
            Row(Modifier.horizontalScroll(scroll).width(total).background(c.raised)) {
                for (i in here) {
                    val title = cols[i].title.orEmpty()
                    Box(Modifier.width(wide(i)).clickable { if (st.sortCol == i) st.sortDesc = !st.sortDesc else { st.sortCol = i; st.sortDesc = false } }) {
                        Row(Modifier.padding(horizontal = 6.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                            Text(title, style = Type.bold, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
                            if (st.sortCol == i) Icon(if (st.sortDesc) Adwaita.PanDown else Adwaita.GoUp, null, tint = c.fg,
                                modifier = Modifier.size(16.dp.icon))
                        }
                        Box(Modifier.align(Alignment.CenterEnd).width(6.dp).fillMaxHeight().pointerInput(i) {
                            detectHorizontalDragGestures { _, d -> resized[i] = maxOf(30.dp, wide(i) + with(density) { d.toDp() }) }
                        })
                    }
                }
                if (pods) Box(Modifier.width(buttons))
            }
            Box(Modifier.width(view).height(1.dp).background(c.rule))
            LazyColumn(Modifier.horizontalScroll(scroll).width(total)) {
                items(rows, key = { it.uid.orEmpty() }) { row ->
                    val on = row.uid == st.selected
                    Row(Modifier.width(total).background(if (on) c.rowSelected else Color.Transparent)
                        .combinedClickable(onClick = { st.selectRow(row) }, onDoubleClick = { st.activate(row) })
                        .padding(vertical = k.rowPad), verticalAlignment = Alignment.CenterVertically) {
                        for (i in here) Cell(st, row, i, cols[i], wide(i), q)
                        if (pods) Box(Modifier.width(buttons)) { PodButtons(st, row) }
                    }
                }
            }
        }
    }
}

/** One cell of a row, by its column's kind. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun Cell(st: KubeState, row: Msg.KubeRow, i: Int, col: Msg.KubeColumn, width: Dp, q: String) {
    val c = LocalBaude.current
    val text = row.cells.orEmpty().getOrElse(i) { "" }
    val long = text.length > (if (col.kind == "name") 30 else 40)
    val tip = row.tips.orEmpty().getOrElse(i) { "" }.ifEmpty { if (long) text else "" }
    val select = Modifier.combinedClickable(onClick = { st.selectRow(row) }, onDoubleClick = { st.activate(row) },
        onLongClick = if (tip.isNotEmpty()) ({ toast(tip) }) else null)
    val pad = Modifier.padding(horizontal = 6.dp)
    when (col.kind) {
        "containers" -> Box(Modifier.width(width).then(select).padding(horizontal = 6.dp)) {
            val cs = row.containers.orEmpty()
            Box(Modifier.tip(cs.joinToString("\n") {
                (if (it.init == true) Wording.Kube.initContainer(it.name.orEmpty()) else it.name.orEmpty()) + ": " + it.words.orEmpty()
            })) { Squares(cs) }
        }
        "namespace" -> Box(Modifier.width(width).then(pad)) {
            Text(found(text, q), style = Type.body, color = c.accent, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.tip(if (text.isEmpty()) "" else Wording.Kube.onlyNamespace(text)) { if (text.isNotEmpty()) st.setNamespace(text) })
        }
        "owner" -> Box(Modifier.width(width).then(pad)) {
            val linked = !row.ownerKind.isNullOrEmpty() && !row.ownerName.isNullOrEmpty()
            Text(found(text, q), style = Type.body, color = c.accent, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = if (linked) Modifier.clickable { st.goToOwner(row) } else Modifier)
        }
        else -> {
            val tone = when (row.tones.orEmpty().getOrElse(i) { "" }) {
                "ok" -> c.success
                "warn" -> c.warning
                "bad" -> c.error
                else -> c.fg
            }
            Box(Modifier.width(width).then(select).then(pad)) {
                Text(found(text, q), style = Type.body, color = tone, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
    }
}
