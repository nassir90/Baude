package dev.baude.android

import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
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
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.foundation.text.appendInlineContent
import androidx.compose.foundation.text.InlineTextContent
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.json.JSONArray

// ------------------------------------------------------------------------------------------------- the logo --

/** The merge logo a pull request's pill has (widgets.REF_ICON_SVG["merge"]): three rings, a line and a curve, 16 square. */
val MergeLogo: ImageVector by lazy {
    ImageVector.Builder("merge", 16.dp, 16.dp, 16f, 16f).apply {
        fun ring(cx: Float, cy: Float) = "M${cx - 1.85f},$cy a1.85,1.85 0 1 0 3.7,0 a1.85,1.85 0 1 0 -3.7,0 z"
        for (d in listOf(ring(4.6f, 3.3f), ring(4.6f, 12.7f), ring(11.6f, 9f), "M4.6 5.2v5.6M4.6 5.2c0 2.3 1.6 3.8 5.1 3.8"))
            addPath(PathParser().parsePathString(d).toNodes(), stroke = SolidColor(Color.Black), strokeLineWidth = 1.35f,
                strokeLineCap = StrokeCap.Round, strokeLineJoin = StrokeJoin.Round)
    }.build()
}

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

/** Text with pills and chips in it as their own pieces, each sized by its label at the text's size. */
@Composable
fun PillText(text: AnnotatedString, style: TextStyle, color: Color, modifier: Modifier = Modifier, maxLines: Int = Int.MAX_VALUE,
             overflow: TextOverflow = TextOverflow.Clip) {
    val ids = text.getStringAnnotations(INLINE_TAG, 0, text.length).map { it.item }.distinct()
    if (ids.isEmpty()) return Text(text, style = style, color = color, modifier = modifier, maxLines = maxLines, overflow = overflow)
    val measure = rememberTextMeasurer()
    val density = LocalDensity.current
    val size = if (style.fontSize.isSpecified) style.fontSize else 15.sp
    val map = HashMap<String, InlineTextContent>()
    for (id in ids) {
        val parts = id.split(SEP)
        val pill = parts[0] == "pill"
        val label = if (pill) parts[4] else parts[2]
        val scale = if (pill) parts[2].toFloat() else Tokens.Git.chipScale.toFloat()
        val labelSize = size * scale
        val box = (size.value * 1.17f * scale + 2f).sp
        val labelPx = measure.measure(label, style.copy(fontSize = labelSize, fontWeight = if (parts[1] == "head") FontWeight.Bold else null)).size.width
        val logo = size * 1.17f * 0.78f * scale
        val width = with(density) {
            if (pill) Tokens.Git.pillIn.toPx() + logo.toPx() + Tokens.Git.pillGap.toPx() + labelPx + Tokens.Git.pillOut.toPx()
            else Tokens.Git.chipPad.toPx() * 2 + labelPx
        }
        map[id] = InlineTextContent(Placeholder(with(density) { width.toSp() }, box, PlaceholderVerticalAlign.TextCenter)) {
            if (pill) Pill(parts[1], parts[3], label, labelSize, logo) else Chip(parts[1], label, labelSize)
        }
    }
    Text(text, style = style, color = color, modifier = modifier, maxLines = maxLines, overflow = overflow, inlineContent = map)
}

private val TextUnit.isSpecified get() = this != TextUnit.Unspecified

@Composable
private fun Pill(state: String, href: String, label: String, size: TextUnit, logo: TextUnit) {
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
        Icon(MergeLogo, null, tint = colour, modifier = Modifier.size(with(LocalDensity.current) { logo.toDp() }))
        Text(label, fontSize = size, color = colour, maxLines = 1,
            textDecoration = if (state == "closed") TextDecoration.LineThrough else null)
    }
}

@Composable
private fun Chip(kind: String, label: String, size: TextUnit) {
    val c = LocalBaude.current
    val (text, patch) = when (kind) {
        "remote" -> c.fg.copy(alpha = c.fg.alpha * Tokens.Git.remoteText.toFloat()) to c.fg.copy(alpha = Tokens.Git.remotePatch.toFloat())
        "tag" -> Tokens.Git.tag to Tokens.Git.tag.copy(alpha = Tokens.Git.tagPatch.toFloat())
        "chat" -> Tokens.Git.chat to Tokens.Git.chat.copy(alpha = Tokens.Git.chatPatch.toFloat())
        else -> c.accent to c.accentBg.copy(alpha = Tokens.Git.chipPatch.toFloat())
    }
    Row(Modifier.clip(RoundedCornerShape(Tokens.Git.chipRadius)).background(patch).padding(horizontal = Tokens.Git.chipPad),
        verticalAlignment = Alignment.CenterVertically) {
        Text(label, fontSize = size, color = text, maxLines = 1, fontWeight = if (kind == "head") FontWeight.Bold else null)
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
                if (r.optString("op") == Op.GIT && g.error.isNullOrBlank()) put(place.hostId, place.cwd, g.repo, parse(g.prs), g.gh == true)
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
