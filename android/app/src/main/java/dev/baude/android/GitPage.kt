package dev.baude.android

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.tween
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.math.ceil

const val GIT_KEPT_CHATS = 50
private const val STALE_DELAY_MS = 800L

// ---------------------------------------------------------------------------------------------------- state --

/** One chat's Git page as it stands: which branches it shows, what it read, what's opened. */
class GitState(val hostId: String, val sid: String) {
    var follow by mutableStateOf(true)
    var revs by mutableStateOf<List<String>?>(null)
    var pinned by mutableStateOf<String?>(null) // (another folder's history, from a link: GitPage.pinned)
    var every by mutableStateOf(false)
    var page by mutableStateOf<Msg.GitReply?>(null)
    val commits = mutableStateListOf<Msg.Commit>()
    val opened = mutableStateMapOf<String, List<Msg.Commit>?>()
    var openedFor: Pair<List<String>, Boolean>? = null
    var reading by mutableStateOf(false)
    var turning by mutableStateOf(false)
    var more by mutableStateOf(false)
    var card by mutableStateOf<String?>(null)
    var highlight by mutableStateOf<String?>(null)
    var listOpen by mutableStateOf(false)
    var pr by mutableStateOf<Msg.PullInfo?>(null)
    var signedIn by mutableStateOf(false)
    var avatars by mutableStateOf<Map<String, String>>(emptyMap())
    val list = LazyListState()
    var seen = -1
    var token = 0
    var then: String? = null
    var jump = mutableIntStateOf(0)

    /** Reads the page (the branches shown, the log to as far as it's been shown), then its pull requests. */
    fun load(cwd: String, lines: Int, fetch: Boolean = false) {
        val mine = ++token
        reading = true
        turning = fetch
        val limit = maxOf(1, ceil(commits.size / GIT_LOG_LIMIT.toDouble()).toInt()) * GIT_LOG_LIMIT
        Mesh.ask(hostId, Msg.Git(what = "page", cwd = cwd, session = sid, follow = follow, revs = if (!follow) revs else null,
            every = every, fetch = fetch, then = then, limit = limit).json()) { r ->
            if (mine != token) return@ask
            reading = false
            turning = false
            val g = Msg.GitReply.from(r).let { if (r.optString("op") == Op.ERROR) Msg.GitReply(error = said(r, null)) else it }
            then.also { then = null }.let { target ->
                page = g
                commits.clear()
                if (!g.error.isNullOrBlank()) {
                    more = false
                    return@let
                }
                commits.addAll(g.commits.orEmpty())
                more = g.more == true
                val shown = g.picked.orEmpty() to (g.every == true)
                if (shown != openedFor) opened.clear()
                openedFor = shown
                seen = lines
                revs = g.picked
                if (g.fetched == false) toast(Wording.Git.fetchFailed)
                val found = g.found
                if (found != null) {
                    found.merge?.let { m -> if (opened[m] == null) commits.firstOrNull { it.sha == m }?.let { openMerge(cwd, it) } }
                    highlight = found.sha
                    jump.intValue++
                } else if (target != null) card = target
                if (g.repo != null) {
                    val known = PullStates.of(hostId, g.repo)
                    val prs = PullStates.parse(g.prs)
                    if (prs != null || known == null) PullStates.put(hostId, cwd, g.repo, prs, prs != null || known?.gh == true)
                    askPulls(cwd, g, fetch, mine)
                }
            }
        }
    }

    private fun askPulls(cwd: String, g: Msg.GitReply, fresh: Boolean, mine: Int) {
        val head = if (g.picked.orEmpty().size == 1) g.branch?.name?.ifBlank { null } else null
        Mesh.ask(hostId, Msg.Git(what = "pulls", cwd = cwd, repo = g.repo, fresh = fresh, head = head, people = true).json()) { r ->
            val p = Msg.GitReply.from(r)
            if (r.optString("op") != Op.GIT || !p.error.isNullOrBlank()) return@ask
            PullStates.put(hostId, cwd, p.repo, PullStates.parse(p.prs), p.gh == true)
            if (mine != token) return@ask
            pr = p.pr
            signedIn = p.signedIn == true
            avatars = p.avatars.orEmpty()
        }
    }

    /** More of the log. */
    fun loadMore(cwd: String) {
        val p = page ?: return
        Mesh.ask(hostId, Msg.Git(what = "log", cwd = cwd, revs = p.revs.orEmpty().ifEmpty { null }, every = p.every,
            firstParent = p.every != true, skip = commits.size, limit = GIT_LOG_LIMIT).json()) { r ->
            val g = Msg.GitReply.from(r)
            if (r.optString("op") != Op.GIT || !g.error.isNullOrBlank()) return@ask
            commits.addAll(g.commits.orEmpty())
            more = g.commits.orEmpty().size >= GIT_LOG_LIMIT
        }
    }

    /** A merge's merged commits, read and shown under it. */
    fun openMerge(cwd: String, c: Msg.Commit) {
        val parents = c.parents.orEmpty()
        if (parents.size < 2) return
        opened[c.sha.orEmpty()] = null
        Mesh.ask(hostId, Msg.Git(what = "log", cwd = cwd, revs = listOf(parents[1], "^" + parents[0]), limit = GIT_MERGE_LIMIT).json()) { r ->
            val g = Msg.GitReply.from(r)
            opened[c.sha.orEmpty()] = if (r.optString("op") == Op.GIT && g.error.isNullOrBlank()) g.commits.orEmpty() else emptyList()
        }
    }
}

/** Each chat's page, kept (the least recently used dropped past GIT_KEPT_CHATS). */
object GitPages {
    private val kept = object : LinkedHashMap<String, GitState>(16, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, GitState>?) = size > GIT_KEPT_CHATS
    }
    var showPage: () -> Unit = {}

    fun of(hostId: String, sid: String): GitState = kept.getOrPut("$hostId\u0000$sid") { GitState(hostId, sid) }

    /** The rig's: the branches picked (none: the chat's own), every branch or not. */
    fun prepare(hostId: String, sid: String, pick: List<String>?, every: Boolean) {
        kept.remove("$hostId\u0000$sid")
        GitCards.clear()
        PullStates.reset()
        val s = of(hostId, sid)
        s.follow = pick == null
        s.revs = pick
        s.every = every
    }

    /** The page at a branch or commit named elsewhere (a link in the chat): that branch alone, the commit's card open
     * once it's read; folder: another repository's or worktree's (a commit the chat made there). */
    fun focus(hostId: String, sid: String, rev: String?, sha: String?, folder: String? = null) {
        val s = of(hostId, sid)
        s.follow = false
        s.pinned = folder
        s.revs = rev?.let(::listOf)
        s.then = sha
        s.card = null
        s.seen = -1
        showPage()
    }
}

// --------------------------------------------------------------------------------------------------- words --

private fun bold(x: String) = "\u0001$x\u0002"
private fun mono(x: String) = "\u0003$x\u0004"
private fun linked(x: String) = "\u0005$x\u0006"

/** Text whose bold, monospace and link parts are marked by control characters, as styled text. */
fun marked(text: String, url: String? = null, link: androidx.compose.ui.graphics.Color = androidx.compose.ui.graphics.Color.Unspecified): AnnotatedString =
    buildAnnotatedString {
        var i = 0
        while (i < text.length) {
            val open = text[i]
            val close = when (open) { '\u0001' -> '\u0002'; '\u0003' -> '\u0004'; '\u0005' -> '\u0006'; else -> null }
            if (close == null) {
                append(open)
                i++
                continue
            }
            val end = text.indexOf(close, i + 1).takeIf { it > 0 } ?: text.length
            val part = text.substring(i + 1, end)
            when (open) {
                '\u0001' -> withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { append(part) }
                '\u0003' -> withStyle(SpanStyle(fontFamily = Type.mono.fontFamily)) { append(part) }
                else -> if (url != null) withLink(LinkAnnotation.Url(url, TextLinkStyles(SpanStyle(color = link,
                    textDecoration = TextDecoration.Underline)))) { append(part) } else append(part)
            }
            i = end + 1
        }
    }

/** git_ahead_behind: how many commits ahead and behind, "" when neither. */
fun aheadBehind(ahead: Number?, behind: Number?): String = listOfNotNull(
    ahead?.toInt()?.takeIf { it > 0 }?.let { Wording.Git.ahead(n = it) },
    behind?.toInt()?.takeIf { it > 0 }?.let { Wording.Git.behind(n = it) }).joinToString(", ")

fun stamped(at: Long): String {
    val d = Date(at * 1000)
    val day = SimpleDateFormat("d", Locale.ENGLISH).format(d).toInt()
    return SimpleDateFormat("HH:mm EEE ", Locale.ENGLISH).format(d) + Wording.Time.dayOfMonth(day = day) + " " + SimpleDateFormat("MMMM yyyy", Locale.ENGLISH).format(d)
}

fun ago(at: Long): String {
    val secs = maxOf(0L, System.currentTimeMillis() / 1000 - at)
    for ((n, said) in listOf<Pair<Long, (Long) -> String>>(365 * 86400L to { Wording.Time.yearsAgo(n = it) },
        30 * 86400L to { Wording.Time.monthsAgo(n = it) }, 7 * 86400L to { Wording.Time.weeksAgo(n = it) },
        86400L to { Wording.Time.daysAgo(n = it) }, 3600L to { Wording.Time.hoursAgo(n = it) },
        60L to { Wording.Time.minutesAgo(n = it) }))
        if (secs >= n) return said(secs / n)
    return Wording.Time.justNow
}

private fun Msg.Commit.withParents(parents: List<String>) = Msg.Commit(sha = sha, parents = parents, author = author, email = email,
    at = at, refs = refs, subject = subject, ct = ct, title = title, files = files, added = added, removed = removed)

private class LogShown(val c: Msg.Commit, val drawn: Msg.Commit, val merge: Boolean?)

/** The rows: a merge's second parent's line left out unless its merged commits are read, and then they follow it. */
private fun shownRows(page: Msg.GitReply, commits: List<Msg.Commit>, opened: Map<String, List<Msg.Commit>?>): List<LogShown> {
    val rows = ArrayList<LogShown>()
    for (c in commits) {
        val parents = c.parents.orEmpty()
        val merged = opened[c.sha.orEmpty()]
        when {
            page.every == true || parents.size < 2 -> rows.add(LogShown(c, c, null))
            merged != null -> {
                rows.add(LogShown(c, c, true))
                merged.forEach { rows.add(LogShown(it, it, null)) }
            }
            else -> rows.add(LogShown(c, c.withParents(parents.take(1)), true))
        }
    }
    return rows
}

// ----------------------------------------------------------------------------------------------------- page --

/** The chat folder's git, as Baude's Git page has it: the branches shown, what they track, their pull requests and
 * their history as a graph; a commit opens into its card. here: it's the page shown. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun GitPage(hostId: String, sid: String, here: Boolean, openPage: (String) -> Unit) {
    val c = LocalBaude.current
    val state = remember(hostId, sid) { GitPages.of(hostId, sid) }
    val cwd = state.pinned ?: Mesh.session(hostId, sid)?.cwd?.ifBlank { null }
    val chat = remember(hostId, sid) { Mesh.openChat(hostId, sid) }
    val lines = chat.lines.size
    LaunchedEffect(here, cwd) { if (here && cwd != null && state.seen != lines) state.load(cwd, lines) }
    LaunchedEffect(lines) {
        if (here && cwd != null && state.seen >= 0 && state.seen != lines) {
            delay(STALE_DELAY_MS)
            state.load(cwd, lines)
        }
    }
    LaunchedEffect(GitLinks.want.value) { // (a commit named in the chat: its card)
        val sha = GitLinks.want.value ?: return@LaunchedEffect
        state.pinned = null
        state.card = sha
        GitLinks.want.value = null
    }
    var minute by remember { mutableIntStateOf(0) }
    LaunchedEffect(here) { // (the captions' times, as the minutes pass)
        while (here) {
            delay(60_000)
            minute++
        }
    }
    state.card?.let { sha -> return CommitCard(state, hostId, sid, cwd.orEmpty(), sha, openPage) }
    val page = state.page
    page?.missing?.let { dep -> return MissingDep(dep, Wording.Git.title) { cwd?.let { state.load(it, lines) } } }
    val repo = page != null && page.error.isNullOrBlank() && !page.top.isNullOrBlank()
    PullStates.version.intValue
    val prs = if (repo) PullStates.of(hostId, page!!.repo)?.prs else null
    val heads = remember(prs) { pullHeads(prs) }
    val uri = LocalUriHandler.current

    Column(Modifier.fillMaxSize(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        // top bar
        val turn = remember { Animatable(0f) }
        LaunchedEffect(state.turning) {
            if (state.turning) while (true) turn.animateTo(turn.value + 360f, tween(1000, easing = LinearEasing))
            else {
                val rest = ceil(turn.value / 360f) * 360f - turn.value
                if (rest > 0f) turn.animateTo(turn.value + rest, tween((rest / 360f * 1000).toInt() + 1, easing = LinearEasing))
            }
        }
        Row(Modifier.fillMaxWidth().padding(start = 10.dp, end = 8.dp, top = 8.dp), horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically) {
            Text(if (state.reading) Wording.Git.readingHistory else shortPath(hostId, (if (repo) page!!.top else null) ?: cwd.orEmpty()),
                style = Type.body, color = c.dim, maxLines = 1, overflow = TextOverflow.StartEllipsis, modifier = Modifier.weight(1f))
            IconButton(Adwaita.ViewListBullet, Wording.Git.showEveryBranch,
                background = if (state.every) c.fg.copy(alpha = 0.10f) else null) {
                state.every = !state.every
                cwd?.let { state.load(it, lines) }
            }
            Box(Modifier.size(Gap.barButton).clip(RoundedCornerShape(8.dp)).clickable { cwd?.let { state.load(it, lines, fetch = true) } },
                contentAlignment = Alignment.Center) {
                Icon(Adwaita.ViewRefresh, Wording.Git.fetchTip, tint = c.fg, modifier = Modifier.size(18.dp.icon).rotate(turn.value))
            }
        }
        if (page == null) return@Column
        if (!page.error.isNullOrBlank()) {
            Text(page.error, style = Type.body, color = c.fg, modifier = Modifier.padding(start = 12.dp, end = 10.dp))
            return@Column
        }
        // branch picker
        val branches = page.branches.orEmpty()
        val picked = page.picked.orEmpty()
        val chosen = remember(state.listOpen, picked) { mutableStateListOf<String>().also { it.addAll(picked) } }
        fun pick() {
            state.listOpen = false
            if (chosen.isNotEmpty() && chosen.toList() != picked) {
                state.follow = false
                state.revs = chosen.toList()
                cwd?.let { state.load(it, lines) }
            }
        }
        val names = picked.mapNotNull { r -> branches.firstOrNull { it.rev == r } }
        var pillWidth by remember { mutableStateOf(0.dp) }
        val density = androidx.compose.ui.platform.LocalDensity.current
        Box(Modifier.padding(horizontal = 10.dp)) {
            Row(Modifier.fillMaxWidth().onSizeChanged { pillWidth = with(density) { it.width.toDp() } }.clip(RoundedCornerShape(8.dp)).background(c.fg.copy(alpha = 0.10f))
                .clickable { state.listOpen = true }.heightIn(min = Gap.barButton).padding(horizontal = 10.dp),
                horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                val first = names.firstOrNull()
                Row(Modifier.weight(1f), horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(first?.let { it.name ?: it.label }.orEmpty(), style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.MiddleEllipsis,
                        modifier = Modifier.weight(1f, fill = false))
                    if (picked.size > 1) Text(Wording.Git.moreBranches(n = picked.size - 1), style = Type.body, color = c.dim, maxLines = 1)
                }
                Icon(Adwaita.PanDown, Wording.Git.branchesTip + if (picked.size > 1) "\n" + names.joinToString("\n") { it.name ?: it.label.orEmpty() } else "",
                    tint = c.fg, modifier = Modifier.size(16.dp.icon))
            }
            Popover(state.listOpen, ::pick, ticks = Ticks.END, width = pillWidth) {
                Column(Modifier.heightIn(max = Tokens.Git.branchesHeight).verticalScroll(rememberScrollState())) {
                    val firstUntouched = branches.indexOfFirst { it.touched != true }
                    branches.forEachIndexed { i, b ->
                        if (i == firstUntouched && i > 0) MenuDivider()
                        val on = b.rev in chosen
                        MenuChoice(if (b.rev == page.mine) Wording.Git.thisChatBranch(branch = b.label.orEmpty()) else b.label.orEmpty(), on,
                            onLongClick = { if (on) chosen.remove(b.rev) else b.rev?.let { chosen.add(it) } }) {
                            chosen.clear()
                            b.rev?.let { chosen.add(it) }
                        }
                    }
                }
            }
        }
        // info
        val info = buildInfo(page, state.pr.takeIf { state.signedIn })
        if (info != null) Text(marked(info.first, info.second, c.accent), style = Type.body, color = c.fg,
            modifier = Modifier.padding(start = 12.dp, end = 10.dp))
        // pull requests
        val rows = shownRows(page, state.commits, state.opened)
        val lanes = remember(rows.map { it.drawn.sha to it.drawn.parents }) { gitLanes(rows.map { it.drawn }) }
        val cols = laneColumns(lanes)
        if (prs != null) {
            val touched = touchedPulls(prs, page.repo, page.heads.orEmpty(), page.opened.orEmpty().map { it.toInt() }, page.links.orEmpty())
            if (touched.isNotEmpty()) PullsList(touched, cols, Triple(page.repo, page.top, page.mine))
        }
        // the log
        val emails = rows.map { it.c.email.orEmpty() }
        LaunchedEffect(emails.size, state.avatars) { GitAvatars.fetch(emails, state.avatars) }
        LaunchedEffect(state.jump.intValue, rows.size) {
            val at = state.highlight?.let { h -> rows.indexOfFirst { it.c.sha == h } } ?: -1
            if (at >= 0 && state.jump.intValue > 0) state.list.scrollToItem(at)
        }
        if (rows.isEmpty()) Empty(Wording.Git.noCommits)
        else LazyColumn(Modifier.weight(1f).fillMaxWidth(), state = state.list) {
            items(rows.size) { i ->
                val r = rows[i]
                val sha = r.c.sha.orEmpty()
                LogRow(r.c, lanes[i], cols, dim = sha in page.theirs.orEmpty(), own = sha in page.own.orEmpty(),
                    made = page.made.orEmpty().any { it.sha.orEmpty().isNotEmpty() && sha.startsWith(it.sha.orEmpty()) },
                    found = state.highlight == sha, heads = heads, merge = r.merge, minute = minute,
                    opened = state.opened[sha] != null, open = { state.card = sha }) {
                    if (state.opened[sha] != null) state.opened.remove(sha) else cwd?.let { state.openMerge(it, r.c) }
                }
            }
            if (state.more) item {
                Box(Modifier.fillMaxWidth().padding(top = 6.dp, bottom = 10.dp), contentAlignment = Alignment.Center) {
                    Text(Wording.Git.showMore, style = Type.bold, color = c.fg,
                        modifier = Modifier.clickable { cwd?.let { state.loadMore(it) } }.padding(horizontal = 16.dp, vertical = 9.dp))
                }
            }
        }
    }
}

/** The info line's text (control characters mark bold, monospace and the link) and the link's address. */
private fun buildInfo(page: Msg.GitReply, pr: Msg.PullInfo?): Pair<String, String?>? {
    val lines = ArrayList<String>()
    var url: String? = null
    if (page.every == true) lines.add(Wording.Git.everyBranch)
    page.branch?.takeIf { !it.upstream.isNullOrBlank() }?.let { b ->
        val track = if (b.gone == true) Wording.Git.goneFromRemote
        else aheadBehind(b.ahead, b.behind).ifEmpty { Wording.Git.upToDate }
        lines.add(Wording.Git.tracks(upstream = bold(b.upstream!!), track = track))
    }
    page.parent?.let { p ->
        lines.add(Wording.Git.fromBranch(branch = bold(p.name.orEmpty()), sha = mono(p.base.orEmpty().take(7)),
            track = aheadBehind(p.ahead, p.behind).ifEmpty { Wording.Git.upToDate }))
    }
    if (pr != null) {
        url = pr.url
        lines.add(Wording.Git.pullRequest(link = linked("#${pr.number}"), title = pr.title.orEmpty().take(60), state = pr.state.orEmpty().lowercase()))
    }
    return if (lines.isEmpty()) null else lines.joinToString("\n") to url
}
