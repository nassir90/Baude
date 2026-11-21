package dev.baude.android

import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
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
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp

/** A short note, as Baude's toast. */
fun toast(text: String) {
    Toast.makeText(Mesh.app, text, Toast.LENGTH_SHORT).show()
}

/** A name with its spaces tidied (Baude's " ".join(text.split())). */
fun tidy(text: String): String = text.split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")

/** A text entry that takes focus when it opens, and confirms on Done (Baude's Gtk.Entry with activates_default). */
@Composable
fun NameField(value: String, set: (String) -> Unit, hint: String, onDone: () -> Unit, focus: Boolean = true) {
    val c = LocalBaude.current
    val focusing = remember { FocusRequester() }
    LaunchedEffect(Unit) { if (focus) focusing.requestFocus() }
    Box(Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).background(c.fg.copy(alpha = 0.08f))
        .padding(horizontal = 10.dp, vertical = 9.dp)) {
        if (value.isEmpty()) Text(hint, style = Type.body, color = c.dim)
        BasicTextField(value, set, singleLine = true, textStyle = Type.body.copy(color = c.fg),
            cursorBrush = SolidColor(c.accentBg), keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
            keyboardActions = KeyboardActions(onDone = { onDone() }), modifier = Modifier.fillMaxWidth().focusRequester(focusing))
    }
}

/** Rename session / Rename Bot (Baude's ask_rename): the name asked for, then sent to the session's host. */
@Composable
fun RenameSession(item: Item, done: () -> Unit) {
    val c = LocalBaude.current
    val s = item.s
    val h = item.hostId
    val now = s.bot?.name?.ifBlank { null } ?: s.title.orEmpty()
    var text by remember { mutableStateOf(now) }
    fun go() {
        val name = tidy(text).take(80)
        done()
        if (name.isBlank() || name == now) return
        val sid = s.id.orEmpty()
        if (s.bot != null) Mesh.ask(h, Msg.Bot(session = sid, bot = Msg.BotFields(name = name)).json()) { r ->
            if (r.optString("op") == Op.ERROR) toast(Wording.BotSettings.failed(why = Msg.Error.from(r).said.orEmpty()))
        } else Mesh.ask(h, Msg.Rename(session = sid, title = name).json()) { r ->
            if (r.optString("op") != Op.RENAMED) toast(Wording.Sidebar.hostDidntRename(host = Mesh.label(h),
                why = Msg.Error.from(r).said.orEmpty()))
        }
    }
    AlertDialog(
        onDismissRequest = done,
        title = { Text(if (s.bot != null) Wording.Sidebar.renameBot else Wording.Sidebar.renameSession) },
        text = { NameField(text, { text = it }, "", ::go) },
        confirmButton = { Button(Wording.Common.rename, suggested = true, onClick = ::go) },
        dismissButton = { Button(Wording.Common.cancel, onClick = done) },
        containerColor = c.dialog,
    )
}

/** Move to Trash (Baude's ask_trash): where its transcript goes, then asked of its host. */
@Composable
fun MoveToTrash(item: Item, done: () -> Unit) {
    val c = LocalBaude.current
    val s = item.s
    val h = item.hostId
    val where = if (h == "phone") Wording.Trash.yours else Wording.Trash.onHost(host = Mesh.label(h))
    val body = Wording.Trash.body(where = where) + if (program(s.model) == "codex") " " + Wording.Trash.codexCopy else ""
    AlertDialog(
        onDismissRequest = done,
        title = { Text(Wording.Trash.heading(title = s.title?.ifBlank { null } ?: Wording.Trash.thisSession)) },
        text = { Text(body, style = Type.body, color = c.fg) },
        confirmButton = {
            Button(Wording.Trash.move, destructive = true) {
                done()
                Mesh.ask(h, Msg.Delete(session = s.id.orEmpty()).json()) { r ->
                    if (r.optString("op") != "deleted") toast(Wording.Trash.hostRefused(host = Mesh.label(h), op = "delete",
                        why = Msg.Error.from(r).said.orEmpty()))
                }
            }
        },
        dismissButton = { Button(Wording.Common.cancel, onClick = done) },
        containerColor = c.dialog,
    )
}

/** New group / Rename group (Baude's ask_group_name): a name asked for; a new group's made, and the chat moveKey (if
 * given) put in it, or old's renamed. */
@Composable
fun GroupName(new: Boolean, old: String = "", moveKey: String? = null, done: () -> Unit) {
    val c = LocalBaude.current
    var text by remember { mutableStateOf(if (new) "" else old) }
    fun go() {
        val name = tidy(text)
        if (name.isBlank()) return
        done()
        if (new) {
            Groups.make(name)
            moveKey?.let { Groups.move(it, name) }
        } else if (name != old) Groups.rename(old, name)
    }
    AlertDialog(
        onDismissRequest = done,
        title = { Text(if (new) Wording.NewSession.newGroup else Wording.NewSession.renameGroup) },
        text = { NameField(text, { text = it }, "", ::go) },
        confirmButton = { Button(if (new) Wording.Common.create else Wording.Common.rename, suggested = true, onClick = ::go) },
        dismissButton = { Button(Wording.Common.cancel, onClick = done) },
        containerColor = c.dialog,
    )
}
