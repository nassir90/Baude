package dev.baude.android

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/** A daemon's or an app's code, as the Mesh page says it: its commit, and how many commits it lacks of the mesh's
 * newest (as the host that says so counts them). */
private class Code(val commit: String?, val made: Long?, val behind: Long?)

/** A host on the Mesh page: its name, its daemon's code, and the apps connected to it (by what they are) with theirs. */
private class HostCode(val id: String, val name: String, val daemon: Code, val heads: List<Pair<String, Code>>)

private fun Number?.long(): Long? = this?.toLong()

/** The mesh as your first host that's up says it is (widgets.mesh_versions): (the newest code's commit and when it
 * was made, or null; the hosts, that one first, each once; the hosts in the mesh not reached now, as (id, name)), or
 * null with none up. */
private fun meshVersions(): Triple<Pair<String, Long?>?, List<HostCode>, List<Pair<String, String>>>? {
    val info = Mesh.versions() ?: return null
    fun heads(hs: List<Msg.Head>?) = hs.orEmpty().map { (it.client ?: "") to Code(it.commit, it.made.long(), it.behind.long()) }
    val hosts = (listOf(HostCode(info.id.orEmpty(), info.name ?: info.id.orEmpty().take(8),
        Code(info.commit, info.made.long(), info.behind.long()), heads(info.heads))) +
        info.mesh?.members.orEmpty().map { m ->
            HostCode(m.id.orEmpty(), m.name ?: m.id.orEmpty().take(8),
                Code(m.commit, m.made.long(), m.behind.long()), heads(m.heads))
        }).distinctBy { it.id }
    val seen = hosts.map { it.id }.toSet()
    val away = info.mesh?.gone.orEmpty().filter { it.id !in seen }.map { it.id.orEmpty() to (it.name ?: it.id.orEmpty().take(8)) }
    val newest = info.mesh?.newest ?: return Triple(null, hosts, away)
    val made = hosts.flatMap { listOf(it.daemon) + it.heads.map { h -> h.second } }
        .firstOrNull { it.commit == newest && it.made != null }?.made
    return Triple(newest to made, hosts, away)
}

/** Where a daemon's or an app's code stands against the newest (widgets.version_standing): (newest, behind, older,
 * unknown or null; what's said of it). */
private fun standing(code: Code, newest: Pair<String, Long?>?): Pair<String?, String> = when {
    code.commit == null -> "unknown" to ""
    newest == null -> null to ""
    code.commit == newest.first -> "newest" to Wording.MeshVersions.newest
    (code.behind ?: 0) > 0 -> "behind" to Wording.MeshVersions.behind(n = code.behind!!)
    code.made != null && newest.second != null && code.made < newest.second!! -> "older" to Wording.MeshVersions.older
    else -> null to ""
}

private fun clientName(client: String): String = when (client) {
    "baude" -> Wording.MeshVersions.baude
    "record" -> Wording.MeshVersions.record
    "emission" -> Wording.MeshVersions.emission
    "baude-android" -> Wording.MeshVersions.android
    else -> client
}

/** ⋮ → Mesh, as Baude's and Record's ☰ → Mesh: for each host in the mesh (your first host that's up first), its
 * daemon's code and each app connected to it, a line each: what it is, its commit (tap for what that has: the
 * changelog), and how far behind the newest code in the mesh it is; as the hosts say it, as it changes. */
@Composable
fun MeshScreen(back: () -> Unit) {
    val c = LocalBaude.current
    Mesh.tick.value
    val got = meshVersions()
    var log by remember { mutableStateOf<String?>(null) }
    log?.let {
        androidx.activity.compose.BackHandler { log = null }
        ChangelogScreen(it) { log = null }
        return
    }
    Column(Modifier.fillMaxSize().background(c.frame)) {
        Bar(Wording.MeshVersions.title, start = { IconButton(Adwaita.GoPrevious, Wording.Browser.back, onClick = back) })
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).navigationBarsPadding()) {
            if (got == null) {
                Text(Wording.MeshVersions.none, style = Type.caption, color = c.dim,
                    modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp))
                return@Column
            }
            val (newest, hosts, away) = got
            for (h in hosts) {
                Heading(h.name)
                BoxedList(listOf(Wording.HostMenu.daemon to h.daemon) + h.heads.map { clientName(it.first) to it.second }, "") { (title, code) ->
                    val (kind, says) = standing(code, newest)
                    Row(Modifier.fillMaxWidth().heightIn(min = 44.dp).padding(horizontal = 12.dp, vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        Text(title, style = Type.body, color = c.fg, maxLines = 1, modifier = Modifier.weight(1f))
                        if (code.commit == null) Text(Wording.Updates.versionUnknown, style = Type.body, color = c.dim)
                        else Text(code.commit.take(7), style = Type.mono, color = c.accent,
                            modifier = Modifier.clickable { log = code.commit })
                        if (code.commit != null) Text(says, style = Type.body, textAlign = androidx.compose.ui.text.style.TextAlign.End,
                            color = if (kind == "newest") c.dim else c.warning, maxLines = 1, modifier = Modifier.width(150.dp))
                    }
                }
            }
            for ((_, name) in away) {
                Heading(name)
                Text(Wording.Hosts.down(host = name), style = Type.caption, color = c.dim, // (its left the lists' edge)
                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp))
            }
        }
    }
}
