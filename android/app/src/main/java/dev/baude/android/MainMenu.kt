package dev.baude.android

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext

/** An item of the main menu: its label, a check mark where it's a toggle (checked: whether it's on; null: it isn't
 * one), whether it can be picked (greyed if not), and what it does. */
class MenuItem(val label: String, val checked: Boolean? = null, val enabled: Boolean = true,
               val content: (@Composable () -> Unit)? = null, val go: () -> Unit)

/** ☰'s main menu, as the header bar's: sections of one-line items with a rule between them (as Baude's and Record's:
 * the chat's, the view's, the views over the window, the app's); with a toggle among them every item has a column for
 * its check mark before it. An empty section isn't shown. */
@Composable
fun MainMenu(sections: List<List<MenuItem>>) {
    var open by remember { mutableStateOf(false) }
    val shown = sections.filter { it.isNotEmpty() }
    Box {
        IconButton(Adwaita.ViewMore, Wording.ChatHeader.menu) { open = true }
        Popover(open, { open = false }, ticks = if (shown.flatten().any { it.checked != null }) Ticks.START else Ticks.NONE) {
            shown.forEachIndexed { i, items ->
                if (i > 0) MenuDivider()
                for (item in items) {
                    val content = item.content
                    if (content != null) Box(Modifier.clickable { open = false; item.go() }) { content() }
                    else MenuChoice(item.label, item.checked == true, enabled = item.enabled) {
                        open = false
                        item.go()
                    }
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
 * session (checked and greyed while the chat's archived: read-only till it's unarchived); Full-width text; Kubernetes…, Docker, Databases…, Terminal (a host that takes them); Settings…, Changelog, Mesh, Send
 * Feedback…, Ongoing Feedback. screen opens one of "settings", "changelog", "mesh", "fixes", "databases", "kubernetes",
 * "docker" and "terminal" over the chat. */
@Composable
fun ChatMenu(hostId: String, sid: String, feedback: () -> Unit, screen: (String) -> Unit, writes: Boolean = true) {
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
            if (takes(hostId, "kube")) MenuItem(Wording.Window.kubernetes) { screen("kubernetes") } else null,
            if (takes(hostId, "docker")) MenuItem(Wording.Window.docker) { screen("docker") } else null,
            if (takes(hostId, "databases")) MenuItem(Wording.Window.databaseSchemas) { screen("databases") } else null,
            if (takes(hostId, "terminal")) MenuItem(Wording.Multiplex.terminalMenu) { screen("terminal") } else null,
        ),
        listOf(usage { screen("subscriptions") }),
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
 * host picked beside New session (a host that takes them), as Baude's ☰ opens them on its window's host. tool opens
 * one of "kubernetes", "docker", "databases" and "terminal" on a host. */
@Composable
fun HomeMenu(settings: () -> Unit, changelog: () -> Unit, mesh: () -> Unit, feedback: () -> Unit, fixes: () -> Unit,
             tool: (String, String) -> Unit = { _, _ -> }, subscriptions: () -> Unit = {}) {
    val host = picked()?.takeIf { it != "phone" }
    MainMenu(listOf(
        listOf(fullWidth()),
        if (host == null) emptyList() else listOfNotNull(
            if (takes(host, "kube")) MenuItem(Wording.Window.kubernetes) { tool("kubernetes", host) } else null,
            if (takes(host, "docker")) MenuItem(Wording.Window.docker) { tool("docker", host) } else null,
            if (takes(host, "databases")) MenuItem(Wording.Window.databaseSchemas) { tool("databases", host) } else null,
            if (takes(host, "terminal")) MenuItem(Wording.Multiplex.terminalMenu) { tool("terminal", host) } else null,
        ),
        listOf(usage(subscriptions)),
        listOf(
            MenuItem(Wording.Window.settings) { settings() },
            MenuItem(Wording.Changelog.title) { changelog() },
            MenuItem(Wording.MeshVersions.title) { mesh() },
            MenuItem(Wording.Fixes.sendFeedback) { feedback() },
            MenuItem(Wording.Window.ongoingFeedback) { fixes() },
        ),
    ))
}

/** ☰'s usage row (MenuUsage): the current subscription's usage; tapped, Settings → Subscriptions (go). */
private fun usage(go: () -> Unit) = MenuItem(Wording.Subscriptions.title, content = { MenuUsage() }, go = go)

/** Whether a chat is archived on its host: read-only till it's unarchived (baudewindow's chat_archived). */
fun isArchived(hostId: String, sid: String) = sid.isNotEmpty() && Mesh.session(hostId, sid)?.archived == true
