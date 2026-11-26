package dev.baude.android

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.ProvideTextStyle
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.DpOffset
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.DpSize
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntRect
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.compose.ui.window.Popup
import androidx.compose.ui.window.PopupPositionProvider
import androidx.compose.ui.window.PopupProperties

/** The phone's popups, menus, pickers' lists and dialogs, as one (schema/ui's README, "Popups"; schema/tokens' popup).
 * A popup window brings its own density, the screen's: each is drawn at the page's instead (BaudeTheme's, Global
 * Scale's and Text size's), so it's the size of what it opened from. */
object Popups {
    val row = Tokens.Popup.row               // (every menu's row)
    val inset = Tokens.Popup.inset           // (a row's first and last thing, in from the sides; headings too)
    val gap = Tokens.Popup.gap               // (a check, dot or logo to its text)
    val pad = Tokens.Popup.pad               // (above the first row, below the last, either side of a divider)
    val minWidth = Tokens.Popup.minWidth
    val margin = Tokens.Popup.margin         // (off the screen's edges)
    val radius = Tokens.Radius.popover
    val dialogInset = Tokens.Popup.dialogInset
    // (a dialog's sides at the chat's text: the inside's gap, its 1px edge, then the chat's gutter)
    val dialogGutter = Gap.inside + 1.dp + Tokens.Popup.dialogGutter
}

/** Where a menu's rows put their ticks: nowhere (a menu of things to do; a toggle that's on still shows its tick at
 * its end), at the end (a picker: one of them picked, every row keeping room for the tick), or in a column before
 * every row's text (a menu of choices: the groups shown, the namespaces). */
enum class Ticks { NONE, END, START }

private val LocalTicks = staticCompositionLocalOf { Ticks.NONE }

/** A popup's own window at the page's density, placed by Placer; most: the room it has on the screen (as wide as the
 * screen less its margins, as tall as the more of the room under its anchor and over it). */
@Composable
fun PopupAt(offset: DpOffset, dismiss: () -> Unit, content: @Composable (most: DpSize) -> Unit) {
    val density = LocalDensity.current
    val view = LocalView.current
    val margin = with(density) { Popups.margin.roundToPx() }
    val off = with(density) { IntOffset(offset.x.roundToPx(), offset.y.roundToPx()) }
    val room = remember { mutableIntStateOf(Int.MAX_VALUE) }
    val placer = remember(off, margin, view) { Placer(off, offset != DpOffset.Zero, margin, view, room) }
    Popup(popupPositionProvider = placer, onDismissRequest = dismiss, properties = PopupProperties(focusable = true)) {
        val frame = android.graphics.Rect().also { view.getWindowVisibleDisplayFrame(it) }
        val most = with(density) { DpSize((frame.width() - 2 * margin).toDp(), minOf(frame.height() - 2 * margin, room.intValue).toDp()) }
        Paged(density) { content(most) }
    }
}

/** What's in a popup's window drawn at the page's density (and with the page's theme, which reaches it already). */
@Composable
private fun Paged(density: Density, content: @Composable () -> Unit) =
    CompositionLocalProvider(LocalDensity provides density, content = content)

/** A popup placed on the screen: at its anchor's start, under it (a point: offset from the anchor's bottom left, where
 * a finger pressed), else ending at its end, over it; kept margin off the screen's edges. The anchor's in its own
 * window, which may be a dialog's, away from the screen's corner: placed against the screen, then given back in it.
 * room: the more of the room under it and over it, told to the popup, which is no taller. */
private class Placer(val off: IntOffset, val point: Boolean, val margin: Int, val view: android.view.View,
                     val room: androidx.compose.runtime.MutableIntState) : PopupPositionProvider {
    override fun calculatePosition(anchorBounds: IntRect, windowSize: IntSize, layoutDirection: LayoutDirection,
                                   popupContentSize: IntSize): IntOffset {
        val origin = IntArray(2).also { view.rootView.getLocationOnScreen(it) }
        val f = android.graphics.Rect().also { view.getWindowVisibleDisplayFrame(it) }
        val a = anchorBounds.translate(origin[0], origin[1])
        val w = popupContentSize.width
        val h = popupContentSize.height
        val px = a.left + off.x
        val py = a.bottom + off.y
        val top = if (point) py else a.top // (the popup's bottom, over it)
        val under = f.bottom - margin - py
        val over = top - f.top - margin
        maxOf(under, over, 0).let { if (room.intValue != it) room.intValue = it }
        val x = if (px + w <= f.right - margin) px else (if (point) px else a.right) - w
        val y = if (h <= under || under >= over) py else top - h
        return IntOffset(x.coerceIn(f.left + margin, maxOf(f.left + margin, f.right - margin - w)) - origin[0],
            y.coerceIn(f.top + margin, maxOf(f.top + margin, f.bottom - margin - h)) - origin[1])
    }
}

/** A menu as libadwaita's popover is: its own fill, rounded, its rows (MenuChoice, MenuRow), headings and dividers;
 * as wide as its widest row (or width), on the screen, scrolling past the screen's height. ticks: where its ticks go. */
@Composable
fun Popover(open: Boolean, dismiss: () -> Unit, offset: DpOffset = DpOffset.Zero, ticks: Ticks = Ticks.NONE,
            width: Dp? = null, content: @Composable ColumnScope.() -> Unit) {
    if (!open) return
    val c = LocalBaude.current
    PopupAt(offset, dismiss) { most ->
        val shape = RoundedCornerShape(Popups.radius)
        CompositionLocalProvider(LocalTicks provides ticks) {
            Column((if (width != null) Modifier.width(width) else Modifier.widthIn(min = Popups.minWidth, max = most.width))
                .heightIn(max = most.height).shadow(8.dp, shape).clip(shape).background(c.dialog)
                .then(if (width != null) Modifier else Modifier.width(IntrinsicSize.Max))
                .verticalScroll(rememberScrollState()).padding(vertical = Popups.pad), content = content)
        }
    }
}

/** A menu's row: Popups.row high, its things Popups.inset in from the sides and Popups.gap apart; pressed and held:
 * onLongClick. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun MenuLine(enabled: Boolean = true, onLongClick: (() -> Unit)? = null, onClick: () -> Unit,
             content: @Composable RowScope.() -> Unit) {
    Row(Modifier.fillMaxWidth().heightIn(min = Popups.row)
        .combinedClickable(enabled = enabled, onClick = onClick, onLongClick = onLongClick)
        .padding(horizontal = Popups.inset), verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(Popups.gap), content = content)
}

/** A menu's item, checked if it's the one (or one of those) picked: its tick where the menu's Ticks put it; dot: a
 * colour before it (the accents'); onLongClick: pressed and held. */
@Composable
fun MenuChoice(label: String, on: Boolean, enabled: Boolean = true, textColor: Color? = null, dot: Color? = null,
               onLongClick: (() -> Unit)? = null, onClick: () -> Unit) {
    val c = LocalBaude.current
    val ticks = LocalTicks.current
    MenuLine(enabled, onLongClick, onClick) {
        if (ticks == Ticks.START) Tick(on, enabled)
        dot?.let { Box(Modifier.size(14.dp).clip(CircleShape).background(it)) }
        Text(label, style = Type.body, color = textColor ?: if (enabled) c.fg else c.dim, maxLines = 1,
            overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
        if (ticks == Ticks.END || ticks == Ticks.NONE && on) Tick(on, enabled)
    }
}

/** A square check for each of a list of things and one over them all, as the Kubernetes page's Namespaces and home's ⋮
 * → Groups have them (Baude's pickers.CheckList): those checked are shown, in the order checked. Every one shown is
 * every one checked (null), the one over them checked too; tapping that one checks every one, or unchecks every one
 * (an empty list) while they all are. items: (key, label); pick(the keys shown, null for every one); onLongClick(key):
 * one pressed and held; heading: the one over them reads as a section heading (the menu's "Group by"), its check kept;
 * dimmed: the keys whose labels are greyed (a column filter's values no row has), still there to tap. */
@Composable
fun MenuChecks(allLabel: String, items: List<Pair<String, String>>, shown: List<String>?, pick: (List<String>?) -> Unit,
               onLongClick: ((String) -> Unit)? = null, heading: Boolean = false, dimmed: Set<String> = emptySet()) {
    val keys = items.map { it.first }
    MenuCheck(allLabel, shown == null, heading = heading) { pick(if (shown == null) emptyList() else null) }
    MenuDivider()
    Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState())) {
        for ((key, label) in items) {
            val on = shown == null || key in shown
            MenuCheck(label, on, onLongClick = onLongClick?.let { { it(key) } }, dim = key in dimmed) {
                val next = (shown ?: keys).filter { it != key } + if (on) emptyList() else listOf(key)
                pick(if (next.isNotEmpty() && next.toSet().containsAll(keys)) null else next)
            }
        }
    }
}

/** A row with a square check before its label (libadwaita's check button): the accent filled with a tick when on, an
 * outline when off. heading: its label as MenuHeading's. */
@Composable
fun MenuCheck(label: String, on: Boolean, heading: Boolean = false, onLongClick: (() -> Unit)? = null,
              dim: Boolean = false, onClick: () -> Unit) {
    val c = LocalBaude.current
    MenuLine(true, onLongClick, onClick) {
        val box = Modifier.size(16.dp.icon).clip(RoundedCornerShape(3.dp))
        if (on) Box(box.background(c.accentBg), contentAlignment = Alignment.Center) {
            Icon(Adwaita.ObjectSelect, null, tint = c.accentFg, modifier = Modifier.size(14.dp.icon))
        } else Box(box.border(1.dp, c.dim, RoundedCornerShape(3.dp)))
        Text(label, style = if (heading) Type.folder else Type.body, color = if (heading || dim) c.dim else c.fg, maxLines = 1,
            overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
    }
}

/** A menu item that opens a page of the menu: its label, ▸ at the end, where a picker's tick is. */
@Composable
fun MenuRow(label: String, onClick: () -> Unit) {
    val c = LocalBaude.current
    MenuLine(onClick = onClick) {
        if (LocalTicks.current == Ticks.START) Tick(false)
        Text(label, style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
        Icon(Adwaita.GoNext, null, tint = c.fg, modifier = Modifier.size(16.dp.icon))
    }
}

/** A menu page's head, going back to the menu: ◂, its name in bold. */
@Composable
fun MenuBack(label: String, onClick: () -> Unit) {
    val c = LocalBaude.current
    MenuLine(onClick = onClick) {
        Icon(Adwaita.GoPrevious, null, tint = c.fg, modifier = Modifier.size(16.dp.icon))
        Text(label, style = Type.bold, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
    }
}

/** The tick of a row picked (the room of one where it isn't). */
@Composable
private fun Tick(on: Boolean, enabled: Boolean = true) {
    val c = LocalBaude.current
    if (on) Icon(Adwaita.ObjectSelect, null, tint = if (enabled) c.accent else c.dim, modifier = Modifier.size(16.dp.icon))
    else Spacer(Modifier.size(16.dp.icon))
}

/** A menu's heading: small, bold and dim, where its rows' first things are. */
@Composable
fun MenuHeading(text: String) {
    val c = LocalBaude.current
    Text(text, style = Type.folder, color = c.dim, maxLines = 1, overflow = TextOverflow.Ellipsis,
        modifier = Modifier.padding(start = Popups.inset, end = Popups.inset, top = Popups.pad, bottom = Popups.pad / 2))
}

/** A menu's divider: the rule, Popups.pad either side of it. */
@Composable
fun MenuDivider() {
    HorizontalDivider(Modifier.padding(vertical = Popups.pad), color = LocalBaude.current.rule)
}

/** A dialog (libadwaita's AlertDialog; Material's, laid out as the phone's): the chat's column wide (Popups.dialogGutter
 * in from the chat's text's sides: the screen's, or, with Full-width text off and the column clamped, the column's), at the page's density; its heading, its body (dim), then its buttons at the end, all
 * Popups.dialogInset in from its edges. Every dialog of the app is this one. */
@Composable
fun AlertDialog(onDismissRequest: () -> Unit, confirmButton: @Composable () -> Unit, modifier: Modifier = Modifier,
                dismissButton: (@Composable () -> Unit)? = null, title: (@Composable () -> Unit)? = null, text: (@Composable () -> Unit)? = null,
                containerColor: Color = LocalBaude.current.dialog) {
    val density = LocalDensity.current
    Dialog(onDismissRequest, DialogProperties(usePlatformDefaultWidth = false)) {
        Paged(density) {
            val c = LocalBaude.current
            BoxWithConstraints(Modifier.fillMaxWidth()) {
            val room = maxWidth - (Gap.inside + 1.dp) * 2 // (the chat's box, as the chat is in it)
            val side = if (Settings.fullWidth.value) 0.dp else (room - chatColumn(room)) / 2
            Column(modifier.fillMaxWidth().padding(horizontal = Popups.dialogGutter + side).clip(RoundedCornerShape(Popups.radius))
                .background(containerColor).padding(Popups.dialogInset)) {
                title?.let {
                    CompositionLocalProvider(LocalContentColor provides c.fg) { ProvideTextStyle(Type.dialogTitle, it) }
                    Spacer(Modifier.height(Popups.dialogInset / 2))
                }
                text?.let {
                    Box(Modifier.weight(1f, fill = false)) {
                        CompositionLocalProvider(LocalContentColor provides c.dim) { ProvideTextStyle(Type.body, it) }
                    }
                }
                Row(Modifier.align(Alignment.End).padding(top = Popups.dialogInset),
                    horizontalArrangement = Arrangement.spacedBy(Popups.gap), verticalAlignment = Alignment.CenterVertically) {
                    dismissButton?.invoke()
                    confirmButton()
                }
            }
            }
        }
    }
}
