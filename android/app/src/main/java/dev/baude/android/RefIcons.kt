package dev.baude.android

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
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
import kotlin.math.cos
import kotlin.math.sin

/** A logo's shapes, in widgets.REF_ICON_SVG's 16 by 16: a path, circle or rectangle, filled or stroked in the text's colour. */
private sealed interface Shape
private class P(val d: String, val fill: Boolean = false, val width: Float = 1.35f) : Shape
private class C(val x: Float, val y: Float, val r: Float, val fill: Boolean = false, val width: Float = 1.35f) : Shape
private class Rr(val x: Float, val y: Float, val w: Float, val h: Float, val rx: Float, val fill: Boolean = false, val width: Float = 1.35f) : Shape

/** A logo: its shapes, and for a cut-out one (git, Kubernetes, a database) what's cut from them. */
private class Logo(val shapes: List<Shape>, val cut: List<Shape> = emptyList())

private val heptagon = (0 until 7).map { -Math.PI / 2 + 2 * Math.PI * it / 7 }
private fun f2(x: Double) = String.format(java.util.Locale.ROOT, "%.2f", x)
private val wheel = heptagon.joinToString("") { a ->
    "M${f2(8 + 1.15 * cos(a))} ${f2(8 + 1.15 * sin(a))}L${f2(8 + 5.1 * cos(a))} ${f2(8 + 5.1 * sin(a))}"
}
private val heptagonPath = "M" + heptagon.joinToString("L") { a -> "${f2(8 + 7.6 * cos(a))} ${f2(8 + 7.6 * sin(a))}" } + "Z"

private val git = Logo(listOf(P("M8 .5 15.5 8 8 15.5.5 8z", fill = true)), listOf(
    P("M6.3 4.4v7.2M6.6 4.7l3.4 3.4", width = 1.3f), C(6.3f, 4.4f, 1.35f, true), C(6.3f, 11.6f, 1.35f, true), C(10.2f, 8.3f, 1.35f, true)))
private val issue = Logo(listOf(P("M6.4 2 4.8 14M11.2 2 9.6 14M2.6 5.7h11.4M2 10.3h11.4", width = 1.6f)))

/** widgets.REF_ICON_SVG's logos (a pull request has the branch's, an issue's closed one the #'s). */
private val LOGOS: Map<String, Logo> = mapOf(
    "git" to git,
    "k8s" to Logo(listOf(P(heptagonPath, fill = true)), listOf(C(8f, 8f, 3.1f, width = 1.25f), P(wheel, width = 1.15f), C(8f, 8f, 1.05f, true))),
    "db" to Logo(listOf(P("M2.5 3.5C2.5 2.2 5 1.2 8 1.2s5.5 1 5.5 2.3v9c0 1.3-2.5 2.3-5.5 2.3s-5.5-1-5.5-2.3z", fill = true)),
        listOf(P("M2.5 6.2c0 1.3 2.5 2.3 5.5 2.3s5.5-1 5.5-2.3M2.5 10c0 1.3 2.5 2.3 5.5 2.3s5.5-1 5.5-2.3", width = 1.1f))),
    "arango" to Logo(listOf(P("M4.3 4.6 11.6 6.4M4.3 4.6l2.4 7.3M11.6 6.4l-4.9 5.5"), C(4.3f, 4.6f, 2.3f, true),
        C(11.6f, 6.4f, 2.3f, true), C(6.7f, 11.9f, 2.3f, true))),
    "file" to Logo(listOf(P("M3.5 1.5h5.8l3.2 3.2v9.8h-9zM9 1.5V5h3.5"))),
    "folder" to Logo(listOf(P("M1.5 3.5h4.5l1.5 1.5h7v8.5h-13z"))),
    "code" to Logo(listOf(P("M6 2.5c-1.5 0-2 .6-2 2v1.6c0 .9-.5 1.4-1.5 1.4.9 0 1.5.5 1.5 1.4v1.6c0 1.4.5 2 2 2M10 2.5c1.5 0 2 .6 2 2v1.6c0 .9.5 1.4 1.5 1.4-1 0-1.5.5-1.5 1.4v1.6c0 1.4-.5 2-2 2", width = 1.6f))),
    "env" to Logo(listOf(P("M10.8 4.6C10.3 3.6 9.3 3 8 3 6.4 3 5.2 3.9 5.2 5.2c0 3 5.8 1.7 5.8 4.9 0 1.4-1.3 2.4-3 2.4-1.4 0-2.6-.7-3.1-1.8M8 1.3v13.4", width = 1.5f))),
    "route" to Logo(listOf(C(8f, 8f, 6.2f), P("M1.8 8h12.4M8 1.8c-3.3 3.5-3.3 8.9 0 12.4M8 1.8c3.3 3.5 3.3 8.9 0 12.4"))),
    "config" to Logo(listOf(P("M2 4h12M2 8h12M2 12h12"), C(5f, 4f, 1.8f, true), C(10.5f, 8f, 1.8f, true), C(6.5f, 12f, 1.8f, true))),
    "cli" to Logo(listOf(Rr(1.5f, 2.5f, 13f, 11f, 2f), P("M4.5 6.5 7 8.5l-2.5 2M8.5 11h3"))),
    "package" to Logo(listOf(P("M8 1.5 14 4.7v6.6L8 14.5 2 11.3V4.7zM2 4.7 8 8l6-3.3M8 8v6.5"))),
    "service" to Logo(listOf(Rr(1.5f, 7f, 13f, 7f, 1.8f, true), P("M3 3.8h3v2.4H3zM6.6 3.8h3v2.4h-3zM6.6.9h3v2.4h-3z", fill = true))),
    "issue" to issue,
    "claude" to Logo(listOf(P("M8 1.5v13M2.4 4.8l11.2 6.4M2.4 11.2l11.2-6.4", width = 1.9f))),
    "external" to Logo(listOf(P("M9 2.5h4.5V7M13.5 2.5 7 9M11 9.5v3.5H3V5h3.5"))),
    "pr_open" to git, "pr_merged" to git, "pr_closed" to git, "issue_closed" to issue,
)

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
    fun Draw(name: String, tint: Color, size: Dp = 16.dp.icon) {
        val logo = LOGOS[name] ?: return
        val color = own(name) ?: tint
        Canvas(Modifier.size(size).graphicsLayer { compositingStrategy = CompositingStrategy.Offscreen }) {
            scale(this.size.width / 16f, Offset.Zero) {
                for (s in logo.shapes) draw(s, color)
                for (s in logo.cut) draw(s, Color.Black, clear = true)
            }
        }
    }

    /** What a text's inline logos are made of, by "ref-icon-<name>-<ok|stale>": a logo before a link, the link's colour
     * (the accent, or amber once stale). */
    val inline: Map<String, InlineTextContent> by lazy {
        LOGOS.keys.flatMap { n -> listOf("ok", "stale").map { st -> n to st } }.associate { (n, st) ->
            "ref-icon-$n-$st" to InlineTextContent(Placeholder(1.em, 1.em, PlaceholderVerticalAlign.TextCenter)) {
                Draw(n, if (st == "stale") Tokens.Signal.stale else LocalBaude.current.accent, 16.dp.icon)
            }
        }
    }

    /** The placeholders of the logos in a text, for measuring it (a table cell's width). */
    fun placeholders(text: AnnotatedString): List<AnnotatedString.Range<Placeholder>> =
        text.text.indices.filter { text.text[it] == SLOT[0] }.map {
            AnnotatedString.Range(Placeholder(1.em, 1.em, PlaceholderVerticalAlign.TextCenter), it, it + 1)
        }
}
