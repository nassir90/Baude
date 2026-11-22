package dev.baude.android

import android.content.ClipData
import android.content.ClipboardManager
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp

/** This phone as a host: its name in the mesh, its key, its agents, and its trash. */
@Composable
fun PhoneScreen(back: () -> Unit, open: (String) -> Unit) {
    val c = LocalBaude.current
    Mesh.tick.value // (a host's state, or the agents', changed: drawn again)
    var deleting by remember { mutableStateOf<Msg.Session?>(null) }
    var renaming by remember { mutableStateOf(false) }
    val ctx = androidx.compose.ui.platform.LocalContext.current
    var picturing by remember { mutableStateOf<Msg.Session?>(null) } // (the agent whose picture's being picked)
    val pick = androidx.activity.compose.rememberLauncherForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.GetContent()) { uri ->
        val s = picturing ?: return@rememberLauncherForActivityResult
        picturing = null
        if (uri != null) kotlin.concurrent.thread(name = "picture", isDaemon = true) { picture(ctx, uri)?.let { setPicture(s, it) } }
    }
    Column(Modifier.fillMaxSize().background(c.frame)) {
        Bar(Wording.ThisPhone.title, start = { IconButton(Adwaita.GoPrevious, Wording.Browser.back, onClick = back) })
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).navigationBarsPadding().padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Panel(rows = true) {
                ActionRow(Wording.Common.name, null, onClick = { renaming = true }, suffix = {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text(PhoneHost.name, style = Type.body, color = c.dim, maxLines = 1, overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.widthIn(max = 220.dp))
                        Icon(Adwaita.GoNext, null, tint = c.dim, modifier = Modifier.size(16.dp.icon))
                    }
                })
            }

            PanelHeading(Wording.PhoneKeys.heading)
            KeyMaterial()

            val far = startedElsewhere() // (an agent on another host started it: under that one in Home's list only)
            val agents = Mesh.sessions["phone"].orEmpty().filter { it.archived != true && "phone/${it.id.orEmpty()}" !in far }
            if (agents.isNotEmpty()) {
                PanelHeading(Wording.ThisPhone.agents)
                Panel(rows = true) {
                    agents.forEachIndexed { i, s ->
                        if (i > 0) Rule()
                        val name = s.bot?.name?.ifBlank { null } ?: s.title?.ifBlank { null } // (a Bot: its name alone)
                            ?: modelSaid(s.model.orEmpty()).ifBlank { Wording.Common.agent }
                        Row(Modifier.fillMaxWidth().clickable { open(s.id.orEmpty()) }.padding(start = 12.dp, top = 8.dp, bottom = 8.dp, end = 4.dp),
                            verticalAlignment = Alignment.CenterVertically) {
                            val contact = Mesh.contact("phone", s.id.orEmpty())
                            androidx.compose.runtime.LaunchedEffect(contact?.pictureKey) { contact?.let { Mesh.picture(it) } }
                            Box(Modifier.clip(CircleShape).clickable(enabled = s.bot != null) { // (a Bot's picture: picked)
                                picturing = s
                                pick.launch("image/*")
                            }) { Avatar(name, contact?.let { Mesh.pictures[it.pictureKey] }) }
                            Spacer(Modifier.width(Gap.avatarGap))
                            Text(name, style = Type.bold, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis,
                                modifier = Modifier.weight(1f))
                            StatusDot(s.state.orEmpty(), !s.asks.isNullOrEmpty())
                            IconButton(Adwaita.UserTrash, Wording.Trash.moveDots) { deleting = s }
                        }
                    }
                }
            }

            val bin = PhoneHost.trashed()
            if (bin.isNotEmpty()) {
                PanelHeading(Wording.ThisPhone.trash)
                Panel(rows = true) {
                    bin.forEachIndexed { i, m ->
                        if (i > 0) Rule()
                        Row(Modifier.fillMaxWidth().padding(start = 12.dp, top = 6.dp, bottom = 6.dp, end = 8.dp),
                            verticalAlignment = Alignment.CenterVertically) {
                            Text(m.optJSONObject("bot")?.optString("name")?.ifBlank { null } // (a Bot: its name alone)
                                ?: (if (m.isNull("title")) "" else m.optString("title")).ifBlank { modelSaid(m.optString("model")) }
                                    .ifBlank { Wording.Common.agent },
                                style = Type.bold, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                            Button(Wording.ThisPhone.restore) { PhoneHost.putBack(m.optString("id")) }
                        }
                    }
                }
            }
        }
    }
    if (renaming) RenamePhone { renaming = false }
    deleting?.let { s ->
        val name = s.bot?.name?.ifBlank { null } ?: Wording.Trash.thisSession
        AlertDialog(onDismissRequest = { deleting = null }, title = { Text(Wording.Trash.heading(title = name)) },
            text = { Text(Wording.ThisPhone.trashBody) },
            confirmButton = {
                Button(Wording.Trash.move, destructive = true) {
                    Mesh.conn("phone")?.request(Msg.Delete(session = s.id.orEmpty()).json()) {}
                    deleting = null
                }
            },
            dismissButton = { Button(Wording.Common.cancel) { deleting = null } }, containerColor = c.dialog)
    }
}

/** This phone's Name renamed (mesh_rename's dialog, for its own name): the name the mesh knows it by; left empty,
 * the one Android gives it. */
@Composable
private fun RenamePhone(done: () -> Unit) {
    var text by remember { mutableStateOf(PhoneHost.name) }
    fun go() {
        PhoneHost.rename(text)
        done()
    }
    AlertDialog(
        onDismissRequest = done,
        title = { Text(Wording.MeshVersions.renameHeading(host = PhoneHost.name)) },
        text = { NameField(text, { text = it }, PhoneHost.deviceName(), ::go) },
        confirmButton = { Button(Wording.Common.rename, suggested = true, onClick = ::go) },
        dismissButton = { Button(Wording.Common.cancel, onClick = done) },
        containerColor = LocalBaude.current.dialog,
    )
}

/** This phone's own ssh key (it has no ssh agent): its authorized_keys line, to copy, and New Key; Make Key while
 * it has none. */
@Composable
private fun KeyMaterial() {
    val c = LocalBaude.current
    val ctx = LocalContext.current
    var renewing by remember { mutableStateOf(false) }
    val key = Mesh.key.value
    val error = Mesh.keyError.value
    Panel {
        if (key == null && error != null) {
            Text(error, style = Type.body, color = c.destructive)
            Row { Button(Wording.Common.tryAgain) { Mesh.readKey() } }
        } else if (key == null) {
            if (Mesh.making.value) Text(Wording.PhoneKeys.making, style = Type.body, color = c.dim)
            else Row { Button(Wording.PhoneKeys.make, suggested = true) { Mesh.makeKey() } }
        } else {
            SelectionContainer {
                Text(key.line, style = Type.mono, color = c.fg, modifier = Modifier.fillMaxWidth()
                    .clip(RoundedCornerShape(Gap.codeRadius)).background(c.raised).padding(8.dp))
            }
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Button(Wording.Common.copy) {
                    ctx.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("Baude's key", key.line))
                    toast(Wording.Report.copied)
                }
                Button(Wording.PhoneKeys.newKey) { renewing = true }
            }
        }
    }
    if (renewing) AlertDialog(onDismissRequest = { renewing = false }, title = { Text(Wording.PhoneKeys.newHeading) },
        text = { Text(Wording.PhoneKeys.newBody) },
        confirmButton = { Button(Wording.PhoneKeys.make, destructive = true) { Mesh.makeKey(); renewing = false } },
        dismissButton = { Button(Wording.Common.cancel) { renewing = false } }, containerColor = c.dialog)
}

/** A picture picked, as a Bot's is sent: a PNG, 256 across at most; or null and why not ("" if it isn't an image). */
fun pictureOr(ctx: android.content.Context, uri: android.net.Uri): Pair<ByteArray?, String> = try {
    val bmp = ctx.contentResolver.openInputStream(uri)?.use { android.graphics.BitmapFactory.decodeStream(it) }
    if (bmp == null) null to "" else {
        val k = minOf(1f, 256f / maxOf(bmp.width, bmp.height))
        val small = android.graphics.Bitmap.createScaledBitmap(bmp, maxOf(1, (bmp.width * k).toInt()), maxOf(1, (bmp.height * k).toInt()), true)
        java.io.ByteArrayOutputStream().also { o -> small.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, o) }.toByteArray() to ""
    }
} catch (e: Exception) {
    null to (e.message ?: e.javaClass.simpleName)
}

fun picture(ctx: android.content.Context, uri: android.net.Uri): ByteArray? = pictureOr(ctx, uri).first

/** A Bot's picture set, as Baude's Bot Settings sets one (bot, its picture with it). */
private fun setPicture(s: Msg.Session, png: ByteArray) {
    val bot = s.bot ?: return
    Mesh.conn("phone")?.request(Msg.Bot(session = s.id.orEmpty(), bot = Msg.BotFields(
        name = bot.name.orEmpty(), picture = java.util.Base64.getEncoder().encodeToString(png))).json()) {}
}

/** A model's value as the agents list says it, by its name in its provider's API. */
private fun modelSaid(value: String) = value.substringAfterLast("/")

@Composable
private fun PanelHeading(text: String) {
    val c = LocalBaude.current
    Text(text, style = Type.folder, color = c.dim, modifier = Modifier.padding(start = 4.dp, top = 4.dp))
}

/** A rounded group on the frame, as Hosts has them (rows: its rows each pad themselves, with rules between). */
@Composable
private fun Panel(rows: Boolean = false, content: @Composable ColumnScope.() -> Unit) {
    val c = LocalBaude.current
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.inside)
        .then(if (rows) Modifier else Modifier.padding(12.dp)),
        verticalArrangement = if (rows) Arrangement.Top else Arrangement.spacedBy(8.dp), content = content)
}

@Composable
private fun Rule() {
    val c = LocalBaude.current
    Box(Modifier.fillMaxWidth().padding(start = 12.dp).heightIn(min = 1.dp).background(c.rule))
}
