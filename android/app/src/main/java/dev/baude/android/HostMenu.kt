package dev.baude.android

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.unit.dp

/** The menu on the host picker (Baude's host_menu), opened by a long-press: Deactivate or Activate (never this phone's
 * own), then Add Host…. */
@Composable
fun HostMenu(open: Boolean, dismiss: () -> Unit) {
    val id = picked()
    var adding by remember { mutableStateOf(false) }
    Popover(open, dismiss) {
        if (id != null && id != "phone") {
            val now = off(id)
            MenuChoice(if (now) Wording.Hosts.activate else Wording.Hosts.deactivate, false) {
                dismiss()
                Mesh.deactivate(id, !now)
                val name = Mesh.label(id)
                toast(if (now) Wording.Hosts.nowActivated(host = name) else Wording.Hosts.nowDeactivated(host = name))
            }
        }
        if (id != null && id != "phone") MenuDivider()
        MenuChoice(Wording.Sidebar.addHost, false) { dismiss(); adding = true }
    }
    if (adding) AddHost(done = { adding = false })
}

/** Add a host (Baude's ask_add_host): where ssh goes, which runs CONNECT there. */
@Composable
fun AddHost(done: () -> Unit) {
    var dest by remember { mutableStateOf(TextFieldValue()) }
    val host = Host.parse(dest.text)
    fun go() {
        addHost(dest.text) ?: return
        done()
    }
    AlertDialog(
        onDismissRequest = done,
        title = { Text(Wording.NewSession.addHost) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                AddHostFields(dest, { dest = it }, focus = true, onDone = ::go)
            }
        },
        confirmButton = { Button(Wording.Common.add, suggested = host != null, onClick = ::go) },
        dismissButton = { Button(Wording.Common.cancel, onClick = done) },
        containerColor = LocalBaude.current.dialog,
    )
}

/** Add a host's fields (Baude's host_form), its dialog's and Meta's add_host form's: where ssh goes. */
@Composable
fun AddHostFields(dest: TextFieldValue, setDest: (TextFieldValue) -> Unit, focus: Boolean, onDone: () -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        NameField(dest, setDest, Wording.NewSession.hostPlaceholder, onDone, focus = focus)
    }
}

/** A host added where ssh goes, looked for, said so: it, or null when that isn't one. */
fun addHost(dest: String): Host? {
    val h = Host.parse(dest) ?: return null
    Mesh.addHost(h)
    toast(Wording.Hosts.lookingForBaude(dest = dest.trim()))
    return h
}
