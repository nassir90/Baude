package dev.baude.android

import android.graphics.BitmapFactory
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import java.net.HttpURLConnection
import java.net.URL

const val GIT_LOG_LIMIT = 50
const val GIT_MERGE_LIMIT = 200

// ----------------------------------------------------------------------------------------------------- lanes --

/** One log row's graph: its commit's column and lane, and the lanes into it (from above), through it and out of it
 * (below), each as (column, lane id). */
class LaneRow(val col: Int, val id: Int, val into: List<Pair<Int, Int>>, val through: List<Pair<Int, Int>>,
              val out: List<Pair<Int, Int>>)

/** gitview.git_lanes: each commit's column, newest first, and the lines that pass it. */
fun gitLanes(commits: List<Msg.Commit>): List<LaneRow> {
    val lanes = ArrayList<String?>()
    val ids = ArrayList<Int?>()
    var next = 0
    fun free(): Int {
        val i = lanes.indexOfFirst { it == null }
        if (i >= 0) return i
        lanes.add(null)
        ids.add(null)
        return lanes.size - 1
    }
    val rows = ArrayList<LaneRow>()
    for (c in commits) {
        val sha = c.sha.orEmpty()
        val hits = lanes.indices.filter { lanes[it] == sha }
        val col = if (hits.isNotEmpty()) hits[0] else free().also { ids[it] = next++ }
        val into = hits.map { it to ids[it]!! }
        val through = lanes.indices.filter { lanes[it] != null && lanes[it] != sha }.map { it to ids[it]!! }
        val mine = ids[col]!!
        for (i in hits.drop(1)) {
            lanes[i] = null
            ids[i] = null
        }
        val out = ArrayList<Pair<Int, Int>>()
        val parents = c.parents.orEmpty()
        if (parents.isNotEmpty()) {
            lanes[col] = parents[0]
            out.add(col to mine)
            for (p in parents.drop(1)) {
                var j = lanes.indexOf(p)
                if (j < 0) {
                    j = free()
                    lanes[j] = p
                    ids[j] = next++
                }
                out.add(j to ids[j]!!)
            }
        } else {
            lanes[col] = null
            ids[col] = null
        }
        while (lanes.isNotEmpty() && lanes.last() == null) {
            lanes.removeAt(lanes.size - 1)
            ids.removeAt(ids.size - 1)
        }
        rows.add(LaneRow(col, mine, into, through, out))
    }
    return rows
}

/** The columns the rows use, at least one. */
fun laneColumns(rows: List<LaneRow>): Int = maxOf(1, rows.maxOfOrNull { r ->
    maxOf(r.col, (r.into + r.through + r.out).maxOfOrNull { it.first } ?: 0) + 1
} ?: 1)

fun laneWidth(cols: Int): Dp = Tokens.Git.lanePad * 2 + Tokens.Git.laneStep * (minOf(cols, Tokens.Git.laneMax) - 1)

private val LANE_COLOURS get() = listOf(Tokens.Git.lane1, Tokens.Git.lane2, Tokens.Git.lane3, Tokens.Git.lane4, Tokens.Git.lane5,
    Tokens.Git.lane6, Tokens.Git.lane7, Tokens.Git.lane8)

/** A row's graph, drawn into the row's own height from x (its lanes' lines, and its commit's dot). */
private fun DrawScope.lanes(row: LaneRow, x0: Float, dim: Boolean, made: Boolean) {
    val step = Tokens.Git.laneStep.toPx()
    val pad = Tokens.Git.lanePad.toPx()
    fun x(col: Int) = x0 + pad + minOf(col, Tokens.Git.laneMax) * step
    val mid = size.height / 2
    val alpha = if (dim) Tokens.Git.laneDim.toFloat() else 1f
    val stroke = Stroke(Tokens.Git.laneStroke.toPx())
    fun colour(id: Int) = LANE_COLOURS[id % 8].copy(alpha = alpha)
    fun line(x1: Float, y1: Float, x2: Float, y2: Float, color: Color) {
        val p = Path().apply {
            moveTo(x1, y1)
            if (x1 == x2) lineTo(x2, y2) else cubicTo(x1, (y1 + y2) / 2, x2, (y1 + y2) / 2, x2, y2)
        }
        drawPath(p, color, style = stroke)
    }
    val xc = x(row.col)
    for ((col, id) in row.through) line(x(col), 0f, x(col), size.height, colour(id))
    for ((col, id) in row.into) line(x(col), 0f, xc, mid, colour(id))
    for ((col, id) in row.out) line(xc, mid, x(col), size.height, colour(id))
    drawCircle(if (made) Tokens.Git.chat.copy(alpha = alpha) else colour(row.id), Tokens.Git.laneDot.toPx(), Offset(xc, mid))
}

// ----------------------------------------------------------------------------------------------- ref chips --

/** git_ref_chip: a ref as log names it, as its chip's name and kind; a long name is cut short in the middle. */
fun chipOf(ref: String): Pair<String, String> {
    var name = ref.removePrefix("tag: ").replace("HEAD -> ", "")
    val kind = when {
        ref.startsWith("tag: ") -> "tag"
        ref.startsWith("HEAD") -> "head"
        "/" in ref && ref.substringBefore('/') in listOf("origin", "upstream") -> "remote"
        else -> "local"
    }
    val max = Tokens.Git.chipChars
    if (name.length > max) name = name.take(max / 2 - 1) + "…" + name.takeLast(max - max / 2)
    return name to kind
}

/** git_pull_heads: the pull request each branch is from, the latest of a branch's. */
fun pullHeads(prs: Map<Int, Msg.PullInfo>?): Map<String, Msg.PullInfo> {
    val heads = HashMap<String, Msg.PullInfo>()
    for (p in prs?.values.orEmpty().sortedBy { it.number!!.toInt() }) p.head?.let { heads[it] = p }
    return heads
}

/** The pull request from the branch a log row's ref names (its local name, or a remote's copy's), or null. */
fun pullAt(heads: Map<String, Msg.PullInfo>, ref: String): Msg.PullInfo? {
    var name = ref.replace("HEAD -> ", "")
    if (heads.isEmpty() || ref.startsWith("tag: ") || name == "HEAD") return null
    if ("/" in name && name.substringBefore('/') in listOf("origin", "upstream")) name = name.substringAfter('/')
    return heads[name]
}

/** A log row's line: its refs' chips, a pull request's pill before its branch's, the chat's chip if it made the
 * commit, then the title. */
fun lineText(c: Msg.Commit, made: Boolean, heads: Map<String, Msg.PullInfo>): AnnotatedString = buildAnnotatedString {
    val done = HashSet<Int>()
    for (ref in c.refs.orEmpty()) {
        pullAt(heads, ref)?.let { p ->
            if (done.add(p.number!!.toInt())) {
                pill(p.number.toString(), p.state, p.url.orEmpty(), Tokens.Git.chipScale.toFloat())
                append(" ")
            }
        }
        val (name, kind) = chipOf(ref)
        chip(name, kind)
        append(" ")
    }
    if (made) {
        chip(Wording.Git.thisChat, "chat")
        append(" ")
    }
    append(c.title?.takeIf { it.isNotEmpty() } ?: c.subject.orEmpty())
}

/** The green and red of a diff's counts, the light theme's in both. */
val GIT_ADDED get() = Tokens.light.diff.added
val GIT_REMOVED get() = Tokens.light.diff.removed

fun caption(c: Msg.Commit): AnnotatedString = buildAnnotatedString {
    append(ago(c.at?.toLong() ?: 0L) + " · " + c.author.orEmpty())
    if ((c.files?.toInt() ?: 0) > 0) {
        append(" · ")
        withStyle(SpanStyle(color = GIT_ADDED)) { append(Wording.Git.added(n = c.added ?: 0)) }
        append(" ")
        withStyle(SpanStyle(color = GIT_REMOVED)) { append(Wording.Git.removed(n = c.removed ?: 0)) }
        append(" " + Wording.Git.inFiles(n = c.files!!))
    }
}

// ----------------------------------------------------------------------------------------------- the rows --

/** One log row: its graph's lanes, the author's picture, the line and the caption, and a merge's ▸. */
@Composable
fun LogRow(c: Msg.Commit, lane: LaneRow, cols: Int, dim: Boolean, own: Boolean, made: Boolean, found: Boolean,
           heads: Map<String, Msg.PullInfo>, merge: Boolean?, minute: Int, opened: Boolean, open: () -> Unit, toggle: () -> Unit) {
    val colors = LocalBaude.current
    GitAvatars.version.intValue
    val density = LocalDensity.current
    Row(Modifier.fillMaxWidth().drawBehind {
        if (found) {
            drawRect(colors.accentBg.copy(alpha = 0.2f))
            drawRect(colors.accent, size = androidx.compose.ui.geometry.Size(3.dp.toPx(), size.height))
        }
        lanes(lane, 2.dp.toPx(), dim, made)
    }.clickable(onClick = open).padding(start = 2.dp, end = 8.dp), horizontalArrangement = Arrangement.spacedBy(Tokens.Git.rowGap),
        verticalAlignment = Alignment.CenterVertically) {
        Spacer(Modifier.width(laneWidth(cols)))
        Box(Modifier.alpha(if (dim) Tokens.Git.rowDim.toFloat() else 1f)) {
            Avatar(c.author.orEmpty(), GitAvatars.picture(c.email.orEmpty()), Tokens.Git.avatar)
        }
        Column(Modifier.weight(1f).padding(vertical = 4.dp).alpha(if (dim) Tokens.Git.rowDim.toFloat() else 1f)) {
            PillText(lineText(c, made, heads), style = Type.body.copy(fontWeight = if (own) Tokens.Git.ownWeight else null),
                color = colors.fg, maxLines = 2, overflow = TextOverflow.Ellipsis)
            Text(caption(c), style = Type.caption, color = colors.fg.copy(alpha = colors.fg.alpha * Tokens.Git.captionOpacity.toFloat()),
                maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        if (merge == true) IconButton(if (opened) Adwaita.PanDown else Adwaita.PanEnd,
            if (opened) Wording.Git.hideMerged else Wording.Git.showMerged, onClick = toggle)
    }
}

// ---------------------------------------------------------------------------------------- pull requests --

/** git_touched_pulls: the pull requests the chat touches: those it opened and those from the branches it worked on
 * (newest first), then those its replies named, last named first. */
fun touchedPulls(prs: Map<Int, Msg.PullInfo>, repo: String?, heads: List<String>, opened: List<Int>,
                 links: List<Msg.PullLink>): List<Msg.PullInfo> {
    val own = (opened.filter { it in prs } + prs.values.filter { it.head != null && it.head in heads }.map { it.number!!.toInt() })
        .distinct().sortedDescending().toMutableList()
    for (l in links.reversed()) {
        val n = l.number?.toInt() ?: continue
        if ((l.repo == null || l.repo.equals(repo, true)) && n in prs && n !in own) own.add(n)
    }
    return own.map { prs.getValue(it) }
}

/** A pull request's pill standing alone (.git-pr). */
@Composable
fun PullPill(p: Msg.PullInfo) {
    PillText(buildAnnotatedString { pill(p.number.toString(), p.state, p.url.orEmpty(), Tokens.Git.chipScale.toFloat()) },
        Type.body, LocalBaude.current.fg)
}

/** The pull requests list: a row each (its pill at the log's text's start, its title), the first rows shown, the
 * rest scrolled to. */
@Composable
fun PullsList(rows: List<Msg.PullInfo>, cols: Int, resetKey: Any) {
    val c = LocalBaude.current
    val uri = LocalUriHandler.current
    val list = rememberLazyListState()
    val heights = remember { mutableStateMapOf<Int, Int>() }
    LaunchedEffect(resetKey) { list.scrollToItem(0) }
    val shown = Tokens.Git.pullsShown
    val px = (0 until minOf(rows.size, shown)).sumOf { heights[it] ?: 0 }
    val limit = if (rows.size > shown) (0 until shown - 1).sumOf { heights[it] ?: 0 } + (heights[shown - 1] ?: 0) / 2 else px
    val height = with(LocalDensity.current) { limit.toDp() }
    LazyColumn(Modifier.fillMaxWidth().then(if (limit > 0) Modifier.height(height) else Modifier), state = list) {
        items(rows.size) { i ->
            val p = rows[i]
            Row(Modifier.fillMaxWidth().onSizeChanged { heights[i] = it.height }
                .clickable { try { uri.openUri(p.url.orEmpty()) } catch (_: Exception) {} }
                .padding(top = 2.dp, bottom = 2.dp, start = 2.dp, end = 8.dp), horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalAlignment = Alignment.Top) {
                Box(Modifier.width(laneWidth(cols) + Tokens.Git.rowGap + Tokens.Git.avatar), contentAlignment = Alignment.TopEnd) {
                    PullPill(p)
                }
                Text(p.title.orEmpty(), style = Type.body, color = c.fg, maxLines = 2, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f))
            }
        }
    }
}

// --------------------------------------------------------------------------------------------- pictures --

/** Authors' pictures: from a noreply address's own, or the one GitHub gave for the email; fetched from GitHub's own
 * hosts only, once for each, kept as long as the app runs. */
object GitAvatars {
    var off = false
    val version = mutableIntStateOf(0)
    private const val AVATAR_ASK_MOST = 100
    private val pictures = HashMap<String, ImageBitmap>()
    private val asked = HashSet<String>()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val NOREPLY = Regex("(?:\\d+\\+)?([A-Za-z0-9-]+)@users\\.noreply\\.github\\.com")
    private val HOST = Regex("github\\.com|avatars\\d*\\.githubusercontent\\.com")

    fun picture(email: String): ImageBitmap? = pictures[email.lowercase()]

    /** avatar_url_of: a noreply address's own picture. */
    private fun urlOf(email: String): String? {
        val m = NOREPLY.matchEntire(email.lowercase()) ?: return null
        val id = email.substringBefore('@').substringBefore('+', "")
        return if (id.isNotEmpty() && id.all { it.isDigit() } && '+' in email) "https://avatars.githubusercontent.com/u/$id?s=40"
        else "https://github.com/${m.groupValues[1]}.png?size=40"
    }

    /** avatar_url_ok: https, GitHub's hosts, no other port, no credentials. */
    private fun ok(url: String): Boolean = try {
        val u = URL(url)
        u.protocol == "https" && HOST.matches(u.host) && (u.port == -1 || u.port == 443) && '@' !in u.authority && ' ' !in url
    } catch (_: Exception) {
        false
    }

    fun fetch(emails: List<String>, given: Map<String, String>) {
        if (off) return
        var count = 0
        for (e in emails.map { it.lowercase() }.distinct()) {
            if (e.isEmpty() || e in pictures || count >= AVATAR_ASK_MOST) continue
            val url = urlOf(e) ?: given[e] ?: continue
            if (!ok(url) || !asked.add(e)) continue
            count++
            scope.launch {
                val bytes = try {
                    val conn = URL(url).openConnection() as HttpURLConnection
                    conn.connectTimeout = 15_000
                    conn.readTimeout = 15_000
                    conn.inputStream.use { it.readUpTo(1_000_000) }
                } catch (_: Exception) {
                    return@launch
                }
                val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
                if (bounds.outWidth <= 0 || bounds.outWidth > 1024 || bounds.outHeight > 1024) return@launch
                val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: return@launch
                Mesh.post {
                    pictures[e] = bmp.asImageBitmap()
                    version.intValue++
                }
            }
        }
    }
}
