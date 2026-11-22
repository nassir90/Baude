package dev.baude.android

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.size
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp

/** An item of the main menu: its label, a check mark where it's a toggle (checked: whether it's on; null: it isn't
 * one), and what it does. */
class MenuItem(val label: String, val checked: Boolean? = null, val go: () -> Unit)

/** ☰'s main menu, as the header bar's: a plain list, one line each; with a toggle among them every item has a slot for
 * its check mark before it. */
@Composable
fun MainMenu(items: List<MenuItem>) {
    val c = LocalBaude.current
    var open by remember { mutableStateOf(false) }
    val slot = items.any { it.checked != null }
    Box {
        IconButton(Adwaita.ViewMore, Wording.ChatHeader.menu) { open = true }
        Popover(open, { open = false }) {
            for (item in items) DropdownMenuItem({ Text(item.label, style = Type.body, color = c.fg) },
                { open = false; item.go() }, modifier = Modifier.heightIn(min = 38.dp),
                leadingIcon = if (slot) ({
                    Box(Modifier.size(16.dp.icon)) {
                        if (item.checked == true) Icon(Adwaita.ObjectSelect, null, tint = c.fg, modifier = Modifier.size(16.dp.icon))
                    }
                }) else null)
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

/** A chat's ☰ (baude.BaudeWindow's menu): Settings…, Changelog, Mesh, Full-width text, Read-only session, Send
 * Feedback…, Ongoing Feedback, Copy Transcript for an Issue. screen opens one of "settings", "changelog", "mesh" and "fixes" over the chat. */
@Composable
fun ChatMenu(hostId: String, sid: String, feedback: () -> Unit, screen: (String) -> Unit) {
    val ctx = LocalContext.current
    MainMenu(listOfNotNull(
        MenuItem(Wording.Window.settings) { screen("settings") },
        MenuItem(Wording.Changelog.title) { screen("changelog") },
        MenuItem(Wording.MeshVersions.title) { screen("mesh") },
        fullWidth(),
        if (sid.isEmpty()) null else MenuItem(Wording.Window.readOnly, Settings.isReadOnly(sid)) {
            if (sid in Settings.readOnly) Settings.readOnly.remove(sid) else Settings.readOnly.add(sid)
            Settings.save(ctx)
        },
        MenuItem(Wording.Find.placeholder) { Find.open["$hostId/$sid"] = true },
        MenuItem(Wording.Fixes.sendFeedback) { feedback() },
        MenuItem(Wording.Window.ongoingFeedback) { screen("fixes") },
        MenuItem(Wording.Window.copyTranscript) { copyReport(ctx, hostId, sid) },
    ))
}

/** The home screen's ☰: the items that don't need a chat open. */
@Composable
fun HomeMenu(settings: () -> Unit, changelog: () -> Unit, mesh: () -> Unit, feedback: () -> Unit, fixes: () -> Unit) {
    MainMenu(listOf(
        MenuItem(Wording.Window.settings) { settings() },
        MenuItem(Wording.Changelog.title) { changelog() },
        MenuItem(Wording.MeshVersions.title) { mesh() },
        fullWidth(),
        MenuItem(Wording.Fixes.sendFeedback) { feedback() },
        MenuItem(Wording.Window.ongoingFeedback) { fixes() },
    ))
}
