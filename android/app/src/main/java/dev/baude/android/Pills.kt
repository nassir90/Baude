package dev.baude.android

import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.layout
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.PathParser
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.Placeholder
import androidx.compose.ui.text.PlaceholderVerticalAlign
import androidx.compose.ui.text.TextLayoutResult
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.foundation.text.BasicText
import androidx.compose.foundation.text.appendInlineContent
import androidx.compose.foundation.text.InlineTextContent
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.json.JSONArray
import kotlin.math.roundToInt

// ------------------------------------------------------------------------------------------------- the logo --

/** A pull request's colour by its state (widgets.PR_COLORS); one not known is the accent. */
fun prColor(state: String?, c: BaudeColors): Color = when (state) {
    "open" -> Tokens.Git.prOpen
    "draft" -> Tokens.Git.prDraft
    "changes" -> Tokens.Git.prChanges
    "merged" -> Tokens.Git.prMerged
    "closed" -> Tokens.Git.prClosed
    else -> c.accent
}

// ------------------------------------------------------------------------------------------------- the text --

private const val SEP = "\u001f"
private const val INLINE_TAG = "androidx.compose.foundation.text.inlineContent"

/** A pill in text: a pull request's number with the merge logo, in its state's colour, a link to its page; one
 * unbreakable piece, as the desktop's. */
fun AnnotatedString.Builder.pill(label: String, state: String?, href: String, scale: Float) =
    appendInlineContent(listOf("pill", state ?: "unknown", scale.toString(), href, label).joinToString(SEP), label)

/** A branch or tag in text, as a chip (kind: head, local, remote, tag or chat). */
fun AnnotatedString.Builder.chip(label: String, kind: String) =
    appendInlineContent(listOf("chip", kind, label).joinToString(SEP), label)

/** Text with pills and chips in it as their own pieces, each sized by its label at the text's size: as wide as its
 * label and its margins, as tall as the label's line and a pixel more each way (the desktop's patch), centred on its
 * line. Their sizes are given in em of the text's size, so they keep to the text however the phone scales its sizes. */
@Composable
fun PillText(text: AnnotatedString, style: TextStyle, color: Color, modifier: Modifier = Modifier, maxLines: Int = Int.MAX_VALUE,
             overflow: TextOverflow = TextOverflow.Clip, onTextLayout: (TextLayoutResult) -> Unit = {}) {
    val ids = text.getStringAnnotations(INLINE_TAG, 0, text.length).map { it.item }.filter { it.startsWith("pill$SEP") || it.startsWith("chip$SEP") }.distinct()
    if (ids.isEmpty()) return Text(text, style = style, color = color, modifier = modifier, maxLines = maxLines, overflow = overflow,
        inlineContent = RefIcons.inline, onTextLayout = onTextLayout)
    val measure = rememberTextMeasurer()
    val density = LocalDensity.current
    val size = if (style.fontSize.isSpecified) style.fontSize else 15.sp
    val sizePx = with(density) { size.toPx() }
    val map = HashMap<String, InlineTextContent>(RefIcons.inline)
    for (id in ids) {
        val parts = id.split(SEP)
        val pill = parts[0] == "pill"
        val label = if (pill) parts[4] else parts[2]
        val scale = if (pill) parts[2].toFloat() else Tokens.Git.chipScale.toFloat()
        val labelStyle = labelStyle(style, size * scale, bold = !pill && parts[1] == "head")
        val labelLayout = measure.measure(label, labelStyle)
        val labelSize = labelLayout.size
        val logoPx = with(density) { (size * 1.17f * 0.78f * scale).toPx() }
        val width = with(density) {
            if (pill) Tokens.Git.pillIn.toPx() + logoPx + Tokens.Git.pillGap.toPx() + labelSize.width + Tokens.Git.pillOut.toPx()
            else Tokens.Git.chipPad.toPx() * 2 + labelSize.width
        }
        val height = maxOf(labelSize.height.toFloat(), if (pill) logoPx else 0f) + with(density) { 2.dp.toPx() }
        // (its place is its patch's part above its label's baseline, sat on the text's baseline, so its label shares
        // the line's baseline as the desktop's chips do and the line's no taller for it; the rest's drawn below)
        val above = (height - labelSize.height) / 2f + labelLayout.firstBaseline
        val tall = height.roundToInt()
        map[id] = InlineTextContent(Placeholder((width / sizePx).em, (above / sizePx).em, PlaceholderVerticalAlign.AboveBaseline)) {
            Box(Modifier.layout { m, c ->
                val p = m.measure(Constraints.fixed(c.maxWidth, tall))
                layout(c.maxWidth, c.maxHeight) { p.place(0, 0) }
            }, propagateMinConstraints = true) {
                if (pill) Pill(parts[1], parts[3], label, labelStyle, with(density) { logoPx.toDp() }) else Chip(parts[1], label, labelStyle)
            }
        }
    }
    if (overflow != TextOverflow.Ellipsis || maxLines == Int.MAX_VALUE)
        return Text(text, style = style, color = color, modifier = modifier, maxLines = maxLines, overflow = overflow,
            inlineContent = map, onTextLayout = onTextLayout)
    // (cut short here, not by the text's own …, which drops the pills on the line it cuts and keeps their room empty)
    val merged = LocalTextStyle.current.merge(style)
    val places = text.getStringAnnotations(INLINE_TAG, 0, text.length)
        .mapNotNull { a -> map[a.item]?.let { AnnotatedString.Range(it.placeholder, a.start, a.end) } }
    BoxWithConstraints(modifier) {
        val shown = if (!constraints.hasBoundedWidth) text else remember(text, constraints.maxWidth, merged, density) {
            ellipsized(text, places, maxLines) { t, p ->
                measure.measure(t, merged, TextOverflow.Clip, maxLines = maxLines, placeholders = p,
                    constraints = Constraints(maxWidth = constraints.maxWidth)).hasVisualOverflow
            }
        }
        Text(shown, style = style, color = color, maxLines = maxLines, overflow = TextOverflow.Clip, inlineContent = map,
            onTextLayout = onTextLayout)
    }
}

/** text as long as fits in maxLines (overflows: whether a text, with its placeholders, doesn't): all of it, or as much
 * as fits with a … after it, never cut inside a pill. */
private fun ellipsized(text: AnnotatedString, places: List<AnnotatedString.Range<Placeholder>>, maxLines: Int,
                       overflows: (AnnotatedString, List<AnnotatedString.Range<Placeholder>>) -> Boolean): AnnotatedString {
    if (!overflows(text, places)) return text
    var end = text.length
    while (end > 0) {
        places.firstOrNull { it.start < end && end < it.end }?.let { end = it.start }
        while (end > 0 && text[end - 1].isWhitespace()) end--
        val cut = buildAnnotatedString { append(text.subSequence(0, end)); append("…") }
        val kept = places.filter { it.end <= end }
        if (!overflows(cut, kept)) return cut
        end--
    }
    return AnnotatedString("…")
}

/** A pill's or chip's label's text: the line's, at the label's size, its line as tall as the font's own (drawn with
 * BasicText, which doesn't take the theme's body line height in). */
private fun labelStyle(style: TextStyle, size: TextUnit, bold: Boolean) =
    style.copy(fontSize = size, lineHeight = TextUnit.Unspecified, fontWeight = if (bold) FontWeight.Bold else FontWeight.Normal)

private val TextUnit.isSpecified get() = this != TextUnit.Unspecified

@Composable
private fun Pill(state: String, href: String, label: String, style: TextStyle, logo: Dp) {
    val c = LocalBaude.current
    val uri = LocalUriHandler.current
    val colour = prColor(state.takeIf { it != "unknown" }, c)
    val faint = state == "closed" || state == "unknown"
    Row(Modifier.clip(RoundedCornerShape(Tokens.Git.chipRadius))
        .background(colour.copy(alpha = (if (faint) Tokens.Git.pillPatchFaint else Tokens.Git.pillPatch).toFloat()))
        .clickable { try { uri.openUri(href) } catch (_: Exception) {} }
        .padding(start = Tokens.Git.pillIn, end = Tokens.Git.pillOut),
        horizontalArrangement = androidx.compose.foundation.layout.Arrangement.spacedBy(Tokens.Git.pillGap),
        verticalAlignment = Alignment.CenterVertically) {
        RefIcons.Draw("merge", colour, logo)
        BasicText(label, style = style.copy(color = colour, textDecoration = if (state == "closed") TextDecoration.LineThrough else null),
            maxLines = 1)
    }
}

@Composable
private fun Chip(kind: String, label: String, style: TextStyle) {
    val c = LocalBaude.current
    val (text, patch) = when (kind) {
        "remote" -> c.fg.copy(alpha = c.fg.alpha * Tokens.Git.remoteText.toFloat()) to c.fg.copy(alpha = Tokens.Git.remotePatch.toFloat())
        "tag" -> Tokens.Git.tag to Tokens.Git.tag.copy(alpha = Tokens.Git.tagPatch.toFloat())
        "chat" -> Tokens.Git.chat to Tokens.Git.chat.copy(alpha = Tokens.Git.chatPatch.toFloat())
        else -> c.accent to c.accentBg.copy(alpha = Tokens.Git.chipPatch.toFloat())
    }
    Row(Modifier.clip(RoundedCornerShape(Tokens.Git.chipRadius)).background(patch).padding(horizontal = Tokens.Git.chipPad),
        verticalAlignment = Alignment.CenterVertically) {
        BasicText(label, style = style.copy(color = text), maxLines = 1)
    }
}

// --------------------------------------------------------------------------------------------- pull states --

/** A repository's pull requests' states as the chat has learned them (PULLS on the desktop): asked of the host once
 * for each repository in two minutes, kept; version changes as one comes, what's drawn from them drawn again. */
object PullStates {
    val version = mutableIntStateOf(0)

    class Known(val repo: String?, val prs: Map<Int, Msg.PullInfo>?, val gh: Boolean, val at: Long)

    private const val PULLS_FRESH_S = 120L
    private val main = Handler(Looper.getMainLooper())
    private val known = HashMap<String, Known>()
    private val asked = HashMap<String, Long>()

    private fun key(hostId: String, repo: String) = hostId + "\u0000" + repo
    private fun cwdKey(place: ChatPlace) = place.hostId + "\u0000cwd:" + place.cwd

    /** A pulls answer's pull requests, by number (null when it has none to give). */
    fun parse(prs: Any?): Map<Int, Msg.PullInfo>? = (prs as? JSONArray)?.let { a ->
        (0 until a.length()).mapNotNull { a.optJSONObject(it)?.let(Msg.PullInfo::from) }.filter { it.number != null }
            .associateBy { it.number!!.toInt() }
    }

    /** Keeps what the Git page read: repo's pull requests, as signed in or not. */
    fun put(hostId: String, cwd: String?, repo: String?, prs: Map<Int, Msg.PullInfo>?, gh: Boolean) {
        val k = Known(repo, prs, gh, SystemClock.elapsedRealtime())
        if (repo != null) known[key(hostId, repo)] = k
        if (cwd != null) known[hostId + "\u0000cwd:" + cwd] = k
        version.intValue++
    }

    /** Everything forgotten (the rig starts each scene afresh). */
    fun reset() {
        known.clear()
        asked.clear()
        version.intValue++
    }

    fun of(hostId: String, repo: String?): Known? = repo?.let { known[key(hostId, it)] }

    private fun ask(place: ChatPlace, repo: String?) {
        if (!takes(place.hostId, "git") || place.cwd == null) return
        val k = if (repo != null) key(place.hostId, repo) else cwdKey(place)
        val now = SystemClock.elapsedRealtime()
        if (now - (asked[k] ?: -PULLS_FRESH_S * 1000) < PULLS_FRESH_S * 1000) return
        asked[k] = now
        main.post {
            Mesh.ask(place.hostId, Msg.Git(what = "pulls", cwd = place.cwd, repo = repo).json()) { r ->
                val g = Msg.GitReply.from(r)
                if (r.optString("op") == Op.GIT && g.error.isNullOrBlank()) put(place.hostId, if (repo == null) place.cwd else null, g.repo, parse(g.prs), g.gh == true)
            }
        }
    }

    /** The chat's own repository's, if known (else asked for). */
    private fun chatKnown(place: ChatPlace): Known? {
        version.intValue
        val k = known[cwdKey(place)]
        if (k == null || SystemClock.elapsedRealtime() - k.at > PULLS_FRESH_S * 1000) ask(place, null)
        return k
    }

    /** A pull request's link as a pill: its state (null when not known) and true; null while nothing's known of the
     * repository, or while gh isn't there to say: the link stays a link. */
    fun link(place: ChatPlace, repo: String, n: Int): Pair<String?, Boolean>? {
        version.intValue
        val k = known[key(place.hostId, repo)]
        if (k == null || SystemClock.elapsedRealtime() - k.at > PULLS_FRESH_S * 1000) ask(place, repo)
        if (k == null || (k.prs == null && !k.gh)) return null
        return k.prs?.get(n)?.state to true
    }

    /** A bare #12's pull request, when the chat's repository has it. */
    fun bare(place: ChatPlace, n: Int): Msg.PullInfo? = chatKnown(place)?.prs?.get(n)
}
