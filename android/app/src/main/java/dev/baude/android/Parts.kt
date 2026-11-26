package dev.baude.android

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.sqrt

/** Someone's or a Bot's picture, or their initials on libadwaita's gradient for their name (as AdwAvatar draws one),
 * set into the page under .picture-shade (imageviews.shaded_picture). */
@Composable
fun Avatar(name: String, picture: ImageBitmap?, size: Dp = Gap.avatar) {
    if (picture != null) {
        Image(picture, null, Modifier.size(size).clip(CircleShape).pictureShade(), contentScale = ContentScale.Crop)
        return
    }
    val (top, bottom, fg) = avatarColors(name)
    Box(
        Modifier.size(size).clip(CircleShape).background(Brush.verticalGradient(listOf(top, bottom))).pictureShade(),
        contentAlignment = Alignment.Center,
    ) {
        Text(initials(name), color = fg, fontWeight = FontWeight.Bold, fontSize = (size.value * 0.4f).sp)
    }
}

/** .picture-shade over a round picture: CSS's three inset shadows (Tokens.PictureShade), each a radial gradient that
 * fades as a Gaussian blur does (sigma half the blur) from the edge of its circle, moved down by its offset. */
private fun Modifier.pictureShade() = drawWithContent {
    drawContent()
    val s = Tokens.PictureShade
    val r = size.minDimension / 2
    fun inset(dy: Float, blur: Float, colour: Color) {
        val sigma = (blur / 2).coerceAtLeast(0.01f)
        val reach = r + abs(dy) + 1f // (as far as the picture's farthest point from the moved circle's middle)
        val from = (r - 3 * sigma).coerceAtLeast(0f)
        val stops = (0..12).map { i ->
            val at = from + (reach - from) * i / 12
            at / reach to colour.copy(alpha = colour.alpha * gaussTail((r - at) / sigma))
        }
        val all = (if (from > 0f) listOf(0f to Color.Transparent) else emptyList()) + stops
        drawCircle(Brush.radialGradient(*all.toTypedArray(), center = center + Offset(0f, dy), radius = reach), r)
    }
    inset(0f, s.blur.toPx(), s.blurColor)
    inset(s.topY.toPx(), s.topBlur.toPx(), s.topColor)
    val w = s.ring.toPx()
    drawCircle(s.ringColor, r - w / 2, style = Stroke(w))
}

/** How much of a unit Gaussian lies beyond x (1 - its CDF): Abramowitz and Stegun's 7.1.26 for erf. */
private fun gaussTail(x: Float): Float {
    val z = abs(x) / sqrt(2f)
    val t = 1f / (1f + 0.3275911f * z)
    val erf = 1f - (((((1.061405429f * t - 1.453152027f) * t) + 1.421413741f) * t - 0.284496736f) * t + 0.254829592f) * t * exp(-z * z)
    return if (x >= 0) (1f - erf) / 2 else (1f + erf) / 2
}

private val DONE = androidx.compose.ui.graphics.Color(0xFF3584E4) // (Baude's .status-dot.done)

/** A chat's state, as Baude's StatusDot: working (grey, pulsing), waiting (asking: yellow), done (blue: where it's
 * said, a run's Started); nothing between turns. */
@Composable
fun StatusDot(state: String, asking: Boolean) {
    val c = LocalBaude.current
    val colour = when {
        asking -> c.warning
        state == "working" -> c.fg.copy(alpha = 0.45f)
        state == "done" -> DONE
        else -> return
    }
    val pulse by rememberInfiniteTransition(label = "dot").animateFloat(
        1f, if (state == "working" && !asking) 0.35f else 1f,
        infiniteRepeatable(tween(Tokens.Motion.pulseMs), RepeatMode.Reverse), label = "pulse",
    )
    Box(Modifier.size(Gap.dot).alpha(pulse).clip(CircleShape).background(colour))
}

/** A bar as Adw.HeaderBar is: 46 tall, the frame's colour, its title in the middle (at the start: left, as the app's
 * own name on its home screen is, with no window's buttons beside it). */
@Composable
fun Bar(title: String, start: (@Composable RowScope.() -> Unit)? = null, end: (@Composable RowScope.() -> Unit)? = null,
        subtitle: String? = null, lead: (@Composable () -> Unit)? = null, left: Boolean = false) {
    val c = LocalBaude.current
    val bar = Modifier.fillMaxWidth().background(c.frame).statusBarsPadding().height(Gap.bar).padding(horizontal = 6.dp)
    if (lead == null && !left) { // (in the middle of the bar, as Adw.HeaderBar's; an end too wide for that: pushed
                                 // aside, between the two ends)
        androidx.compose.ui.layout.Layout({
            Row(verticalAlignment = Alignment.CenterVertically) { if (start != null) start() }
            Column(Modifier.padding(horizontal = 6.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                Text(title, style = Type.title, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis)
                if (subtitle != null) Text(subtitle, style = Type.caption, color = c.dim, maxLines = 1,
                    overflow = TextOverflow.Ellipsis)
            }
            Row(verticalAlignment = Alignment.CenterVertically) { if (end != null) end() }
        }, bar) { (s, m, e), k ->
            val loose = k.copy(minWidth = 0, minHeight = 0)
            val sp = s.measure(loose)
            val ep = e.measure(loose)
            val mp = m.measure(loose.copy(maxWidth = (k.maxWidth - sp.width - ep.width).coerceAtLeast(0)))
            val x = ((k.maxWidth - mp.width) / 2).coerceAtMost(k.maxWidth - ep.width - mp.width).coerceAtLeast(sp.width)
            layout(k.maxWidth, k.maxHeight) {
                sp.place(0, (k.maxHeight - sp.height) / 2)
                mp.place(x, (k.maxHeight - mp.height) / 2)
                ep.place(k.maxWidth - ep.width, (k.maxHeight - ep.height) / 2)
            }
        }
        return
    }
    Row(bar, verticalAlignment = Alignment.CenterVertically) {
        if (start != null) start()
        Row(Modifier.weight(1f).padding(horizontal = 6.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = if (lead == null && !left) Arrangement.Center else Arrangement.Start) {
            if (lead != null) {
                lead()
                Spacer(Modifier.width(Gap.avatarGap))
            }
            Column(horizontalAlignment = if (lead == null && !left) Alignment.CenterHorizontally else Alignment.Start) {
                Text(title, style = Type.title, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis)
                if (subtitle != null) Text(subtitle, style = Type.caption, color = c.dim, maxLines = 1,
                    overflow = TextOverflow.Ellipsis)
            }
        }
        if (end != null) end()
    }
}

/** A flat button as libadwaita's in a bar: an icon, 34 square (side: the box you write in's are smaller), rounded. */
@Composable
fun IconButton(icon: ImageVector, label: String, enabled: Boolean = true, tint: androidx.compose.ui.graphics.Color? = null,
               background: androidx.compose.ui.graphics.Color? = null, tip: String? = null,
               side: androidx.compose.ui.unit.Dp = Gap.barButton, onClick: () -> Unit) {
    val c = LocalBaude.current
    Box(
        Modifier.size(side).clip(RoundedCornerShape(8.dp))
            .then(if (background != null) Modifier.background(background) else Modifier)
            .then(if (tip != null) Modifier.tip(tip) { if (enabled) onClick() } else Modifier.clickable(enabled = enabled, onClick = onClick)),
        contentAlignment = Alignment.Center,
    ) {
        Icon(icon, label, tint = (tint ?: c.fg).copy(alpha = if (enabled) 1f else 0.4f), modifier = Modifier.size(16.dp.icon))
    }
}

/** A grey line across the top, as the Memory page's can't-connect banner is. */
@Composable
fun GreyBanner(text: String, onClick: (() -> Unit)? = null) {
    val c = LocalBaude.current
    Text(
        text, style = Type.caption, color = c.fg.copy(alpha = c.fg.alpha * 0.75f),
        modifier = Modifier.fillMaxWidth().background(c.fg.copy(alpha = 0.08f))
            .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier)
            .padding(horizontal = 12.dp, vertical = 8.dp),
    )
}

/** A button as libadwaita's pill-less ones: suggested (the accent), or plain (raised). */
@Composable
fun Button(label: String, suggested: Boolean = false, destructive: Boolean = false, enabled: Boolean = true,
           modifier: Modifier = Modifier, onClick: () -> Unit) {
    val c = LocalBaude.current
    Box(
        modifier.clip(RoundedCornerShape(8.dp))
            .background((if (suggested) c.accentBg else c.fg.copy(alpha = 0.10f)).let { if (enabled) it else it.copy(alpha = it.alpha * 0.4f) })
            .clickable(enabled = enabled, onClick = onClick).padding(horizontal = 16.dp, vertical = 9.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(label, style = Type.bold, color = when {
            suggested -> c.accentFg
            destructive -> c.destructive
            else -> c.fg
        }.let { if (enabled) it else it.copy(alpha = it.alpha * 0.4f) })
    }
}

/** Set into the frame as Baude's inside is (widgets.inside, .baude-inside): Gap.inside in from the frame's sides and
 * bottom, square, its 1px edge the rule, the inside's fill. */
fun Modifier.inside(c: BaudeColors): Modifier = this.padding(start = Gap.inside, end = Gap.inside, bottom = Gap.inside)
    .border(1.dp, c.rule).background(c.inside).padding(1.dp)

/** A box with Baude's one edge (.tool-card's: a 1px rule, rounded 8). */
fun Modifier.card(c: BaudeColors): Modifier =
    this.border(1.dp, c.rule, RoundedCornerShape(Gap.cardRadius)).padding(horizontal = Gap.cardH, vertical = Gap.cardV)

/** Each row's levels whose parent has a child further down (its line runs on past the row, top to bottom): a level
 * is open from a row at depth level + 1 up to the first row above that's at that depth or less. */
fun through(depths: List<Int>): List<Set<Int>> {
    val open = HashSet<Int>()
    val out = ArrayList<Set<Int>>(depths.size)
    for (depth in depths.asReversed()) {
        out.add(open.filter { it < depth }.toSet())
        open.removeAll { it >= depth }
        if (depth > 0) open.add(depth - 1)
    }
    return out.asReversed()
}

/** A nested row's part of its parents' lines, as Baude's TreeList draws them, one joined line per parent: down from
 * under the parent's title to its last child's middle, then a corner across to just short of that child's title;
 * through: the levels whose line runs on past this row. start: where the row's titles start in; lineX: how far into
 * a parent's title its line runs; root: where the top-level parent's anchor starts in (a Bot's picture is further
 * out than a title). */
fun Modifier.treeLines(depth: Int, through: Set<Int>, color: Color, start: Dp = 6.dp, lineX: Dp = 5.dp,
                       root: Dp = start): Modifier =
    if (depth == 0) this else drawBehind {
        val w = 1.dp.toPx()
        val mid = (1.dp.toPx() + size.height) / 2 // (the title's middle: under the 1dp gap above the row)
        for (k in 0 until depth) {
            val x = ((if (k == 0) root else start + 18.dp * k) + lineX).toPx()
            if (k in through) drawRect(color, Offset(x, 0f), Size(w, size.height))
            else if (k == depth - 1) {
                drawRect(color, Offset(x, 0f), Size(w, mid + w))
                drawRect(color, Offset(x + w, mid), Size((start + 18.dp * depth - 4.dp).toPx() - x - w, w))
            }
        }
    }

/** The top of a parent row's line to its children (TreeList's): down at x from 1dp under its anchor (the title, a
 * Bot's picture) to the row's foot, where the first child's part of it carries on. under: the anchor's foot. */
fun Modifier.treeStem(x: Dp, color: Color, under: DrawScope.() -> Float): Modifier = drawBehind {
    val top = under() + 1.dp.toPx()
    if (top < size.height) drawRect(color, Offset(x.toPx(), top), Size(1.dp.toPx(), size.height - top))
}
