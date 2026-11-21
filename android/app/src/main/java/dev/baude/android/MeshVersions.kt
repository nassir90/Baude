package dev.baude.android

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/** A daemon's or an app's code, as Mesh Versions says it: its commit, when that was made, and how many commits it
 * lacks of the mesh's newest (as the host that says so counts them). */
private class Code(val commit: String?, val made: Long?, val behind: Long?)

/** A host on Mesh Versions: its name, its machine's, its daemon's code, and the apps connected to it (by what they
 * are) with theirs. */
private class HostCode(val name: String, val host: String, val daemon: Code,
                       val heads: List<Pair<String, Code>>)

private fun Number?.long(): Long? = this?.toLong()

private fun when_(made: Long): String =
    DateTimeFormatter.ofPattern("d MMMM yyyy, HH:mm").format(Instant.ofEpochSecond(made).atZone(ZoneId.systemDefault()))

/** The mesh as your first host that's up says it is (widgets.mesh_versions): (the newest code's commit and when it
 * was made, or null; the hosts, that one first), or null with none up. */
private fun meshVersions(): Pair<Pair<String, Long?>?, List<HostCode>>? {
    val info = Mesh.versions() ?: return null
    fun heads(hs: List<Msg.Head>?) = hs.orEmpty().map { (it.client ?: "") to Code(it.commit, it.made.long(), it.behind.long()) }
    val hosts = listOf(HostCode(info.name ?: info.id.orEmpty().take(8), info.host.orEmpty(),
        Code(info.commit, info.made.long(), info.behind.long()), heads(info.heads))) +
        info.mesh?.members.orEmpty().map { m ->
            HostCode(m.name ?: m.id.orEmpty().take(8), m.host.orEmpty(),
                Code(m.commit, m.made.long(), m.behind.long()), heads(m.heads))
        }
    val newest = info.mesh?.newest ?: return null to hosts
    val made = hosts.flatMap { listOf(it.daemon) + it.heads.map { h -> h.second } }
        .firstOrNull { it.commit == newest && it.made != null }?.made
    return (newest to made) to hosts
}

/** Where a daemon's or an app's code stands against the newest (widgets.version_standing): (newest, behind, older,
 * unknown or null; what's said of it). */
private fun standing(code: Code, newest: Pair<String, Long?>?): Pair<String?, String> = when {
    code.commit == null -> "unknown" to Wording.Updates.versionUnknown
    newest == null -> null to ""
    code.commit == newest.first -> "newest" to Wording.MeshVersions.newest
    (code.behind ?: 0) > 0 -> "behind" to Wording.MeshVersions.behind(n = code.behind!!)
    code.made != null && newest.second != null && code.made < newest.second!! -> "older" to Wording.MeshVersions.older
    else -> null to ""
}

private fun said(code: Code): String = when {
    code.commit == null -> ""
    code.made == null -> code.commit.take(7)
    else -> Wording.MeshVersions.commitMade(commit = code.commit.take(7), made = when_(code.made))
}

private fun clientName(client: String): String = when (client) {
    "baude" -> Wording.MeshVersions.baude
    "record" -> Wording.MeshVersions.record
    "emission" -> Wording.MeshVersions.emission
    "baude-android" -> Wording.MeshVersions.android
    else -> client
}

/** Settings → Mesh Versions's subtitle: how many daemons and apps are behind the newest code. */
@Composable
fun meshVersionsSummary(): String {
    Mesh.tick.value // (a connection's state changed: said again)
    val (newest, hosts) = meshVersions() ?: return Wording.MeshVersions.none
    val n = hosts.flatMap { listOf(it.daemon) + it.heads.map { h -> h.second } }
        .count { standing(it, newest).first in setOf("behind", "older", "unknown") }
    return if (n > 0) Wording.MeshVersions.someBehind(n = n) else Wording.MeshVersions.allNewest
}

/** Settings → Mesh Versions, as Baude's and Record's: for each host in the mesh (your first host that's up first),
 * its daemon's code and each app connected to it, against the newest code in the mesh; as the hosts say it, as it
 * changes. */
@Composable
fun MeshVersionsScreen(back: () -> Unit) {
    val c = LocalBaude.current
    Mesh.tick.value
    val got = meshVersions()
    Column(Modifier.fillMaxSize().background(c.frame)) {
        Bar(Wording.MeshVersions.title, start = { IconButton(Icons.AutoMirrored.Filled.ArrowBack, Wording.Browser.back, onClick = back) })
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).navigationBarsPadding()) {
            if (got == null) {
                Note(Wording.MeshVersions.none)
                return@Column
            }
            val (newest, hosts) = got
            newest?.second?.let { Note(Wording.MeshVersions.against(commit = newest.first.take(7), made = when_(it))) }
            for (h in hosts) {
                Column(Modifier.padding(top = 8.dp)) {
                    Heading(h.name)
                    if (h.host.isNotBlank()) Text(h.host, style = Type.caption, color = c.dim,
                        modifier = Modifier.padding(horizontal = 14.dp))
                }
                BoxedList(listOf(Wording.HostMenu.daemon to h.daemon) + h.heads.map { clientName(it.first) to it.second }, "") { (title, code) ->
                    val (kind, says) = standing(code, newest)
                    ActionRow(title, said(code), suffix = if (says.isEmpty()) null else ({
                        Text(says, style = Type.body, color = if (kind == "newest" || kind == "unknown") c.dim else c.warning)
                    }))
                }
            }
        }
    }
}

@Composable
private fun Note(text: String) {
    val c = LocalBaude.current
    Text(text, style = Type.caption, color = c.dim, modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp))
}
