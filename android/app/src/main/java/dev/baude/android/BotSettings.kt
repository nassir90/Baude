package dev.baude.android

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import java.util.Base64

/** Make Bot / Bot Settings (Baude's ask_make_bot): a name and a picture for a chat on a host, which then lists it as a
 * Bot. */
@Composable
fun BotSettings(item: Item, done: () -> Unit) {
    val c = LocalBaude.current
    val ctx = LocalContext.current
    val s = item.s
    val h = item.hostId
    val making = s.bot == null
    val contact = Contact(h, s)
    LaunchedEffect(contact.pictureKey) { Mesh.picture(contact) }
    var name by remember { mutableStateOf(s.bot?.name?.ifBlank { null } ?: tidy(s.title.orEmpty()).take(80)) }
    var pick by remember { mutableStateOf<ByteArray?>(null) } // (a picture chosen)
    var cleared by remember { mutableStateOf(false) } // (its picture removed)
    val chosen = pick?.let { b -> android.graphics.BitmapFactory.decodeByteArray(b, 0, b.size)?.asImageBitmap() }
    val shown = if (cleared) null else if (pick != null) chosen else Mesh.pictures[contact.pictureKey]
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
        if (uri != null) {
            val (png, why) = pictureOr(ctx, uri)
            if (png == null) toast(Wording.BotSettings.unreadable(why = why))
            else {
                pick = png
                cleared = false
            }
        }
    }
    fun go() {
        val n = tidy(name)
        if (n.isBlank()) return
        done()
        val picture = pick?.let { Base64.getEncoder().encodeToString(it) } ?: if (cleared && s.bot?.picture != null) "" else null
        Mesh.ask(h, Msg.Bot(session = s.id.orEmpty(), bot = Msg.BotFields(name = n, picture = picture)).json()) { r ->
            if (r.optString("op") == Op.ERROR) toast(Wording.BotSettings.failed(why = Msg.Error.from(r).said.orEmpty()))
        }
    }
    AlertDialog(
        onDismissRequest = done,
        title = { Text(if (making) Wording.BotSettings.makeBot else Wording.BotSettings.settings) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                if (making) Text(Wording.BotSettings.body, style = Type.body, color = c.fg)
                Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                    Avatar(name.ifBlank { Wording.Sidebar.bot }, shown, size = 64.dp.icon)
                }
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp, Alignment.CenterHorizontally)) {
                    Button(Wording.BotSettings.choosePicture) {
                        launcher.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly))
                    }
                    if (shown != null) Button(Wording.BotSettings.removePicture) {
                        pick = null
                        cleared = true
                    }
                }
                NameField(name, { name = it }, Wording.Common.name, ::go)
            }
        },
        confirmButton = {
            Button(if (making) Wording.BotSettings.makeBot else Wording.Common.save, suggested = true, enabled = tidy(name).isNotBlank(), onClick = ::go)
        },
        dismissButton = { Button(Wording.Common.cancel, onClick = done) },
        containerColor = c.dialog,
    )
}
