package dev.baude.android

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.height
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.compositeOver
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

private val widthCache = android.util.LruCache<List<Any?>, Dp>(2048)

/** A markdown table as Baude draws one (md-table): a frame with a ruled grid, its first row the head (bold, raised).
 * Short columns are as wide as their text; the long ones (a cell over 40 characters) share what's left of the
 * column and wrap, within the bubble or column it's in, never under 12 characters; wider than that even so, it
 * scrolls sideways. */
@Composable
fun MarkdownTable(rows: List<List<String>>, color: Color, under: Color) {
    val c = LocalBaude.current
    val columns = rows.maxOfOrNull { it.size } ?: return
    val measure = rememberTextMeasurer()
    val density = LocalDensity.current
    val bold = Type.body.copy(fontWeight = FontWeight.Bold)
    val cells = rows.mapIndexed { r, row -> List(columns) { col -> rememberInline(row.getOrElse(col) { "" }) } }
    val pics = LocalMarkdownPictures.current // (a cell that's only a picture: the picture, where an ask's body has them)
    val shown = rows.map { row -> List(columns) { col -> pics?.let { imageOnly(row.getOrElse(col) { "" }) } } }
    BoxWithConstraints(Modifier.padding(vertical = 2.dp)) {
        val available = maxWidth
        fun natural(text: AnnotatedString, head: Boolean): Dp {
            val key = listOf(text, head, density.density, density.fontScale)
            return widthCache.get(key) ?: with(density) {
                measure.measure(text, if (head) bold else Type.body, softWrap = false, placeholders = RefIcons.placeholders(text)).size.width.toDp()
            }.also { widthCache.put(key, it) }
        }
        val long = (0 until columns).filter { col -> rows.any { it.getOrElse(col) { "" }.length > Tokens.Markdown.tableWrapChars } }
        val widths = (0 until columns).map { col -> cells.indices.maxOf { r ->
            shown[r][col]?.let { pics!!.cellWidth(it.second) } ?: natural(cells[r][col], r == 0) } }
        val least = with(density) { measure.measure("0".repeat(12), Type.body).size.width.toDp() }
        val shortSum = (0 until columns).filter { it !in long }.sumOf { widths[it].value.toDouble() }.dp
        val share = if (long.isEmpty()) 0.dp else (available - 4.dp - shortSum) / long.size - 26.dp
        val shape = RoundedCornerShape(Tokens.Markdown.tableRadius)
        Box(Modifier.clip(shape).border(1.dp, c.rule, shape)) {
            Column(Modifier.horizontalScroll(rememberScrollState())) {
                cells.forEachIndexed { r, row ->
                    val head = r == 0
                    val total = row.indices.fold(0.dp) { sum, col ->
                        sum + (if (col in long) maxOf(least, minOf(share, widths[col])) else widths[col]) + 24.dp
                    }
                    Column {
                        Row(Modifier.background(if (head) c.raised.compositeOver(under) else Color.Transparent)) {
                            row.forEachIndexed { col, text ->
                                val w = if (col in long) maxOf(least, minOf(share, widths[col])) else widths[col]
                                val pic = shown[r][col]
                                if (pic != null) Box(Modifier.width(w + 24.dp).padding(horizontal = 12.dp, vertical = 4.dp)) {
                                    pics!!.Cell(pic.first, pic.second)
                                } else Text(text, style = if (head) bold else Type.body, color = color, inlineContent = RefIcons.inline,
                                    modifier = Modifier.width(w + 24.dp).padding(horizontal = 12.dp, vertical = if (head) 5.dp else 4.dp))
                            }
                        }
                        Box(Modifier.height(1.dp).width(total).background(if (head) c.rule else c.rule.copy(alpha = c.rule.alpha * 0.5f)))
                    }
                }
            }
        }
    }
}
