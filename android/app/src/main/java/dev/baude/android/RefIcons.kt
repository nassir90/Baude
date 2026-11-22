package dev.baude.android

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.size
import androidx.compose.ui.Alignment
import androidx.compose.runtime.Composable
import androidx.compose.foundation.text.InlineTextContent
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.CompositingStrategy
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Fill
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.scale
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.vector.PathParser
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.Placeholder
import androidx.compose.ui.text.PlaceholderVerticalAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import dev.baude.android.LinkLogos.C
import dev.baude.android.LinkLogos.Logo
import dev.baude.android.LinkLogos.P
import dev.baude.android.LinkLogos.Rr
import dev.baude.android.LinkLogos.Shape

/** The links' logos (LinkLogos: schema/icons/links), and a pull request's (the branch's) and a closed issue's (the
 * #'s), in a colour of their own. */
private val LOGOS: Map<String, Logo> = LinkLogos.all.let { all ->
    all + mapOf("pr_open" to all.getValue("git"), "pr_merged" to all.getValue("git"), "pr_closed" to all.getValue("git"),
        "issue_closed" to all.getValue("issue"))
}

private val parsed = HashMap<String, Path>()
private fun path(d: String): Path = parsed.getOrPut(d) { PathParser().parsePathString(d).toPath() }

private fun DrawScope.draw(s: Shape, color: Color, clear: Boolean = false) {
    val mode = if (clear) BlendMode.Clear else BlendMode.SrcOver
    fun stroke(w: Float) = Stroke(width = w, cap = StrokeCap.Round, join = StrokeJoin.Round)
    when (s) {
        is P -> drawPath(path(s.d), color, style = if (s.fill) Fill else stroke(s.width), blendMode = mode)
        is C -> drawCircle(color, s.r, Offset(s.x, s.y), style = if (s.fill) Fill else stroke(s.width), blendMode = mode)
        is Rr -> drawRoundRect(color, Offset(s.x, s.y), Size(s.w, s.h), androidx.compose.ui.geometry.CornerRadius(s.rx, s.rx),
            style = if (s.fill) Fill else stroke(s.width), blendMode = mode)
    }
}

/** The tint a logo has of its own (widgets.REF_ICON_TINTS: where a pull request or an issue stands), or null. */
private fun own(name: String): Color? = when (name) {
    "pr_open" -> Tokens.Signal.prOpen
    "pr_merged" -> Tokens.Signal.prMerged
    "pr_closed", "issue_closed" -> Tokens.Signal.prClosed
    else -> null
}

/** One of the logos, drawn as vectors, size square (16 dp at the body's size), in tint or its own colour. */
object RefIcons {
    private const val SLOT = "⁣" // (U+2063: where a logo goes in a line's text)

    /** The text of the place a logo is inlined at. */
    const val slot = SLOT

    @Composable
    fun Draw(name: String, tint: Color, size: Dp = 16.dp.icon) = Paint(name, tint, Modifier.size(size))

    /** The logo in front of a link in text: in the text's colour, 0.78 of the line's height, at the start of its 1 em,
     * the rest of it the gap before the name. */
    @Composable
    private fun Inline(name: String) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.CenterStart) {
            Paint(name, LocalBaude.current.fg, Modifier.fillMaxHeight(0.78f).aspectRatio(1f))
        }
    }

    @Composable
    private fun Paint(name: String, tint: Color, modifier: Modifier) {
        val logo = LOGOS[name] ?: return
        val color = own(name) ?: tint
        Canvas(modifier.graphicsLayer { compositingStrategy = CompositingStrategy.Offscreen }) {
            scale(this.size.width / 16f, Offset.Zero) {
                for (s in logo.shapes) draw(s, color)
                for (s in logo.cut) draw(s, Color.Black, clear = true)
            }
        }
    }

    /** What a text's inline logos are made of, by "ref-icon-<name>": a logo before a link. */
    val inline: Map<String, InlineTextContent> by lazy {
        LOGOS.keys.associate { n ->
            "ref-icon-$n" to InlineTextContent(Placeholder(1.em, 1.em, PlaceholderVerticalAlign.TextCenter)) { Inline(n) }
        }
    }

    /** The placeholders of the logos in a text, for measuring it (a table cell's width). */
    fun placeholders(text: AnnotatedString): List<AnnotatedString.Range<Placeholder>> =
        text.text.indices.filter { text.text[it] == SLOT[0] }.map {
            AnnotatedString.Range(Placeholder(1.em, 1.em, PlaceholderVerticalAlign.TextCenter), it, it + 1)
        }
}
