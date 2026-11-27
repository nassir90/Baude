package dev.baude.android

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp

/** What a decider's row says of it (decidersview.status): Running at its URL, or why it isn't answering. */
fun deciderStatus(d: Msg.Decider): String =
    if (d.reachable == true) Wording.Deciders.running(url = d.url.orEmpty())
    else d.error.orEmpty().replaceFirstChar { it.uppercase() }

/** Settings → Dependencies' Deciders group (decidersview.Group), under its other groups: the decision models' servers
 * the host knows, each with whether it answers, Test and Remove, then a name and a URL to add one (the deciders op). */
@Composable
fun DecidersGroup(hostId: String) {
    var listed by remember(hostId) { mutableStateOf<List<Msg.Decider>?>(null) }
    var error by remember(hostId) { mutableStateOf<String?>(null) }
    var name by remember(hostId) { mutableStateOf("") }
    var url by remember(hostId) { mutableStateOf("http://127.0.0.1:8000") }
    fun ask(msg: Msg.Deciders) = Mesh.ask(hostId, msg.json()) { r ->
        if (r.optString("op") == Op.ERROR) {
            val said = Msg.Error.from(r).said.orEmpty()
            if (listed == null) error = said else toast(said)
        } else {
            val got = Msg.DecidersReply.from(r)
            listed = got.deciders.orEmpty().filter { it.host == null }
            error = null
            if (!got.error.isNullOrBlank()) toast(got.error) else if (got.what == "add") name = ""
        }
    }
    LaunchedEffect(hostId) { ask(Msg.Deciders()) }
    Heading(Wording.Deciders.title)
    val e = error
    if (e != null) BoxedList(listOf(e), "") { ActionRow(it, null) }
    else BoxedList(listed.orEmpty() + listOf("name", "url", "add"), "") { r ->
        when (r) {
            is Msg.Decider -> DeciderRow(r, test = { ask(Msg.Deciders(what = "test", id = r.id)) },
                remove = { ask(Msg.Deciders(what = "remove", id = r.id)) })
            "name" -> DeciderEntry(Wording.Common.name, name, KeyboardType.Text) { name = it }
            "url" -> DeciderEntry(Wording.Deciders.url, url, KeyboardType.Uri) { url = it }
            else -> ButtonRow(Wording.Deciders.add, Adwaita.ListAdd) {
                ask(Msg.Deciders(what = "add", name = name.trim(), url = url.trim()))
            }
        }
    }
}

@Composable
private fun DeciderRow(d: Msg.Decider, test: () -> Unit, remove: () -> Unit) {
    val c = LocalBaude.current
    ActionRow(d.name ?: d.id.orEmpty(), null, suffix = {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(deciderStatus(d), style = Type.body, color = c.dim, maxLines = 2, overflow = TextOverflow.Ellipsis,
                textAlign = TextAlign.End, modifier = Modifier.widthIn(max = 170.dp))
            IconButton(Adwaita.ViewRefresh, Wording.Deciders.test, onClick = test)
            IconButton(Adwaita.UserTrash, Wording.Common.remove, onClick = remove)
        }
    })
}

/** A row to type in (Adw.EntryRow, as Settings' add rows have it): its title small over what's typed, a pencil after. */
@Composable
private fun DeciderEntry(title: String, value: String, type: KeyboardType, set: (String) -> Unit) {
    val c = LocalBaude.current
    Row(Modifier.fillMaxWidth().heightIn(min = 50.dp).padding(horizontal = 12.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(title, style = Type.caption, color = c.dim)
            BasicTextField(value, set, singleLine = true, textStyle = Type.body.copy(color = c.fg),
                cursorBrush = SolidColor(c.accentBg), modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(keyboardType = type, imeAction = ImeAction.Done), keyboardActions = KeyboardActions())
        }
        Icon(Adwaita.DocumentEdit, null, tint = c.fg, modifier = Modifier.size(16.dp.icon))
    }
}
