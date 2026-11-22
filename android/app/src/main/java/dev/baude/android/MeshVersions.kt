package dev.baude.android

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
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
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.unit.dp

/** A daemon's or an app's code, as the Mesh page says it: its commit, and how many commits it lacks of the mesh's
 * newest (as the host that says so counts them). */
private class Code(val commit: String?, val made: Long?, val behind: Long?)

/** A machine on the Mesh page: its name (as it's called here), its daemon's code (a phone's: its app's) and the apps on
 * it connected to it (by what they are) with theirs; what's said of it while it isn't reached from here (greyed), and
 * the connection Rename and Remove act on (null: this phone, or one the mesh only remembers). */
private class Machine(val id: String, val name: String, val phone: Boolean, val daemon: Code,
                      val heads: List<Pair<String, Code>>, val down: String?, val conn: String?)

private fun Number?.long(): Long? = this?.toLong()

/** Every machine as the Mesh page lists it (meshui.mesh_machines), this phone first: those your first host that's up
 * lists in its mesh; then each host and member known here it doesn't (every one New session's picker has); then
 * those its mesh reached lately; none removed here. With (the newest code's commit and when it was made, or null). */
private fun meshMachines(): Pair<Pair<String, Long?>?, List<Machine>> {
    val info = Mesh.versions()
    val removed = HostStore.removed(Mesh.app)
    val out = ArrayList<Machine>()
    val seen = HashSet<String>()
    fun heads(hs: List<Msg.Head>?) = hs.orEmpty().map { (it.client ?: "") to Code(it.commit, it.made.long(), it.behind.long()) }
    fun connOf(id: String): String? = Mesh.hosts.firstOrNull { h -> (Mesh.conn(h.id)?.info?.id ?: HostStore.machine(Mesh.app, h)) == id }?.id
        ?: "mesh:$id".takeIf { Mesh.conn(it) != null || Mesh.members.containsKey(id) }
    fun down(conn: String?, listed: Boolean): String? {
        val c = conn?.let { Mesh.conn(it) }
        if (c?.state == "up" || conn == null || listed && c?.state == "connecting") return null
        val name = Mesh.label(conn)
        return if (c?.state == "connecting") Wording.Hosts.looking(host = name) else Wording.Hosts.down(host = name)
    }
    fun add(id: String, name: String?, phone: Boolean, daemon: Code, hs: List<Msg.Head>?, conn: String?, down: String?) {
        seen.add(id)
        conn?.let { seen.add(it) }
        out.add(Machine(id, conn?.let { Mesh.label(it) } ?: name?.ifBlank { null } ?: id.take(8), phone, daemon, heads(hs), down, conn))
    }
    val mine = info?.mesh?.members?.firstOrNull { it.id == PhoneHost.id } // (this phone, as the mesh has it)
    add(PhoneHost.id, PhoneHost.name, true, mine?.let { Code(it.commit, it.made.long(), it.behind.long()) }
        ?: Code(BuildConfig.COMMIT.ifEmpty { null }, BuildConfig.MADE.takeIf { it > 0 }, null), null, null, null)
    if (info != null) {
        val i = info.id.orEmpty()
        if (i.isNotEmpty() && i !in seen) add(i, info.name, false, Code(info.commit, info.made.long(), info.behind.long()), info.heads,
            connOf(i), null)
        for (m in info.mesh?.members.orEmpty()) {
            val id = m.id.orEmpty()
            if (id.isEmpty() || id in seen || id in removed) continue
            val conn = connOf(id)
            add(id, m.name, m.dial == false, Code(m.commit, m.made.long(), m.behind.long()), m.heads, conn, down(conn, true))
        }
    }
    for ((conn, label, _) in Mesh.known()) {
        val c = Mesh.conn(conn)
        if (conn in seen || c?.place is Place.Phone) continue
        val id = c?.info?.id ?: conn.removePrefix("mesh:")
        if (id in seen) continue
        val i = c?.info.takeIf { c?.state == "up" }
        add(id, label, Mesh.members[id]?.first?.dial == false, Code(i?.commit, i?.made.long(), i?.behind.long()), i?.heads, conn,
            down(conn, false))
    }
    for (g in info?.mesh?.gone.orEmpty()) {
        val id = g.id.orEmpty()
        if (id.isEmpty() || id in seen || id in removed) continue
        add(id, g.name, false, Code(null, null, null), null, null, Wording.Hosts.down(host = g.name?.ifBlank { null } ?: id.take(8)))
    }
    val newest = info?.mesh?.newest ?: return null to out
    val made = out.filter { it.down == null }.flatMap { listOf(it.daemon) + it.heads.map { h -> h.second } }
        .firstOrNull { it.commit == newest && it.made != null }?.made
    return (newest to made) to out
}

/** Where a daemon's or an app's code stands against the newest (meshui.version_standing): (newest, behind, older,
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

/** ⋮ → Mesh, as Baude's and Record's ☰ → Mesh: each machine (meshMachines), its daemon's code and each app on it, a
 * line each (a phone's, its app's): what it is, its commit (tap for what that has: the changelog), and how far behind
 * the newest code in the mesh it is; greyed, with why, while it isn't reached from here; and each one's ⋮, to rename it
 * here or remove it. As the hosts say it, as it changes. */
@Composable
fun MeshScreen(back: () -> Unit) {
    val c = LocalBaude.current
    Mesh.tick.value
    val (newest, machines) = meshMachines()
    var log by remember { mutableStateOf<String?>(null) }
    var renaming by remember { mutableStateOf<Machine?>(null) }
    var removing by remember { mutableStateOf<Machine?>(null) }
    log?.let {
        androidx.activity.compose.BackHandler { log = null }
        ChangelogScreen(it) { log = null }
        return
    }
    Column(Modifier.fillMaxSize().background(c.frame)) {
        Bar(Wording.MeshVersions.title, start = { IconButton(Adwaita.GoPrevious, Wording.Browser.back, onClick = back) })
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).navigationBarsPadding()) {
            for (m in machines) Column(Modifier.alpha(if (m.down != null) 0.55f else 1f)) { // (greyed, as the picker has it)
                Row(Modifier.fillMaxWidth().heightIn(min = 40.dp).padding(end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.weight(1f)) { Heading(m.name) }
                    if (m.conn != null) Menu(listOf(Wording.Common.renameEllipsis to { renaming = m },
                        Wording.Common.removeEllipsis to { removing = m }))
                }
                val rows = when {
                    m.down != null -> emptyList()
                    m.phone -> listOf(Wording.MeshVersions.android to m.daemon)
                    else -> listOf(Wording.HostMenu.daemon to m.daemon) + m.heads.map { clientName(it.first) to it.second }
                }
                if (m.down != null) Text(m.down, style = Type.caption, color = c.dim, // (its left the lists' edge)
                    modifier = Modifier.padding(start = 10.dp, end = 10.dp, bottom = 4.dp))
                else BoxedList(rows, "") { (title, code) ->
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
        }
    }
    renaming?.let { m ->
        val conn = m.conn ?: return@let
        val own = Mesh.conn(conn)?.info?.name?.ifBlank { null } ?: Mesh.members[m.id]?.first?.name?.ifBlank { null }
            ?: Mesh.hosts.firstOrNull { it.id == conn }?.address ?: m.name
        var name by remember(conn) { mutableStateOf(Mesh.hosts.firstOrNull { it.id == conn }?.name ?: HostStore.alias(Mesh.app, m.id).orEmpty()) }
        AlertDialog(onDismissRequest = { renaming = null }, containerColor = c.dialog,
            title = { Text(Wording.MeshVersions.renameHeading(host = m.name)) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(Wording.MeshVersions.renameBody(name = own), style = Type.body, color = c.dim)
                    Field(name, { name = it }, own)
                }
            },
            confirmButton = { Button(Wording.Common.rename, suggested = true) { Mesh.rename(conn, name); renaming = null } },
            dismissButton = { Button(Wording.Common.cancel) { renaming = null } })
    }
    removing?.let { m ->
        val conn = m.conn ?: return@let
        AlertDialog(onDismissRequest = { removing = null }, containerColor = c.dialog,
            title = { Text(Wording.MeshVersions.removeHeading(host = m.name)) },
            text = { Text(Wording.MeshVersions.removeBody, style = Type.body, color = c.dim) },
            confirmButton = { Button(Wording.Common.remove, destructive = true) { Mesh.remove(conn); removing = null } },
            dismissButton = { Button(Wording.Common.cancel) { removing = null } })
    }
}
