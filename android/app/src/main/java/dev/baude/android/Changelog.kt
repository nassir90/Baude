package dev.baude.android

import android.content.Context
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/** A commit as update.changelog lists it: its short hash, when it was made (epoch seconds) and its subject. */
data class Change(val short: String, val made: Long, val subject: String)

/** The version the app is running (update.version, update.changelog): the commit it was built from and when it was
 * made, and the commits that has (the build's changelog.txt: git log of that commit, merges left out, newest first). */
object AppVersion {
    var short: String? = BuildConfig.SHORT.ifEmpty { null }
    var made: Long? = BuildConfig.MADE.takeIf { it > 0 }
    internal var read: List<Change>? = null

    fun changes(ctx: Context): List<Change> = read ?: try {
        ctx.assets.open("changelog.txt").bufferedReader().readLines().mapNotNull { line ->
            line.split('\u001f').takeIf { it.size == 3 && it[1].isNotEmpty() && it[1].all(Char::isDigit) }
                ?.let { Change(it[0], it[1].toLong(), it[2]) }
        }
    } catch (_: Exception) {
        emptyList()
    }.also { read = it }
}

/** A time as update.made_when says it: 21 November 2026, 09:14 (local). */
fun madeWhen(stamp: Long): String = DateTimeFormatter.ofPattern("d MMMM yyyy, HH:mm", Locale.ENGLISH)
    .format(Instant.ofEpochSecond(stamp).atZone(ZoneId.systemDefault()))

/** The Changelog dialog's most rows before Show more. */
private const val FIRST = 50

/** ☰ → Changelog, as Baude's and Record's dialog (update.changelog_dialog): the commits the running version has (for
 * a commit, from a Mesh link: that one's history, read from the versions host's checkout), newest first, merges left
 * out, under a header that names the version and when it was made. */
@Composable
fun ChangelogScreen(commit: String?, back: () -> Unit) {
    val c = LocalBaude.current
    val ctx = LocalContext.current
    var head by remember(commit) { mutableStateOf<Pair<String, Long>?>(null) }
    var rows by remember(commit) { mutableStateOf<List<Change>?>(null) }
    var all by remember(commit) { mutableStateOf(false) }
    LaunchedEffect(commit) {
        if (commit == null) {
            head = AppVersion.short?.let { it to (AppVersion.made ?: 0L) }
            rows = AppVersion.changes(ctx)
            return@LaunchedEffect
        }
        val host = Mesh.versionsHost()
        val checkout = host?.let { Mesh.conn(it)?.info?.checkout?.ifBlank { null } }
        if (host == null || checkout == null) { rows = emptyList(); return@LaunchedEffect }
        val got = ArrayList<Msg.Commit>()
        var skip = 0
        while (true) {
            val r = asked(host, Msg.Git(what = "log", cwd = checkout, rev = commit, limit = 200, skip = skip).json())
            val g = Msg.GitReply.from(r)
            if (r.optString("op") != Op.GIT || !g.error.isNullOrBlank()) {
                if (got.isEmpty()) { rows = emptyList(); return@LaunchedEffect }
                break
            }
            got.addAll(g.commits.orEmpty())
            if (g.more != true || g.commits.isNullOrEmpty()) break
            skip += 200
        }
        head = got.firstOrNull()?.let { it.sha.orEmpty().take(7) to (it.at?.toLong() ?: 0L) }
        rows = got.filter { (it.parents?.size ?: 0) <= 1 }.map { Change(it.sha.orEmpty().take(7), it.at?.toLong() ?: 0L, it.subject.orEmpty()) }
    }
    Column(Modifier.fillMaxSize().background(c.frame)) {
        Bar(Wording.Changelog.title, subtitle = head?.let { Wording.Changelog.versionMade(commit = it.first, made = madeWhen(it.second)) },
            start = { IconButton(Adwaita.GoPrevious, Wording.Browser.back, onClick = back) })
        val list = rows ?: return@Column
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).navigationBarsPadding()) {
            if (list.isEmpty()) BoxedList(listOf(Unit), "") { ChangeRow(Wording.Changelog.empty, Wording.Changelog.emptyDetail) }
            else {
                val shown: List<Change?> = if (all || list.size <= FIRST) list else list.take(FIRST) + null
                BoxedList(shown, "") { ch ->
                    if (ch == null) ButtonRow(Wording.Meta.showMore(n = list.size - FIRST)) { all = true }
                    else ChangeRow(ch.subject, Wording.Changelog.row(made = madeWhen(ch.made), commit = ch.short))
                }
            }
        }
    }
}

/** A commit's row (Adw.ActionRow): its subject, wrapped, and under it when it was made and its hash. */
@Composable
fun ChangeRow(title: String, subtitle: String) {
    val c = LocalBaude.current
    Row(Modifier.fillMaxWidth().heightIn(min = 50.dp).padding(horizontal = 12.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(title, style = Type.body, color = c.fg)
            Text(subtitle, style = Type.caption, color = c.dim)
        }
    }
}
