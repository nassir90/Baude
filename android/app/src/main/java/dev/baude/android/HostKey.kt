package dev.baude.android

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.unit.dp

/** A host's key this phone hasn't seen, asked about wherever you are (it has no terminal to run ssh in): its
 * fingerprint, to trust or not. Not trusting leaves the host unconnected, and asks again the next time the app starts. */
@Composable
fun HostKeyAsk() {
    val c = LocalBaude.current
    Mesh.tick.value
    val dismissed = androidx.compose.runtime.remember { androidx.compose.runtime.mutableStateListOf<String>() } // (closed without an answer)
    val conn = Mesh.hosts.firstNotNullOfOrNull { h ->
        Mesh.conn(h.id)?.takeIf { it.state == "key" && it.shown != null && "${h.id} ${it.shown!!.fingerprint}" !in Mesh.declined &&
            "${h.id} ${it.shown!!.fingerprint}" !in dismissed }
    } ?: return
    val host = conn.host ?: return
    val shown = conn.shown ?: return
    val decline = { Mesh.declined.add("${host.id} ${shown.fingerprint}"); Unit }
    AlertDialog(
        onDismissRequest = { dismissed.add("${host.id} ${shown.fingerprint}") },
        title = { Text(Wording.PhoneKeys.trustHeading(host = Mesh.label(host.id))) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(Wording.PhoneKeys.trustBody(dest = host.dest))
                KeyLines(null, shown.type, shown.fingerprint)
            }
        },
        confirmButton = { Button(Wording.PhoneKeys.trust, suggested = true) { Mesh.trust(host, shown) } },
        dismissButton = { Button(Wording.PhoneKeys.dontTrust, onClick = decline) },
        containerColor = c.dialog,
    )
}

@Composable
private fun KeyLines(label: String?, type: String, fingerprint: String) {
    val c = LocalBaude.current
    Column {
        Text(listOfNotNull(label, type).joinToString(" · "), style = Type.caption, color = c.dim)
        SelectionContainer { Text(fingerprint, style = Type.mono, color = c.fg) }
    }
}
