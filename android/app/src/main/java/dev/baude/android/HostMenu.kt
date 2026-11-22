package dev.baude.android

import android.content.ClipData
import android.content.ClipboardManager
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp

/** The menu on the host picker (Baude's host_menu), opened by a long-press: Deactivate or Activate (never this phone's
 * own), then Add Host…. */
@Composable
fun HostMenu(open: Boolean, dismiss: () -> Unit) {
    val c = LocalBaude.current
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
        if (id != null && id != "phone") HorizontalDivider(color = c.rule)
        MenuChoice(Wording.Sidebar.addHost, false) { dismiss(); adding = true }
    }
    if (adding) AddHost(done = { adding = false })
}

/** Add a host (Baude's ask_add_host): where ssh goes, which runs CONNECT there. given, edited: Meta's add_host form's
 * values, filled by it and by you. */
@Composable
fun AddHost(done: () -> Unit, given: org.json.JSONObject? = null, edited: ((org.json.JSONObject) -> Unit)? = null,
            added: () -> Unit = {}) {
    var dest by remember { mutableStateOf(given?.str("destination").orEmpty()) }
    LaunchedEffect(dest) { edited?.invoke(org.json.JSONObject().put("destination", dest)) }
    val host = Host.parse(dest)
    fun go() {
        val h = host ?: return
        Mesh.addHost(h)
        toast(Wording.Hosts.lookingForBaude(dest = dest.trim()))
        added()
        done()
    }
    AlertDialog(
        onDismissRequest = done,
        title = { Text(Wording.NewSession.addHost) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                PhoneKey()
                NameField(dest, { dest = it }, Wording.NewSession.hostPlaceholder, ::go)
            }
        },
        confirmButton = { Button(Wording.Common.add, suggested = host != null, onClick = ::go) },
        dismissButton = { Button(Wording.Common.cancel, onClick = done) },
        containerColor = LocalBaude.current.dialog,
    )
}

/** This phone's own ssh key, where a host's added (it has no ssh agent): its authorized_keys line to copy, a new one to
 * make, or Make Key while it has none. */
@Composable
private fun PhoneKey() {
    val c = LocalBaude.current
    val ctx = LocalContext.current
    var renewing by remember { mutableStateOf(false) }
    val key = Mesh.key.value
    val error = Mesh.keyError.value
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(Wording.PhoneKeys.heading, style = Type.bold, color = c.fg)
        if (key == null && error != null) {
            Text(error, style = Type.caption, color = c.destructive)
            Button(Wording.Common.tryAgain) { Mesh.readKey() }
        } else if (key == null) {
            Text(Wording.PhoneKeys.none, style = Type.caption, color = c.dim)
            if (Mesh.making.value) Text(Wording.PhoneKeys.making, style = Type.caption, color = c.dim)
            else Button(Wording.PhoneKeys.make, suggested = true) { Mesh.makeKey() }
        } else {
            Text(Wording.PhoneKeys.putLine, style = Type.caption, color = c.dim)
            SelectionContainer {
                Text(key.line, style = Type.mono, color = c.fg, modifier = Modifier.fillMaxWidth()
                    .clip(RoundedCornerShape(Gap.codeRadius)).background(c.raised).padding(8.dp))
            }
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Button(Wording.Common.copy) {
                    ctx.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("Baude's key", key.line))
                    toast(Wording.Report.copied)
                }
                Button(Wording.PhoneKeys.newEllipsis) { renewing = true }
            }
        }
        HorizontalDivider(color = c.rule)
    }
    if (renewing) AlertDialog(onDismissRequest = { renewing = false }, title = { Text(Wording.PhoneKeys.newHeading) },
        text = { Text(Wording.PhoneKeys.newBody) },
        confirmButton = { Button(Wording.PhoneKeys.make, destructive = true) { Mesh.makeKey(); renewing = false } },
        dismissButton = { Button(Wording.Common.cancel) { renewing = false } }, containerColor = c.dialog)
}
