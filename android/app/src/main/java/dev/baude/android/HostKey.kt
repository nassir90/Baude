package dev.baude.android

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp

/** A host's key this phone hasn't seen, asked about wherever you are (it has no terminal to run ssh in), as ssh asks
 * it: the host and its address, the key's type and SHA256 fingerprint, and whether to go on connecting. No leaves the
 * host unconnected, and asks again the next time the app starts. */
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
    val line = Wording.PhoneKeys.fingerprint(type = sshKeyType(shown.type), fingerprint = shown.fingerprint)
    val at = line.indexOf(shown.fingerprint)
    AlertDialog(
        onDismissRequest = { dismissed.add("${host.id} ${shown.fingerprint}") },
        text = {
            SelectionContainer {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(Wording.PhoneKeys.authenticity(host = Mesh.label(host.id), address = host.address), style = Type.body, color = c.fg)
                    Text(buildAnnotatedString { // (the fingerprint in the code font, as a terminal shows it)
                        append(line.take(at))
                        withStyle(Type.mono.toSpanStyle()) { append(shown.fingerprint) }
                        append(line.drop(at + shown.fingerprint.length))
                    }, style = Type.body, color = c.fg)
                    Text(Wording.PhoneKeys.continueConnecting, style = Type.body, color = c.fg)
                }
            }
        },
        confirmButton = { Button(Wording.Common.yes, suggested = true) { Mesh.trust(host, shown) } },
        dismissButton = { Button(Wording.Common.no, onClick = decline) },
        containerColor = c.dialog,
    )
}

/** A key's type as ssh's prompt names it (sshkey_type): ED25519, ECDSA, RSA, DSA, and their security-key kinds. */
fun sshKeyType(type: String): String = when {
    type == "ssh-ed25519" -> "ED25519"
    type.startsWith("sk-ssh-ed25519") -> "ED25519-SK"
    type.startsWith("sk-ecdsa-") -> "ECDSA-SK"
    type.startsWith("ecdsa-") -> "ECDSA"
    type == "ssh-rsa" || type.startsWith("rsa-") -> "RSA"
    type == "ssh-dss" -> "DSA"
    else -> type.uppercase()
}
