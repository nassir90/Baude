package dev.baude.android

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Icon
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp

/** An item of the main menu: its label, a check mark where it's a toggle (checked: whether it's on; null: it isn't
 * one), whether it can be picked (greyed if not), and what it does. */
class MenuItem(val label: String, val checked: Boolean? = null, val enabled: Boolean = true,
               val content: (@Composable (go: () -> Unit) -> Unit)? = null, val hosts: Hosts? = null, val go: () -> Unit)

/** A family row's caret (menufamily.FamilyRow): New session's hosts that take op, the active one ticked; on(id) as one's
 * picked. */
class Hosts(val op: String, val active: String?, val on: (String) -> Unit)

/** ☰'s main menu, as the header bar's: sections of one-line items with a rule between them (as Baude's and Record's:
 * the chat's, the view's, the views over the window, the app's); with a toggle among them every item has a column for
 * its check mark before it. An empty section isn't shown; a section that opens with a drawn item (content) draws its own
 * rule, and none where it draws nothing. */
@Composable
fun MainMenu(sections: List<List<MenuItem>>) {
    var open by remember { mutableStateOf(false) }
    val shown = sections.filter { it.isNotEmpty() }
    Box {
        IconButton(Adwaita.ViewMore, Wording.ChatHeader.menu) { open = true }
        Popover(open, { open = false }, ticks = if (shown.flatten().any { it.checked != null }) Ticks.START else Ticks.NONE) {
            shown.forEachIndexed { i, items ->
                if (i > 0 && items.first().content == null) MenuDivider()
                for (item in items) {
                    val content = item.content
                    val hosts = item.hosts
                    if (content != null) content { open = false; item.go() }
                    else if (hosts != null) FamilyRow(item.label, hosts, item.enabled, { open = false; item.go() }) { id -> open = false; hosts.on(id) }
                    else MenuChoice(item.label, item.checked == true, enabled = item.enabled) {
                        open = false
                        item.go()
                    }
                }
            }
        }
    }
}

/** One of ⋮'s Kubernetes, Docker, Databases and Terminal (menufamily.FamilyRow): its name, tapped, opens it on the
 * active host (go; greyed, not enabled, where that host doesn't take it); the caret at its end, New session's host list
 * (HostChoices) of the hosts that take it, the one tapped opening it there (pick). */
@Composable
private fun FamilyRow(label: String, hosts: Hosts, enabled: Boolean, go: () -> Unit, pick: (String) -> Unit) {
    val c = LocalBaude.current
    var list by remember { mutableStateOf(false) }
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.weight(1f)) { MenuChoice(label, false, enabled = enabled, onClick = go) }
        Box {
            Box(Modifier.size(Popups.row).clickable { list = true }, contentAlignment = Alignment.Center) {
                Icon(Adwaita.PanDown, label, tint = c.fg, modifier = Modifier.size(16.dp.icon))
            }
            Popover(list, { list = false }, ticks = Ticks.END) {
                HostChoices(newSessionHosts().filter { it != "phone" && takes(it, hosts.op) }, hosts.active) { id ->
                    list = false
                    pick(id)
                }
            }
        }
    }
}

/** Full-width text as the menu and Settings have it: toggled, kept. */
@Composable
private fun fullWidth(): MenuItem {
    val ctx = LocalContext.current
    return MenuItem(Wording.MetaSettings.fullWidth, Settings.fullWidth.value) {
        Settings.fullWidth.value = !Settings.fullWidth.value
        Settings.save(ctx)
    }
}

/** A chat's ☰ (baudewindow.BaudeWindow's menu, in its sections): Find, Copy Transcript for an Issue, Read-only
 * session (checked and greyed while the chat's archived: read-only till it's unarchived); Full-width text; Kubernetes…, Docker, Databases…, Terminal (each where a host takes it, greyed where the chat's host doesn't); Settings…, Changelog, Mesh, Send
 * Feedback…, Ongoing Feedback. screen opens one of "settings", "changelog", "mesh", "fixes", "databases", "kubernetes",
 * "docker" and "terminal" over the chat. */
@Composable
fun ChatMenu(hostId: String, sid: String, feedback: () -> Unit, screen: (String) -> Unit, writes: Boolean = true,
             tool: (String, String) -> Unit = { s, h -> if (h == hostId) screen(s) }) {
    val ctx = LocalContext.current
    MainMenu(listOf(
        listOfNotNull(
            MenuItem(Wording.Find.placeholder) { Find.open["$hostId/$sid"] = true },
            MenuItem(Wording.Window.copyTranscript) { copyReport(ctx, hostId, sid) },
            if (sid.isEmpty()) null else MenuItem(Wording.Window.readOnly, Settings.isReadOnly(sid) || isArchived(hostId, sid) ||
                !writes, enabled = !isArchived(hostId, sid) && writes) { // (writes false: its host lets this phone only read it)
                if (sid in Settings.readOnly) Settings.readOnly.remove(sid) else Settings.readOnly.add(sid)
                Settings.save(ctx)
            },
        ),
        listOf(fullWidth()),
        listOfNotNull(
            family(Wording.Window.kubernetes, "kube", hostId, { tool("kubernetes", it) }) { screen("kubernetes") },
            family(Wording.Window.docker, "docker", hostId, { tool("docker", it) }) { screen("docker") },
            family(Wording.Window.databaseSchemas, "databases", hostId, { tool("databases", it) }) { screen("databases") },
            family(Wording.Multiplex.terminalMenu, "terminal", hostId, { tool("terminal", it) }) { screen("terminal") },
        ),
        listOf(usage(hostId to sid) { screen("subscriptions") }),
        listOf(
            MenuItem(Wording.Window.settings) { screen("settings") },
            MenuItem(Wording.Changelog.title) { screen("changelog") },
            MenuItem(Wording.MeshVersions.title) { screen("mesh") },
            MenuItem(Wording.Fixes.sendFeedback) { feedback() },
            MenuItem(Wording.Window.ongoingFeedback) { screen("fixes") },
        ),
    ))
}

/** The home screen's ☰: the items that don't need a chat open, and Kubernetes…, Databases…, Terminal on the
 * host picked beside New session (each where a host takes it, greyed where the one picked doesn't), as Baude's ☰ opens them on its window's host. tool opens
 * one of "kubernetes", "docker", "databases" and "terminal" on a host. */
@Composable
fun HomeMenu(settings: () -> Unit, changelog: () -> Unit, mesh: () -> Unit, feedback: () -> Unit, fixes: () -> Unit,
             tool: (String, String) -> Unit = { _, _ -> }, subscriptions: () -> Unit = {}) {
    val host = picked()?.takeIf { it != "phone" }
    MainMenu(listOf(
        listOf(fullWidth()),
        listOfNotNull(
            family(Wording.Window.kubernetes, "kube", host, { tool("kubernetes", it) }) { host?.let { tool("kubernetes", it) } },
            family(Wording.Window.docker, "docker", host, { tool("docker", it) }) { host?.let { tool("docker", it) } },
            family(Wording.Window.databaseSchemas, "databases", host, { tool("databases", it) }) { host?.let { tool("databases", it) } },
            family(Wording.Multiplex.terminalMenu, "terminal", host, { tool("terminal", it) }) { host?.let { tool("terminal", it) } },
        ),
        listOf(usage(null, subscriptions)),
        listOf(
            MenuItem(Wording.Window.settings) { settings() },
            MenuItem(Wording.Changelog.title) { changelog() },
            MenuItem(Wording.MeshVersions.title) { mesh() },
            MenuItem(Wording.Fixes.sendFeedback) { feedback() },
            MenuItem(Wording.Window.ongoingFeedback) { fixes() },
        ),
    ))
}

/** A family row (FamilyRow), as the desktop's: label, its name opening it on host (go; greyed where host, or none,
 * doesn't take op), its caret on another that takes op (on). None where no host of New session's takes op. */
private fun family(label: String, op: String, host: String?, on: (String) -> Unit, go: () -> Unit): MenuItem? {
    if (newSessionHosts().none { it != "phone" && takes(it, op) }) return null
    return MenuItem(label, enabled = host != null && takes(host, op), hosts = Hosts(op, host, on), go = go)
}

/** ☰'s usage row (MenuUsage): the usage of the subscription the chat (host, sid; null: none) runs under; tapped,
 * Settings → Subscriptions (go). */
private fun usage(chat: Pair<String, String>?, go: () -> Unit) =
    MenuItem(Wording.Subscriptions.title, content = { MenuUsage(chat, it) }, go = go)

/** Whether a chat is archived on its host: read-only till it's unarchived (baudewindow's chat_archived). */
fun isArchived(hostId: String, sid: String) = sid.isNotEmpty() && Mesh.session(hostId, sid)?.archived == true
