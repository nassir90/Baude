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
import androidx.compose.ui.unit.dp

/** The menu on the host picker (Baude's host_menu), opened by a long-press: Host Command… (a host reached over ssh),
 * Deactivate or Activate (never this phone's own), then Add Host…. */
@Composable
fun HostMenu(open: Boolean, dismiss: () -> Unit) {
    val id = picked()
    val h = Mesh.hosts.firstOrNull { it.id == id }
    var command by remember { mutableStateOf<Host?>(null) }
    var adding by remember { mutableStateOf(false) }
    Popover(open, dismiss) {
        if (h != null) MenuChoice(Wording.Hosts.command, false) { dismiss(); command = h }
        if (id != null && id != "phone") {
            val now = off(id)
            MenuChoice(if (now) Wording.Hosts.activate else Wording.Hosts.deactivate, false) {
                dismiss()
                Mesh.deactivate(id, !now)
                val name = Mesh.label(id)
                toast(if (now) Wording.Hosts.nowActivated(host = name) else Wording.Hosts.nowDeactivated(host = name))
            }
        }
        if (h != null || id != null && id != "phone") MenuDivider()
        MenuChoice(Wording.Sidebar.addHost, false) { dismiss(); adding = true }
    }
    command?.let { HostCommand(it) { command = null } }
    if (adding) AddHost(done = { adding = false })
}

/** Add a host (Baude's ask_add_host): where ssh goes, and its host process's command there (empty: on its PATH). */
@Composable
fun AddHost(done: () -> Unit) {
    var dest by remember { mutableStateOf("") }
    var command by remember { mutableStateOf("") }
    val host = Host.parse(dest, "", command)
    fun go() {
        addHost(dest, command) ?: return
        done()
    }
    AlertDialog(
        onDismissRequest = done,
        title = { Text(Wording.NewSession.addHost) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                AddHostFields(dest, { dest = it }, command, { command = it }, focus = true, onDone = ::go)
            }
        },
        confirmButton = { Button(Wording.Common.add, suggested = host != null, onClick = ::go) },
        dismissButton = { Button(Wording.Common.cancel, onClick = done) },
        containerColor = LocalBaude.current.dialog,
    )
}

/** Add a host's fields (Baude's host_form), its dialog's and Meta's add_host form's: where ssh goes, then the command. */
@Composable
fun AddHostFields(dest: String, setDest: (String) -> Unit, command: String, setCommand: (String) -> Unit, focus: Boolean,
                  onDone: () -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        NameField(dest, setDest, Wording.NewSession.hostPlaceholder, onDone, focus = focus)
        Field(command, setCommand, Wording.Hosts.commandPlaceholder(command = "baude_headless.py"))
    }
}

/** A host added where ssh goes (and its command there), looked for, said so: it, or null when that isn't one. */
fun addHost(dest: String, command: String): Host? {
    val h = Host.parse(dest, "", command) ?: return null
    Mesh.addHost(h)
    toast(Wording.Hosts.lookingForBaude(dest = dest.trim()))
    return h
}

/** Host Command… (Baude's ask_host_command): the command ssh runs on the host, with connect after it. */
@Composable
fun HostCommand(h: Host, done: () -> Unit) {
    val c = LocalBaude.current
    var text by remember { mutableStateOf(h.command.takeIf { it != "baude_headless.py" }.orEmpty()) }
    fun go() {
        Mesh.setCommand(h, text.trim())
        done()
    }
    AlertDialog(
        onDismissRequest = done,
        title = { Text(Wording.Hosts.commandHeading(host = Mesh.label(h.id))) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(Wording.Hosts.commandBody(host = h.dest), style = Type.body, color = c.dim)
                NameField(text, { text = it }, Wording.Hosts.commandPlaceholder(command = "baude_headless.py"), ::go)
            }
        },
        confirmButton = { Button(Wording.Common.save, suggested = true, onClick = ::go) },
        dismissButton = { Button(Wording.Common.cancel, onClick = done) },
        containerColor = c.dialog,
    )
}
