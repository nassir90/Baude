package dev.baude.android

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import org.json.JSONArray
import kotlin.math.abs

const val GIT_CARD_FRESH = 120_000L

/** A commit's card as read: kept by (host, folder, sha) for two minutes. */
object GitCards {
    class Card(val card: Msg.GitReply, var github: Msg.GitReply?, val at: Long)

    private val kept = HashMap<String, Card>()
    fun key(hostId: String, top: String, sha: String) = "$hostId\u0000$top\u0000$sha"
    fun get(key: String): Card? = kept[key]?.takeIf { android.os.SystemClock.elapsedRealtime() - it.at < GIT_CARD_FRESH }
    fun put(key: String, c: Card) {
        kept[key] = c
    }
}

private val MERGED_PR = Regex("Merge pull request #(\\d+) from (?:[^\\s/]+/(\\S+)|.+)")
private val TRAILER = Regex("(co-authored|signed-off|reviewed|tested|acked|reported|suggested|helped)-by: .*", RegexOption.IGNORE_CASE)

/** git_title: a merged pull request's own name (the first line of what it says, else its branch), else the subject. */
fun gitTitle(message: String): String {
    val lines = message.lines()
    val subject = lines.firstOrNull().orEmpty()
    val m = MERGED_PR.matchEntire(subject) ?: return subject
    val line = lines.drop(1).map { it.trim() }.firstOrNull { it.isNotEmpty() }?.takeIf { !TRAILER.containsMatchIn(it) && !it.matches(TRAILER) }
    return if (line != null) "#${m.groupValues[1]} $line"
    else if (m.groupValues[2].isNotEmpty()) "#${m.groupValues[1]} ${m.groupValues[2]}" else subject
}

/** A file's bar: its whole width faint, a green share of what went in and a red one of what came out, after it. */
@Composable
private fun DeltaBar(a: Int, r: Int, biggest: Int) {
    val c = LocalBaude.current
    val w = Tokens.Git.deltaWidth
    Row(Modifier.width(w).height(Tokens.Git.deltaHeight).background(c.fg.copy(alpha = Tokens.Git.deltaRest.toFloat()))) {
        Box(Modifier.width(w * (a.toFloat() / biggest)).height(Tokens.Git.deltaHeight).background(GIT_ADDED))
        Box(Modifier.width(w * (r.toFloat() / biggest)).height(Tokens.Git.deltaHeight).background(GIT_REMOVED))
    }
}

/** The card's and the diff's head: back, and the title. */
@Composable
private fun CardHead(back: String, title: String, mono: Boolean, onBack: () -> Unit) {
    val c = LocalBaude.current
    Row(Modifier.fillMaxWidth().padding(start = 8.dp, end = 8.dp, top = 6.dp, bottom = 6.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
        IconButton(Adwaita.GoPrevious, back, onClick = onBack)
        Text(title, style = if (mono) Type.title.copy(fontFamily = Type.mono.fontFamily) else Type.title, color = c.fg, maxLines = 1,
            overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
    }
}

/** A commit's card: what it says and who made it, its files, where it is, and what GitHub knows of it. A file opens
 * its diff in this commit, Open diff all of it. */
@Composable
fun CommitCard(state: GitState, hostId: String, sid: String, cwd: String, sha: String, openPage: (String) -> Unit) {
    val c = LocalBaude.current
    val uri = LocalUriHandler.current
    val top = state.page?.top?.ifBlank { null } ?: cwd
    val key = GitCards.key(hostId, top, sha)
    var card by remember(key) { mutableStateOf(GitCards.get(key)?.card) }
    var github by remember(key) { mutableStateOf(GitCards.get(key)?.github) }
    var failed by remember(key) { mutableStateOf<String?>(null) }
    var diff by remember(key) { mutableStateOf<Pair<String?, Boolean>?>(null) }
    BackHandler { if (diff != null) diff = null else state.card = null }
    LaunchedEffect(key) {
        if (card != null) return@LaunchedEffect
        Mesh.ask(hostId, Msg.Git(what = "card", cwd = cwd, sha = sha).json()) { r ->
            val g = Msg.GitReply.from(r)
            if (r.optString("op") != Op.GIT || !g.error.isNullOrBlank()) {
                failed = said(r, g.error).ifBlank { Wording.HostErrors.gitFailed }
                return@ask
            }
            card = g
            GitCards.put(key, GitCards.Card(g, null, android.os.SystemClock.elapsedRealtime()))
            if (!g.url.isNullOrBlank()) Mesh.ask(hostId, Msg.Git(what = "card", cwd = cwd, sha = sha, github = true).json()) { r2 ->
                val h = Msg.GitReply.from(r2)
                if (r2.optString("op") == Op.GIT && h.error.isNullOrBlank()) {
                    github = h
                    GitCards.get(key)?.github = h
                }
            }
        }
    }
    diff?.let { (path, _) -> return DiffView(hostId, cwd, sha, path) { diff = null } }
    Column(Modifier.fillMaxSize()) {
        CardHead(Wording.Git.theLog, sha.take(9), true) { state.card = null }
        val k = card
        when {
            failed != null -> Empty(failed!!)
            k == null -> Empty(Wording.Git.readingCommit)
            else -> Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(start = 12.dp, end = 12.dp, bottom = 12.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)) {
                val msg = k.message.orEmpty()
                val title = gitTitle(msg)
                val made = state.page?.made.orEmpty().firstOrNull { sha.startsWith(it.sha.orEmpty()) && it.sha.orEmpty().isNotEmpty() }
                val login = github?.login ?: k.login
                SelectionContainer {
                    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(title, style = Type.title, color = c.fg)
                        Column {
                            Text(buildAnnotatedString {
                                if (login != null) link("https://github.com/$login", k.author.orEmpty(), c.accent) else append(k.author.orEmpty())
                                append(" · " + stamped(k.at?.toLong() ?: 0L) + " · ")
                                val s = sha.take(9)
                                withStyle(SpanStyle(fontFamily = Type.mono.fontFamily)) {
                                    if (!k.url.isNullOrBlank()) link(k.url, s, c.accent) else append(s)
                                }
                            }, style = Type.caption, color = c.dim)
                            val ct = k.ct?.toLong() ?: 0L
                            if (k.committer.orEmpty() != k.author.orEmpty() || abs(ct - (k.at?.toLong() ?: 0L)) > 60)
                                Text(Wording.Git.committedBy(committer = k.committer.orEmpty(), `when` = stamped(ct)), style = Type.caption,
                                    color = c.fg.copy(alpha = c.fg.alpha * Tokens.Git.noteOpacity.toFloat()))
                        }
                    }
                }
                if (made != null) Box(Modifier.clip(RoundedCornerShape(8.dp)).background(Tokens.Git.chat.copy(alpha = Tokens.Git.chatPatch.toFloat()))
                    .clickable {
                        val id = made.toolUseId.orEmpty()
                        if (id in Mesh.openChat(hostId, sid).calls.byId) {
                            Mesh.openChat(hostId, sid).jump.value = id
                            openPage("chat")
                        } else toast(Wording.Git.chatFurtherUp)
                    }.padding(horizontal = 16.dp, vertical = 9.dp)) {
                    Text(Wording.Git.madeByChatShow, style = Type.bold, color = Tokens.Git.chat)
                }
                val files = k.files.let { a -> (0 until (a?.length() ?: 0)).mapNotNull { a!!.optJSONArray(it) } }
                val added = files.sumOf { if (it.isNull(1)) 0 else it.optInt(1) }
                val removed = files.sumOf { if (it.isNull(2)) 0 else it.optInt(2) }
                Text(buildAnnotatedString {
                    withStyle(SpanStyle(fontWeight = androidx.compose.ui.text.font.FontWeight.Bold)) { append(Wording.Git.files(n = files.size)) }
                    append("  ")
                    withStyle(SpanStyle(color = GIT_ADDED)) { append(Wording.Git.added(n = added)) }
                    append(" ")
                    withStyle(SpanStyle(color = GIT_REMOVED)) { append(Wording.Git.removed(n = removed)) }
                    if (k.parents.orEmpty().size > 1) withStyle(SpanStyle(color = c.fg.copy(alpha = c.fg.alpha * Tokens.Git.noteOpacity.toFloat()))) {
                        append("  " + Wording.Git.againstParent(sha = k.parents!![0].take(7)))
                    }
                }, style = Type.body, color = c.fg)
                if (files.isNotEmpty()) {
                    val biggest = maxOf(1, files.maxOf { (if (it.isNull(1)) 0 else it.optInt(1)) + (if (it.isNull(2)) 0 else it.optInt(2)) })
                    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.card)
                        .border(1.dp, c.rule, RoundedCornerShape(12.dp))) {
                        files.forEachIndexed { i, f ->
                            if (i > 0) Box(Modifier.fillMaxWidth().height(1.dp).background(c.rule))
                            val a = if (f.isNull(1)) null else f.optInt(1)
                            val r = if (f.isNull(2)) 0 else f.optInt(2)
                            Row(Modifier.fillMaxWidth().clickable { diff = f.optString(0) to false }.padding(vertical = 4.dp, horizontal = 8.dp),
                                horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                                Text(f.optString(0), style = Type.mono, color = c.fg, maxLines = 1, overflow = TextOverflow.StartEllipsis,
                                    modifier = Modifier.weight(1f))
                                Text(if (a == null) AnnotatedString(Wording.Git.binary) else buildAnnotatedString {
                                    withStyle(SpanStyle(color = GIT_ADDED)) { append(Wording.Git.added(n = a)) }
                                    append(" ")
                                    withStyle(SpanStyle(color = GIT_REMOVED)) { append(Wording.Git.removed(n = r)) }
                                }, style = Type.caption, color = c.fg)
                                DeltaBar(a ?: 0, r, biggest)
                            }
                        }
                    }
                }
                val rest = msg.lines().drop(1).joinToString("\n").trim()
                val whole = if (title != msg.lines().first()) msg.trim() else rest
                if (whole.isNotEmpty()) SelectionContainer { Text(whole, style = Type.caption, color = c.fg) }
                val on = k.on.orEmpty() + k.remotes.orEmpty()
                Text(if (on.isEmpty()) Wording.Git.onNoBranch
                    else if (on.size > 8) Wording.Git.onBranchesMore(branches = on.take(8).joinToString(", "), n = on.size - 8)
                    else Wording.Git.onBranches(branches = on.joinToString(", ")),
                    style = Type.caption, color = c.fg.copy(alpha = c.fg.alpha * Tokens.Git.noteOpacity.toFloat()))
                for (p in PullStates.parse(github?.prs).orEmpty().values) Text(buildAnnotatedString {
                    link(p.url.orEmpty(), "#${p.number}", c.accent)
                    append(" ${p.title.orEmpty()} (${p.state.orEmpty()})")
                }, style = Type.body, color = c.fg)
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Button(Wording.Git.openDiff) { diff = null to true }
                    if (!k.url.isNullOrBlank()) Button(Wording.Git.githubLink) { try { uri.openUri(k.url) } catch (_: Exception) {} }
                }
            }
        }
    }
}

private fun AnnotatedString.Builder.link(url: String, text: String, colour: Color) =
    withLink(LinkAnnotation.Url(url, TextLinkStyles(SpanStyle(color = colour, textDecoration = TextDecoration.Underline)))) { append(text) }

/** A file's diff in a commit, or all of the commit's (path null): its lines coloured. */
@Composable
private fun DiffView(hostId: String, cwd: String, sha: String, path: String?, back: () -> Unit) {
    val c = LocalBaude.current
    var text by remember(sha, path) { mutableStateOf<String?>(null) }
    LaunchedEffect(sha, path) {
        Mesh.ask(hostId, Msg.Git(what = "diff", cwd = cwd, sha = sha, path = path).json()) { r ->
            val g = Msg.GitReply.from(r)
            text = said(r, g.error).ifBlank { null } ?: g.text.orEmpty()
        }
    }
    Column(Modifier.fillMaxSize()) {
        CardHead(Wording.Browser.back,
            if (path != null) Wording.Git.pathInCommit(path = path, sha = sha.take(9)) else Wording.Git.commitNote(sha = sha.take(9)), false, back)
        Box(Modifier.fillMaxSize().padding(top = 6.dp).inside(c)) {
            val t = text
            if (t == null) Empty(Wording.Git.readingCommit)
            else { // (a line at a time, as it's scrolled to: a long diff isn't all drawn)
                val lines = remember(t) { t.lines() }
                LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(8.dp)) {
                    items(lines) { l ->
                        Text(l, style = Type.mono, color = when {
                            l.startsWith("+") && !l.startsWith("+++") -> c.added
                            l.startsWith("-") && !l.startsWith("---") -> c.removed
                            l.startsWith("@@") -> c.accent
                            else -> c.fg
                        })
                    }
                }
            }
        }
    }
}
