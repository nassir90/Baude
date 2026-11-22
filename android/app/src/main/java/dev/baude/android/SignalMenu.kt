package dev.baude.android

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
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

/** The header's chain-link button (BaudeWindow.signal_btn) and its dropdown: this chat's two Signal switches. Lit while
 * its links are shown. Show links is the chat's own switch; Activate Signal is drawn off and dimmed. */
@Composable
fun SignalButton(hostId: String, sid: String) {
    val c = LocalBaude.current
    SignalChats.version.intValue
    var open by remember { mutableStateOf(false) }
    val (on, links) = SignalChats.get(hostId, sid)
    val off = SignalChats.off(hostId, sid)
    val now = Wording.Signal.notRunning(n = SignalNames.reply(hostId, sid)?.refs.orEmpty().size)
    Box {
        IconButton(Adwaita.InsertLink, if (off) Wording.SignalMenu.tipOff else Wording.SignalMenu.tipLinks(status = now),
            tint = if (off) c.fg else c.accent.copy(alpha = 0.5f)) { open = true }
        Popover(open, { open = false }) {
            Column(Modifier.width(310.dp).padding(horizontal = Popups.inset, vertical = Popups.pad),
                verticalArrangement = Arrangement.spacedBy(12.dp)) {
                SwitchLine(Wording.SignalMenu.showLinks, Wording.SignalMenu.showLinksDetail, links, true) {
                    SignalChats.set(hostId, sid, on, it)
                }
                SwitchLine(Wording.SignalMenu.activate, Wording.SignalMenu.activateDetail, false, false) {}
            }
        }
    }
}

/** A line of the dropdown: a title, what it does under it, and its switch. */
@Composable
private fun SwitchLine(title: String, detail: String, on: Boolean, enabled: Boolean, set: (Boolean) -> Unit) {
    val c = LocalBaude.current
    Row(Modifier.alpha(if (enabled) 1f else 0.5f), horizontalArrangement = Arrangement.spacedBy(12.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(title, style = Type.body, color = c.fg)
            Text(detail, style = Type.caption, color = c.dim)
        }
        Switch(on, set, enabled = enabled, colors = SwitchDefaults.colors(checkedTrackColor = c.accentBg, checkedThumbColor = c.accentFg))
    }
}
