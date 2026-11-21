package dev.baude.android

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipPath
import androidx.compose.ui.text.drawText
import androidx.compose.ui.graphics.drawscope.withTransform
import androidx.compose.ui.input.pointer.PointerEvent
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChange
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalViewConfiguration
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLayoutResult
import androidx.compose.ui.text.TextMeasurer
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.Density
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt

/** The tokens of the theme being drawn (the card, head, border and badge colours differ light and dark). */
@Composable
fun themeTokens(): ThemeTokens = if (LocalBaude.current.window == DarkBaude.window) Tokens.dark else Tokens.light

/** Positions for table cards (schemadata.schema_layout): linked tables in columns, each one column right of the tables it
 * references; tables with no links in a grid beside them. sizes: (w, h) each; links: (child, parent). */
fun schemaLayout(sizes: List<Pair<Float, Float>>, links: List<Pair<Int, Int>>, gap: Float): List<Pair<Float, Float>> {
    val n = sizes.size
    val parents = List(n) { mutableSetOf<Int>() }
    val children = List(n) { mutableSetOf<Int>() }
    for ((c, p) in links) if (c != p) {
        parents[c].add(p)
        children[p].add(c)
    }
    val rank = HashMap<Int, Int>()
    for (start in 0 until n) {
        val stack = ArrayList<Pair<Int, Iterator<Int>>>()
        stack.add(start to parents[start].iterator())
        val onPath = mutableSetOf(start)
        while (stack.isNotEmpty()) {
            val (node, it) = stack.last()
            var next: Int? = null
            while (it.hasNext()) {
                val p = it.next()
                if (p !in rank && p !in onPath) { next = p; break }
            }
            if (next != null) {
                stack.add(next to parents[next].iterator())
                onPath.add(next)
                continue
            }
            rank[node] = 1 + (parents[node].mapNotNull { rank[it] }.maxOrNull() ?: -1)
            onPath.remove(node)
            stack.removeAt(stack.size - 1)
        }
    }
    val linked = (0 until n).filter { parents[it].isNotEmpty() || children[it].isNotEmpty() }
    val columns = java.util.TreeMap<Int, MutableList<Int>>()
    for (i in linked) columns.getOrPut(rank.getValue(i)) { mutableListOf() }.add(i)
    val pos = arrayOfNulls<Pair<Float, Float>>(n)
    val order = HashMap<Int, Int>()
    var x = 0f
    var y = 0f
    for ((_, col) in columns) {
        col.sortWith(compareBy<Int>({
            if (parents[it].isNotEmpty()) parents[it].sumOf { p -> (order[p] ?: 0).toDouble() } / max(1, parents[it].size) else -1.0
        }, { -children[it].size }, { it }))
        y = 0f
        for ((k, i) in col.withIndex()) {
            pos[i] = x to y
            order[i] = k
            y += sizes[i].second + gap / 2
        }
        x += col.maxOf { sizes[it].first } + gap * 1.6f
    }
    val rest = (0 until n).filter { pos[it] == null }
    if (rest.isNotEmpty()) {
        val area = rest.sumOf { (sizes[it].first * sizes[it].second).toDouble() }
        val placed = pos.withIndex().mapNotNull { (i, p) -> p?.let { it.second + sizes[i].second } }.maxOrNull() ?: 0f
        val height = max(rest.maxOf { sizes[it].second }, max(placed, sqrt(area * 0.6).toFloat()))
        var colW = 0f
        y = 0f
        for (i in rest.sortedByDescending { sizes[it].second }) {
            if (y != 0f && y + sizes[i].second > height) {
                x += colW + gap
                y = 0f
                colW = 0f
            }
            pos[i] = x to y
            y += sizes[i].second + gap / 2
            colW = max(colW, sizes[i].first)
        }
    }
    return pos.map { it!! }
}

/** A table drawn as a card: its text laid out once, its size from the widest row. */
class SchemaCard(val table: Msg.DbTable, val w: Float, val h: Float, val head: TextLayoutResult,
                 val sub: TextLayoutResult?, val rows: List<Triple<Msg.DbColumn, TextLayoutResult, TextLayoutResult>>) {
    var x by mutableFloatStateOf(0f)
    var y by mutableFloatStateOf(0f)
}

class SchemaLink(val a: Int, val ac: String, val b: Int, val bc: String)

/** What a diagram shows and where it is looked at from (SchemaCanvas's state): a port of its view, zoom and find. */
class SchemaDiagram(private val density: Density, private val maxScale: Float = 2f) {
    var cards by mutableStateOf<List<SchemaCard>>(emptyList())
    var links by mutableStateOf<List<SchemaLink>>(emptyList())
    var selected by mutableStateOf<Int?>(null)
    var markColumn by mutableStateOf<String?>(null)
    var query by mutableStateOf("")
    var matches by mutableStateOf<List<Pair<Int, Int?>>>(emptyList())
    var scale by mutableFloatStateOf(1f)
    var ox by mutableFloatStateOf(40f)
    var oy by mutableFloatStateOf(40f)
    var width by mutableFloatStateOf(0f)
    var height by mutableFloatStateOf(0f)
    var onSelect: (Msg.DbTable?) -> Unit = {}
    var cardHead = 0f
    var cardRowH = 0f
    var cardPad = 0f

    fun set(cards: List<SchemaCard>, links: List<SchemaLink>) {
        this.cards = cards
        this.links = links
        selected = null
        setQuery(query)
    }

    /** Find text (any case) in the tables' and columns' names: [(card, row)], row null for a table's name. */
    fun setQuery(text: String): List<Pair<Int, Int?>> {
        query = text.trim().lowercase()
        val found = ArrayList<Pair<Int, Int?>>()
        if (query.isNotEmpty()) for ((i, c) in cards.withIndex()) {
            if (query in c.table.name.orEmpty().lowercase()) found.add(i to null)
            for ((k, r) in c.rows.withIndex()) if (query in r.first.name.orEmpty().lowercase()) found.add(i to k)
        }
        matches = found
        return found
    }

    fun toCanvas(x: Float, y: Float) = Offset((x - ox) / scale, (y - oy) / scale)

    fun cardAt(x: Float, y: Float): Int? {
        val (cx, cy) = toCanvas(x, y).let { it.x to it.y }
        for (i in cards.indices.reversed()) {
            val c = cards[i]
            if (cx >= c.x && cx <= c.x + c.w && cy >= c.y && cy <= c.y + c.h) return i
        }
        return null
    }

    fun bounds(of: List<SchemaCard> = cards): Rect? {
        if (of.isEmpty()) return null
        return Rect(of.minOf { it.x }, of.minOf { it.y }, of.maxOf { it.x + it.w }, of.maxOf { it.y + it.h })
    }

    fun fitScale(of: List<SchemaCard> = cards): Float {
        val b = bounds(of) ?: return 1f
        val pad = 60f * density.density
        return max(0.01f, min((width - pad) / max(1f, b.width), (height - pad) / max(1f, b.height)))
    }

    fun minScale() = min(0.5f, 0.8f * fitScale())

    fun zoomTo(to: Float, px: Float, py: Float, at: Offset? = null) {
        val p = at ?: toCanvas(px, py)
        scale = min(maxScale, max(minScale(), to))
        ox = px - p.x * scale
        oy = py - p.y * scale
        constrain()
    }

    fun zoomBy(factor: Float) = zoomTo(scale * factor, width / 2, height / 2)

    fun panBy(dx: Float, dy: Float) {
        ox += dx
        oy += dy
        constrain()
    }

    /** Keep the diagram on the screen: the view's middle stays over the cards' box (d3-zoom's constrain). */
    fun constrain() {
        val b = bounds() ?: return
        val k = scale
        val w = width
        val h = height
        val dx0 = -ox / k - (b.left - w / 2 / k)
        val dx1 = (w - ox) / k - (b.right + w / 2 / k)
        val dy0 = -oy / k - (b.top - h / 2 / k)
        val dy1 = (h - oy) / k - (b.bottom + h / 2 / k)
        ox += k * (if (dx1 > dx0) (dx0 + dx1) / 2 else if (min(0f, dx0) != 0f) min(0f, dx0) else max(0f, dx1))
        oy += k * (if (dy1 > dy0) (dy0 + dy1) / 2 else if (min(0f, dy0) != 0f) min(0f, dy0) else max(0f, dy1))
    }

    fun fit(of: List<SchemaCard> = cards) {
        val b = bounds(of) ?: return
        scale = min(1.2f, fitScale(of))
        ox = (width - b.width * scale) / 2 - b.left * scale
        oy = (height - b.height * scale) / 2 - b.top * scale
        constrain()
    }

    fun select(i: Int?) {
        selected = i
        onSelect(i?.let { cards[it].table })
    }

    /** Select a card and centre it, zoomed in enough to read. */
    fun focus(i: Int) {
        select(i)
        val c = cards[i]
        scale = min(maxScale, max(scale, 0.9f))
        ox = width / 2 - (c.x + c.w / 2) * scale
        oy = height / 3 - (c.y + cardHead) * scale
        constrain()
    }

    // the minimap: (mx, my, mw, mh, k, bx, by), null without cards or in a small view
    fun mini(miniW: Float, miniH: Float, pad: Float, minW: Float, minH: Float): FloatArray? {
        if (cards.isEmpty() || width < minW || height < minH) return null
        var x0 = cards.minOf { it.x }
        var y0 = cards.minOf { it.y }
        var x1 = cards.maxOf { it.x + it.w }
        var y1 = cards.maxOf { it.y + it.h }
        val m = 0.04f * max(x1 - x0, y1 - y0)
        x0 -= m; y0 -= m; x1 += m; y1 += m
        val k = min(miniW / max(1f, x1 - x0), miniH / max(1f, y1 - y0))
        val mw = (x1 - x0) * k
        val mh = (y1 - y0) * k
        return floatArrayOf(width - pad - mw, height - pad - mh, mw, mh, k, x0, y0)
    }

    fun miniCentre(g: FloatArray, x: Float, y: Float) {
        val cx = g[5] + (min(max(x, g[0]), g[0] + g[2]) - g[0]) / g[4]
        val cy = g[6] + (min(max(y, g[1]), g[1] + g[3]) - g[1]) / g[4]
        ox = width / 2 - cx * scale
        oy = height / 2 - cy * scale
        constrain()
    }

    fun rowY(card: SchemaCard, column: String): Float {
        for ((k, r) in card.rows.withIndex()) if (r.first.name == column) return card.y + cardHead + k * cardRowH + cardRowH / 2
        return card.y + cardHead / 2
    }

    /** (xa, ya, xb, yb, d): where a link leaves its column's row and meets the referenced one's; d the way it leaves. */
    fun linkEnds(l: SchemaLink): FloatArray {
        val ca = cards[l.a]
        val cb = cards[l.b]
        val ya = rowY(ca, l.ac)
        val yb = rowY(cb, l.bc)
        return if (ca.x + ca.w / 2 >= cb.x + cb.w / 2) floatArrayOf(ca.x, ya, cb.x + cb.w, yb, -1f)
        else floatArrayOf(ca.x + ca.w, ya, cb.x, yb, 1f)
    }

    fun linkPath(l: SchemaLink): Path {
        val (xa, ya, xb, yb, d) = linkEnds(l).toList()
        val bend = max(40f * density.density, abs(xb - xa) / 2)
        return Path().apply {
            moveTo(xa, ya)
            if (l.a == l.b) cubicTo(xa + 60f * density.density, ya, xa + 60f * density.density, yb, xa, yb)
            else cubicTo(xa + d * bend, ya, xb - d * bend, yb, xb, yb)
        }
    }
}

private operator fun <T> List<T>.component4() = this[3]
private operator fun <T> List<T>.component5() = this[4]

/** A table's card, its text measured: the head (its name in bold), what it is on the right, a row for each column. */
fun measureCard(t: Msg.DbTable, measure: TextMeasurer, density: Density, headH: Float, rowH: Float, pad: Float,
                maxW: Float): SchemaCard {
    val bold = Type.body.copy(fontWeight = FontWeight.Bold)
    val head = measure.measure(t.name.orEmpty(), bold, softWrap = false)
    val kind = t.kind.orEmpty()
    val subText = if (kind == "r" || kind == "p") Wording.Databases.rows(t.rows ?: 0)
    else mapOf("v" to Wording.Databases.view, "m" to Wording.Databases.materializedView,
        "f" to Wording.Databases.foreignTable)[kind].orEmpty()
    val sub = measure.measure(subText, Type.body, softWrap = false)
    val rows = t.columns.orEmpty().map { col ->
        Triple(col, measure.measure(col.name.orEmpty(), Type.body, softWrap = false),
            measure.measure(col.type.orEmpty() + (if (col.`null` == false) "" else "?"), Type.body, softWrap = false))
    }
    val d = density.density
    val widest = max(head.size.width + sub.size.width + 24 * d,
        rows.maxOfOrNull { it.second.size.width + it.third.size.width + 64 * d } ?: 0f)
    return SchemaCard(t, min(maxW, widest + 2 * pad), headH + rowH * max(1, rows.size) + 6 * d, head, sub, rows)
}

private fun Color.a(alpha: Float) = copy(alpha = alpha)

/** The diagram (schemaview.SchemaCanvas): a card for each table, a curve for each foreign key, and a minimap. Drag a card
 * to move it or the background to pan, pinch to zoom, tap a card to select it, tap it twice for its rows. */
@Composable
fun SchemaCanvas(d: SchemaDiagram, modifier: Modifier = Modifier, onRows: (Msg.DbTable) -> Unit) {
    val c = LocalBaude.current
    val t = themeTokens()
    val density = LocalDensity.current
    val measure = rememberTextMeasurer(256)
    val px = density.density
    val s = t.schema
    val pad = with(density) { s.pad.toPx() }
    val headH = with(density) { s.headH.toPx() }
    val rowH = with(density) { s.rowH.toPx() }
    val miniW = with(density) { s.miniW.toPx() }
    val miniH = with(density) { s.miniH.toPx() }
    val miniPad = with(density) { s.miniPad.toPx() }
    val grid = with(density) { s.grid.toPx() }
    val gridMin = with(density) { s.gridMin.toPx() }
    val link = with(density) { s.link.toPx() }
    val linkHot = with(density) { s.linkHot.toPx() }
    val slop = LocalViewConfiguration.current.touchSlop
    d.cardHead = headH
    d.cardRowH = rowH
    d.cardPad = pad
    val fg = c.fg
    val dim = fg.a(0.55f)
    val dark = c.window == DarkBaude.window
    var lastTap by remember { mutableStateOf<Pair<Long, Int?>>(0L to null) }
    Canvas(modifier.fillMaxSize().onSizeChanged {
        d.width = it.width.toFloat()
        d.height = it.height.toFloat()
        d.constrain()
    }.pointerInput(d, miniW, miniH, miniPad) {
        awaitEachGesture {
            val down = awaitFirstDown(requireUnconsumed = false)
            val g0 = d.mini(miniW, miniH, miniPad, 360f * px, 260f * px)
            fun inMini(p: Offset, g: FloatArray?) = g != null && p.x >= g[0] - 4 && p.x <= g[0] + g[2] + 4 &&
                p.y >= g[1] - 4 && p.y <= g[1] + g[3] + 4
            val onMini = inMini(down.position, g0)
            val card = if (onMini) null else d.cardAt(down.position.x, down.position.y)
            val cardAt = card?.let { d.cards[it].x to d.cards[it].y }
            var view0 = d.ox to d.oy
            var moved = false
            var pinch: Triple<Float, Offset, Float>? = null // (scale, canvas point under the fingers, span) at its start
            var base = down.position
            if (onMini) d.miniCentre(g0!!, down.position.x, down.position.y)
            while (true) {
                val ev: PointerEvent = awaitPointerEvent()
                val down2 = ev.changes.filter { it.pressed }
                if (down2.isEmpty()) break
                if (down2.size >= 2) {
                    val centre = Offset(down2.map { it.position.x }.average().toFloat(), down2.map { it.position.y }.average().toFloat())
                    val span = down2.map { (it.position - centre).getDistance() }.average().toFloat().coerceAtLeast(1f)
                    if (pinch == null) pinch = Triple(d.scale, d.toCanvas(centre.x, centre.y), span)
                    d.zoomTo(pinch.first * span / pinch.third, centre.x, centre.y, pinch.second)
                    moved = true
                    ev.changes.forEach { it.consume() }
                    continue
                }
                val p = down2[0]
                if (pinch != null) { // (one finger left: it pans from here)
                    pinch = null
                    base = p.position
                    view0 = d.ox to d.oy
                    continue
                }
                val delta = p.position - base
                if (!moved && delta.getDistance() < slop) continue
                moved = true
                when {
                    onMini -> d.miniCentre(d.mini(miniW, miniH, miniPad, 360f * px, 260f * px) ?: g0!!, p.position.x, p.position.y)
                    card != null -> {
                        d.cards[card].x = cardAt!!.first + delta.x / d.scale
                        d.cards[card].y = cardAt.second + delta.y / d.scale
                    }
                    else -> {
                        d.ox = view0.first
                        d.oy = view0.second
                        d.panBy(delta.x, delta.y)
                    }
                }
                if (p.positionChange() != Offset.Zero) p.consume()
            }
            if (!moved && !onMini) {
                val now = System.currentTimeMillis()
                val (at, was) = lastTap
                if (card != null && was == card && now - at < 300) {
                    onRows(d.cards[card].table)
                    lastTap = 0L to null
                } else {
                    d.select(card)
                    lastTap = now to card
                }
            }
        }
    }) {
        val w = size.width
        val h = size.height
        val step = grid * d.scale
        if (step >= gridMin) {
            val x0 = d.ox % step
            val y0 = d.oy % step
            for (gx in 0..(w / step).toInt() + 1) for (gy in 0..(h / step).toInt() + 1)
                drawRect(fg.a(0.06f), Offset(x0 + gx * step, y0 + gy * step), Size(1.5f * px, 1.5f * px))
        }
        val sel = d.selected
        val near = if (sel == null) emptySet() else setOf(sel) + d.links.filter { it.a == sel }.map { it.b } +
            d.links.filter { it.b == sel }.map { it.a }
        val cardFill = t.schema.card
        withTransform({ translate(d.ox, d.oy); scale(d.scale, d.scale, Offset.Zero) }) {
            val viewRect = Rect(-d.ox / d.scale, -d.oy / d.scale, (w - d.ox) / d.scale, (h - d.oy) / d.scale)
            for (l in d.links) {
                val hot = sel == l.a || sel == l.b
                val e = d.linkEnds(l)
                val col = if (hot) c.accent else fg.a(0.32f)
                drawPath(d.linkPath(l), col, style = Stroke(if (hot) linkHot else link))
                drawRect(col, Offset(e[2] - 3 * px, e[3] - 3 * px), Size(6 * px, 6 * px))
            }
            for ((i, card) in d.cards.withIndex()) {
                if (!Rect(card.x, card.y, card.x + card.w, card.y + card.h).overlaps(viewRect)) continue
                drawCard(d, card, i == sel, i in near, cardFill, t, fg, dim, c.accent, headH, rowH, pad, px, measure)
            }
        }
        drawMini(d, d.mini(miniW, miniH, miniPad, 360f * px, 260f * px), t, c.accent, fg, dark, px)
    }
}

private fun DrawScope.drawCard(d: SchemaDiagram, card: SchemaCard, selected: Boolean, near: Boolean, fill: Color, t: ThemeTokens,
                               fg: Color, dim: Color, accent: Color, headH: Float, rowH: Float, pad: Float, px: Float,
                               measure: TextMeasurer) {
    val x = card.x
    val y = card.y
    val w = card.w
    val h = card.h
    val round = 8 * px
    val outline = Path().apply { addRoundRect(androidx.compose.ui.geometry.RoundRect(x, y, x + w, y + h, CornerRadius(round))) }
    val q = d.query
    val find = Tokens.Find.match
    clipPath(outline) {
        drawRect(fill, Offset(x, y), Size(w, h))
        drawRect(t.schema.head, Offset(x, y), Size(w, headH))
        drawRect(t.schema.border, Offset(x, y + headH), Size(w, 1f))
        val name = card.table.name.orEmpty()
        val head = if (q.isNotEmpty() && q in name.lowercase()) marked(measure, name, q, true) else card.head
        text(head, fg, Offset(x + pad, y + (headH - head.size.height) / 2))
        card.sub?.let { text(it, dim, Offset(x + w - pad - it.size.width, y + (headH - it.size.height) / 2)) }
        if (q.isNotEmpty() && q in name.lowercase()) drawRect(find.a(0.22f), Offset(x, y), Size(w, headH))
        for ((k, r) in card.rows.withIndex()) {
            val (col, nm, typ) = r
            val ry = y + headH + k * rowH
            val cname = col.name.orEmpty()
            if (q.isNotEmpty() && q in cname.lowercase()) drawRect(find.a(0.16f), Offset(x, ry), Size(w, rowH))
            if (selected && d.markColumn != null && cname == d.markColumn) drawRect(accent.a(0.2f), Offset(x, ry), Size(w, rowH))
            if (k > 0) drawRect(fg.a(0.18f), Offset(x + pad, ry), Size(w - 2 * pad, 0.6f * px))
            var bx = x + pad
            for ((flag, color) in listOf((col.pk == true) to t.schema.pk, (col.fk != null) to t.schema.fk)) if (flag) {
                drawRect(color, Offset(bx, ry + rowH / 2 - 3 * px), Size(6 * px, 6 * px))
                bx += 9 * px
            }
            val label = if (q.isNotEmpty() && q in cname.lowercase()) marked(measure, cname, q, false) else nm
            text(label, if (col.`null` == false || col.pk == true) fg else fg.a(0.55f),
                Offset(x + pad + 20 * px, ry + (rowH - label.size.height) / 2))
            text(typ, dim, Offset(x + w - pad - typ.size.width, ry + (rowH - typ.size.height) / 2))
        }
    }
    drawPath(outline, if (selected) accent else if (near) t.schema.fk else t.schema.border,
        style = Stroke(if (selected || near) 2 * px else px))
}

private fun DrawScope.text(layout: TextLayoutResult, color: Color, at: Offset) =
    drawText(layout, color = color, topLeft = at)

/** A name with each place the find text is in it marked (Find's colour behind it, black on that). */
private fun marked(measure: TextMeasurer, text: String, q: String, bold: Boolean): TextLayoutResult {
    val style = if (bold) Type.body.copy(fontWeight = FontWeight.Bold) else Type.body
    val low = text.lowercase()
    val s: AnnotatedString = buildAnnotatedString {
        var at = 0
        while (true) {
            val i = low.indexOf(q, at)
            if (i < 0 || q.isEmpty()) break
            append(text.substring(at, i))
            withStyle(SpanStyle(background = Tokens.Find.match, color = Color.Black)) { append(text.substring(i, i + q.length)) }
            at = i + q.length
        }
        append(text.substring(at))
    }
    return measure.measure(s, style, softWrap = false)
}

/** The minimap (draw_mini): every table small, what's in view a clear cutout in a greyed map. */
private fun DrawScope.drawMini(d: SchemaDiagram, g: FloatArray?, t: ThemeTokens, accent: Color, fg: Color, dark: Boolean,
                               px: Float) {
    if (g == null) return
    val (mx, my, mw, mh, k, bx, by) = g.toList()
    val frame = Rect(mx - 4 * px, my - 4 * px, mx + mw + 4 * px, my + mh + 4 * px)
    val round = 6 * px
    val clip = Path().apply { addRoundRect(androidx.compose.ui.geometry.RoundRect(frame, CornerRadius(round))) }
    val found = d.matches.map { it.first }.toSet()
    clipPath(clip) {
        drawRect(t.schema.card.a(0.94f), frame.topLeft, frame.size)
        withTransform({ translate(mx - bx * k, my - by * k); scale(k, k, Offset.Zero) }) {
            for (l in d.links) {
                val hot = d.selected == l.a || d.selected == l.b
                drawPath(d.linkPath(l), if (hot) accent else fg.a(0.3f), style = Stroke((if (hot) 1.4f else 0.9f) * px / k))
            }
        }
        for ((i, c) in d.cards.withIndex()) {
            drawRect(if (i == d.selected) accent else if (i in found) Tokens.Find.match else fg.a(0.38f),
                Offset(mx + (c.x - bx) * k, my + (c.y - by) * k), Size(max(1.5f, c.w * k), max(1.5f, c.h * k)))
        }
        val v0 = d.toCanvas(0f, 0f)
        val v1 = d.toCanvas(d.width, d.height)
        val cx0 = max(frame.left, mx + (v0.x - bx) * k)
        val cy0 = max(frame.top, my + (v0.y - by) * k)
        val cx1 = min(frame.right, mx + (v1.x - bx) * k)
        val cy1 = min(frame.bottom, my + (v1.y - by) * k)
        val grey = Color.Black.a(if (dark) 0.45f else 0.28f)
        if (cx1 <= cx0 || cy1 <= cy0) drawRect(grey, frame.topLeft, frame.size)
        else for (r in listOf(Rect(frame.left, frame.top, frame.right, cy0), Rect(frame.left, cy1, frame.right, frame.bottom),
            Rect(frame.left, cy0, cx0, cy1), Rect(cx1, cy0, frame.right, cy1)))
            if (r.width > 0 && r.height > 0) drawRect(grey, r.topLeft, r.size)
        if (cx1 > cx0 && cy1 > cy0) drawRoundRect(accent, Offset(cx0, cy0), Size(cx1 - cx0, cy1 - cy0), CornerRadius(2 * px),
            style = Stroke(1.5f * px))
    }
    drawRoundRect(t.schema.border, frame.topLeft, frame.size, CornerRadius(round), style = Stroke(px))
}

private operator fun <T> List<T>.component6() = this[5]
private operator fun <T> List<T>.component7() = this[6]
