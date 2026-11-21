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
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** A Bot's picture, or its initials on libadwaita's gradient for its name (as AdwAvatar draws one). */
@Composable
fun Avatar(name: String, picture: ImageBitmap?, size: Dp = Gap.avatar) {
    if (picture != null) {
        Image(picture, null, Modifier.size(size).clip(CircleShape), contentScale = ContentScale.Crop)
        return
    }
    val (top, bottom, fg) = avatarColors(name)
    Box(
        Modifier.size(size).clip(CircleShape).background(Brush.verticalGradient(listOf(top, bottom))),
        contentAlignment = Alignment.Center,
    ) {
        Text(initials(name), color = fg, fontWeight = FontWeight.Bold, fontSize = (size.value * 0.4f).sp)
    }
}

/** A chat's state, as Baude's StatusDot: working (grey, pulsing), waiting (asking: yellow); nothing between turns. */
@Composable
fun StatusDot(state: String, asking: Boolean) {
    val c = LocalBaude.current
    val colour = when {
        asking -> c.warning
        state == "working" -> c.fg.copy(alpha = 0.45f)
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
    if (lead == null && !left) { // (in the middle of the bar, as Adw.HeaderBar's: the room either side of it as wide as the wider end's)
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
            val side = maxOf(sp.width, ep.width)
            val mp = m.measure(loose.copy(maxWidth = (k.maxWidth - 2 * side).coerceAtLeast(0)))
            layout(k.maxWidth, k.maxHeight) {
                sp.place(0, (k.maxHeight - sp.height) / 2)
                mp.place((k.maxWidth - mp.width) / 2, (k.maxHeight - mp.height) / 2)
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

/** A flat button as libadwaita's in a bar: an icon, 34 square, rounded. */
@Composable
fun IconButton(icon: ImageVector, label: String, enabled: Boolean = true, tint: androidx.compose.ui.graphics.Color? = null,
               background: androidx.compose.ui.graphics.Color? = null, onClick: () -> Unit) {
    val c = LocalBaude.current
    Box(
        Modifier.size(Gap.composerButton).clip(RoundedCornerShape(8.dp))
            .then(if (background != null) Modifier.background(background) else Modifier)
            .clickable(enabled = enabled, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Icon(icon, label, tint = (tint ?: c.fg).copy(alpha = if (enabled) 1f else 0.4f), modifier = Modifier.size(18.dp.icon))
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
fun Button(label: String, suggested: Boolean = false, destructive: Boolean = false, onClick: () -> Unit) {
    val c = LocalBaude.current
    Box(
        Modifier.clip(RoundedCornerShape(8.dp))
            .background(if (suggested) c.accentBg else c.fg.copy(alpha = 0.10f))
            .clickable(onClick = onClick).padding(horizontal = 16.dp, vertical = 9.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(label, style = Type.bold, color = when {
            suggested -> c.accentFg
            destructive -> c.destructive
            else -> c.fg
        })
    }
}

/** Set into the frame as Baude's inside is (widgets.inside, .baude-inside): Gap.inside in from the frame's sides and
 * bottom, square, its 1px edge the rule, the inside's fill. */
fun Modifier.inside(c: BaudeColors): Modifier = this.padding(start = Gap.inside, end = Gap.inside, bottom = Gap.inside)
    .border(1.dp, c.rule).background(c.inside).padding(1.dp)

/** A box with Baude's one edge (.tool-card's: a 1px rule, rounded 8). */
fun Modifier.card(c: BaudeColors): Modifier =
    this.border(1.dp, c.rule, RoundedCornerShape(Gap.cardRadius)).padding(horizontal = Gap.cardH, vertical = Gap.cardV)
