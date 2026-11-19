package dev.baude.android

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.OpenableColumns
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowUpward
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Dns
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp

// ---------------------------------------------------------------------------------------------------- the Bots --

/** Your Bots, on every host, the last to say something first: as Baude's sidebar lists them, each its picture, its
 * name and what it's on. */
@Composable
fun Contacts(open: (Contact) -> Unit, hosts: () -> Unit) {
    val c = LocalBaude.current
    Mesh.tick.value // (a host's state changed: drawn again)
    val contacts = Mesh.contacts
    Column(Modifier.fillMaxSize().background(c.frame)) {
        Bar("Baude", end = { IconButton(Icons.Filled.Dns, "Hosts", onClick = hosts) })
        val down = Mesh.hosts.mapNotNull { h -> Mesh.conn(h.id)?.takeIf { it.state == "down" || it.state == "key" } }
        if (Mesh.key.value == null || Mesh.hosts.isEmpty()) {
            GreyBanner("Add a host to see its Bots.", onClick = hosts)
        }
        for (conn in down) GreyBanner("Can't connect to ${conn.host.label}: ${conn.why ?: "no answer"}", onClick = hosts)
        if (contacts.isEmpty()) {
            Column(Modifier.fillMaxSize().padding(32.dp), verticalArrangement = Arrangement.Center,
                horizontalAlignment = Alignment.CenterHorizontally) {
                Text("No Bots yet", style = Type.title, color = c.fg)
                Spacer(Modifier.size(6.dp))
                Text("Make a chat a Bot in Baude, and it shows here.", style = Type.body, color = c.dim,
                    textAlign = TextAlign.Center)
            }
            return
        }
        LazyColumn(Modifier.fillMaxSize().padding(horizontal = Gap.inside)) {
            items(contacts, key = { it.hostId + it.id }) { contact ->
                LaunchedEffect(contact.pictureKey) { Mesh.picture(contact) }
                Row(
                    Modifier.fillMaxWidth().heightIn(min = Gap.botRow).clip(RoundedCornerShape(6.dp))
                        .clickable { open(contact) }.padding(horizontal = Gap.sidebarRowH + 4.dp, vertical = Gap.sidebarRowV),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Avatar(contact.name, Mesh.pictures[contact.pictureKey])
                    Spacer(Modifier.width(Gap.avatarGap))
                    Column(Modifier.weight(1f)) {
                        Text(contact.name, style = Type.bold, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        val what = contact.session.optString("title").ifBlank { "" }
                        if (what.isNotEmpty()) Text(what, style = Type.caption, color = c.dim, maxLines = 1,
                            overflow = TextOverflow.Ellipsis)
                    }
                    StatusDot(contact.state, contact.asking)
                }
            }
        }
    }
}

// ------------------------------------------------------------------------------------------------------ a chat --

/** A Bot's chat: what it and you have said, what it did, and a box to write in (files too: pictures and PDFs as
 * Baude sends them, text files in the message). */
@Composable
fun ChatScreen(hostId: String, sid: String, back: () -> Unit) {
    val c = LocalBaude.current
    val ctx = LocalContext.current
    val chat = remember(hostId, sid) { Mesh.openChat(hostId, sid) }
    val contact = Mesh.contact(hostId, sid)
    val name = contact?.name ?: "Bot"
    val host = Mesh.hosts.firstOrNull { it.id == hostId }
    var text by remember { mutableStateOf("") }
    val files = remember { mutableStateListOf<Picked>() }
    val pick = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        uris.forEach { uri -> read(ctx, uri)?.let { files.add(it) } }
    }
    val list = rememberLazyListState()
    LaunchedEffect(chat.lines.size) { if (chat.lines.isNotEmpty()) list.animateScrollToItem(chat.lines.size - 1) }
    Mesh.tick.value
    val conn = Mesh.conn(hostId)
    Column(Modifier.fillMaxSize().background(c.inside).imePadding()) {
        Bar(name, subtitle = host?.label, start = { IconButton(Icons.AutoMirrored.Filled.ArrowBack, "Back", onClick = back) },
            lead = { Avatar(name, contact?.let { Mesh.pictures[it.pictureKey] }) })
        if (conn == null || conn.state != "up") GreyBanner("Can't connect to the host." + (conn?.why?.let { " $it" } ?: ""))
        LazyColumn(Modifier.weight(1f).fillMaxWidth(), state = list,
            contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = Gap.chatH, vertical = Gap.chatV),
            verticalArrangement = Arrangement.spacedBy(Gap.messageGap)) {
            items(chat.lines) { line -> Said(line) }
            if (contact?.state == "working") item { Text("Working…", style = Type.caption, color = c.dim) }
        }
        chat.said.value?.let { Text(it, style = Type.caption, color = c.dim, modifier = Modifier.padding(horizontal = Gap.chatH)) }
        // the composer: rounded 12, a ring of the rule, its files over what's written, + and send beside it
        Column(
            Modifier.fillMaxWidth().navigationBarsPadding().padding(horizontal = 10.dp, vertical = 8.dp)
                .clip(RoundedCornerShape(Gap.composerRadius)).background(c.inside)
                .border(1.dp, c.rule, RoundedCornerShape(Gap.composerRadius)).padding(6.dp),
        ) {
            if (files.isNotEmpty()) Row(Modifier.padding(bottom = 6.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                for (f in files.toList()) Row(
                    Modifier.clip(RoundedCornerShape(8.dp)).background(c.raised).padding(start = 8.dp, end = 2.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(f.name, style = Type.caption, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.widthIn(max = 140.dp))
                    IconButton(Icons.Filled.Close, "Remove") { files.remove(f) }
                }
            }
            Row(verticalAlignment = Alignment.Bottom) {
                IconButton(Icons.Filled.Add, "Attach files") { pick.launch(arrayOf("*/*")) }
                Box(Modifier.weight(1f).padding(horizontal = 6.dp, vertical = 7.dp)) {
                    if (text.isEmpty()) Text("Message $name", style = Type.body, color = c.dim)
                    BasicTextField(text, { text = it }, textStyle = Type.body.copy(color = c.fg),
                        cursorBrush = SolidColor(c.accentBg), modifier = Modifier.fillMaxWidth().heightIn(max = 160.dp))
                }
                val ready = (text.isNotBlank() || files.isNotEmpty()) && !chat.busy.value
                IconButton(Icons.Filled.ArrowUpward, "Send", enabled = ready, tint = if (ready) c.accentFg else c.fg,
                    background = if (ready) c.accentBg else null) {
                    Mesh.send(chat, text, files.toList())
                    text = ""
                    files.clear()
                }
            }
        }
    }
}

@Composable
private fun Said(line: Line) {
    val c = LocalBaude.current
    when (line.kind) {
        "you" -> Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
            SelectionContainer {
                Text(line.text, style = Type.body, color = c.fg, modifier = Modifier.padding(start = 48.dp)
                    .clip(RoundedCornerShape(Gap.bubbleRadius)).background(c.bubble)
                    .padding(horizontal = Gap.bubbleH, vertical = Gap.bubbleV))
            }
        }
        "tool" -> Text(line.text, style = Type.caption, color = c.dim, maxLines = 2, overflow = TextOverflow.Ellipsis,
            modifier = Modifier.fillMaxWidth().card(c))
        else -> SelectionContainer { Prose(line.text) }
    }
}

/** What a Bot said: its text, and its code (``` fenced) in Baude's code blocks. */
@Composable
private fun Prose(text: String) {
    val c = LocalBaude.current
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        text.split("```").forEachIndexed { i, part ->
            if (i % 2 == 1) {
                val code = part.substringAfter("\n", part).trimEnd()
                Text(code, style = Type.mono, color = c.fg, modifier = Modifier.fillMaxWidth()
                    .clip(RoundedCornerShape(Gap.codeRadius)).background(c.raised).padding(horizontal = 8.dp, vertical = 6.dp))
            } else if (part.isNotBlank()) {
                for (block in part.trim().split(Regex("\n\\s*\n"))) Markdown(block)
            }
        }
    }
}

/** A paragraph of Markdown as Baude's chat draws it: headings (.md-h1 1.44em, .md-h2 1.2em), bullets, **bold**,
 * *italic* and `code` (monospace, on the raised fill). */
@Composable
private fun Markdown(block: String) {
    val c = LocalBaude.current
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        for (raw in block.lines()) {
            val h = Regex("^(#{1,6})\\s+(.*)$").find(raw)
            val bullet = Regex("^\\s*([-*+]|\\d+\\.)\\s+(.*)$").find(raw)
            val (text, style) = when {
                h != null -> h.groupValues[2] to Type.body.copy(
                    fontSize = Type.body.fontSize * (if (h.groupValues[1].length == 1) 1.44f else if (h.groupValues[1].length == 2) 1.2f else 1f),
                    fontWeight = androidx.compose.ui.text.font.FontWeight.Bold)
                bullet != null -> ((if (bullet.groupValues[1].first().isDigit()) bullet.groupValues[1] + " " else "•  ") +
                    bullet.groupValues[2]) to Type.body
                else -> raw to Type.body
            }
            Text(inline(text, c), style = style, color = c.fg)
        }
    }
}

private fun inline(text: String, c: BaudeColors) = androidx.compose.ui.text.buildAnnotatedString {
    val token = Regex("""`([^`]+)`|\*\*([^*]+)\*\*|__([^_]+)__|\*([^*\s][^*]*)\*|_([^_\s][^_]*)_""")
    var at = 0
    for (m in token.findAll(text)) {
        append(text.substring(at, m.range.first))
        val (v, style) = when {
            m.groups[1] != null -> m.groupValues[1] to androidx.compose.ui.text.SpanStyle(
                fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace, background = c.raised)
            m.groups[2] != null -> m.groupValues[2] to androidx.compose.ui.text.SpanStyle(
                fontWeight = androidx.compose.ui.text.font.FontWeight.Bold)
            m.groups[3] != null -> m.groupValues[3] to androidx.compose.ui.text.SpanStyle(
                fontWeight = androidx.compose.ui.text.font.FontWeight.Bold)
            else -> (m.groups[4] ?: m.groups[5])!!.value to androidx.compose.ui.text.SpanStyle(
                fontStyle = androidx.compose.ui.text.font.FontStyle.Italic)
        }
        withStyle(style) { append(v) }
        at = m.range.last + 1
    }
    append(text.substring(at))
}

private fun read(ctx: Context, uri: Uri): Picked? {
    val r = ctx.contentResolver
    var name = "file"
    r.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { cur ->
        if (cur.moveToFirst()) {
            name = cur.getString(0) ?: name
            if (!cur.isNull(1) && cur.getLong(1) > MAX_FILE) {
                Toast.makeText(ctx, "$name is too big to send (20 MB at most)", Toast.LENGTH_SHORT).show()
                return null
            }
        }
    }
    val mime = r.getType(uri) ?: "application/octet-stream"
    val bytes = r.openInputStream(uri)?.use { it.readBytes() } ?: return null
    val text = mime.startsWith("text/") || mime in setOf("application/json", "application/xml", "application/x-yaml")
    if (mime !in Mesh.IMAGE_TYPES && mime != "application/pdf" && !text) {
        Toast.makeText(ctx, "$name can't be sent: pictures, PDFs and text files can", Toast.LENGTH_SHORT).show()
        return null
    }
    return Picked(name, mime, bytes)
}

private const val MAX_FILE = 20L * 1024 * 1024

// ----------------------------------------------------------------------------------------------------- hosts --

/** This phone's key, and your hosts: added as user@host (a host's Tailscale name works, as ssh's), each one's key
 * trusted once you've compared it. */
@Composable
fun HostsScreen(back: () -> Unit) {
    val c = LocalBaude.current
    val ctx = LocalContext.current
    Mesh.tick.value
    var adding by remember { mutableStateOf(false) }
    var asking by remember { mutableStateOf<Connection?>(null) }
    var removing by remember { mutableStateOf<Host?>(null) }
    var renewing by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxSize().background(c.frame)) {
        Bar("Hosts", start = { IconButton(Icons.AutoMirrored.Filled.ArrowBack, "Back", onClick = back) },
            end = { IconButton(Icons.Filled.Add, "Add Host") { adding = true } })
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            val key = Mesh.key.value
            Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.inside).padding(12.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("This phone's key", style = Type.title, color = c.fg)
                if (key == null) {
                    Text("Make a key for this phone, then put its public half in each host's ~/.ssh/authorized_keys.",
                        style = Type.body, color = c.dim)
                    Button("Make Key", suggested = true) { Mesh.makeKey() }
                } else {
                    Text("Put this line in each host's ~/.ssh/authorized_keys.", style = Type.body, color = c.dim)
                    SelectionContainer {
                        Text(key.line, style = Type.mono, color = c.fg, modifier = Modifier.fillMaxWidth()
                            .clip(RoundedCornerShape(Gap.codeRadius)).background(c.raised).padding(8.dp))
                    }
                    Text(key.fingerprint, style = Type.caption, color = c.dim)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button("Copy", suggested = true) {
                            ctx.getSystemService(ClipboardManager::class.java)
                                .setPrimaryClip(ClipData.newPlainText("Baude's key", key.line))
                            Toast.makeText(ctx, "Copied", Toast.LENGTH_SHORT).show()
                        }
                        Button("Share") {
                            ctx.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType("text/plain")
                                .putExtra(Intent.EXTRA_TEXT, key.line), "Share the key"))
                        }
                        Button("New Key") { renewing = true }
                    }
                }
            }
            if (Mesh.hosts.isEmpty()) Text("No hosts yet. Add one as user@host: over Tailscale, its Tailscale name.",
                style = Type.body, color = c.dim, modifier = Modifier.padding(horizontal = 4.dp))
            for (h in Mesh.hosts.toList()) {
                val conn = Mesh.conn(h.id)
                val (state, colour) = when (conn?.state) {
                    "up" -> "Connected" to c.success
                    "connecting" -> "Connecting…" to c.dim
                    "key" -> "Its key isn't trusted yet: tap to check it" to c.warning
                    "down" -> "Can't connect: ${conn.why ?: "no answer"}" to c.destructive
                    else -> (if (key == null) "Make this phone's key first" else "Not connected") to c.dim
                }
                Row(
                    Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.inside)
                        .clickable { if (conn?.state == "key") asking = conn else Mesh.connect(h) }.padding(12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(Modifier.weight(1f)) {
                        Text(h.label, style = Type.bold, color = c.fg)
                        Text(h.dest, style = Type.caption, color = c.dim)
                        Text(state, style = Type.caption, color = colour)
                    }
                    IconButton(Icons.Filled.Delete, "Remove") { removing = h }
                }
            }
        }
    }
    if (adding) AddHost { adding = false }
    asking?.let { conn ->
        val shown = conn.shown
        if (shown == null) asking = null else AlertDialog(
            onDismissRequest = { asking = null },
            title = { Text(if (shown.changed) "${conn.host.label}'s key changed" else "Trust ${conn.host.label}?") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(if (shown.changed) "It isn't the key it showed before. Trust it only if you know why it changed."
                        else "Compare its fingerprint with the host's own, from ssh-keygen -lf on its host key.")
                    Text(shown.type, style = Type.caption, color = c.dim)
                    SelectionContainer { Text(shown.fingerprint, style = Type.mono) }
                }
            },
            confirmButton = { Button("Trust", suggested = !shown.changed, destructive = shown.changed) {
                Mesh.trust(conn.host, shown); asking = null } },
            dismissButton = { Button("Cancel") { asking = null } },
            containerColor = c.dialog,
        )
    }
    removing?.let { h ->
        AlertDialog(onDismissRequest = { removing = null }, title = { Text("Remove ${h.label}?") },
            text = { Text("Its Bots aren't listed here any more. Nothing on the host changes.") },
            confirmButton = { Button("Remove", destructive = true) { Mesh.removeHost(h); removing = null } },
            dismissButton = { Button("Cancel") { removing = null } }, containerColor = c.dialog)
    }
    if (renewing) AlertDialog(onDismissRequest = { renewing = false }, title = { Text("Make a new key?") },
        text = { Text("Hosts that have the current key need the new one in their authorized_keys.") },
        confirmButton = { Button("Make Key", destructive = true) { Mesh.makeKey(); renewing = false } },
        dismissButton = { Button("Cancel") { renewing = false } }, containerColor = c.dialog)
}

@Composable
private fun AddHost(done: () -> Unit) {
    val c = LocalBaude.current
    var dest by remember { mutableStateOf("") }
    var name by remember { mutableStateOf("") }
    var command by remember { mutableStateOf("baude_headless.py") }
    val host = Host.parse(dest, name, command)
    AlertDialog(
        onDismissRequest = done,
        title = { Text("Add Host") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Field(dest, { dest = it }, "user@host (or user@host:port)")
                Field(name, { name = it }, "Name (optional)")
                Field(command, { command = it }, "Its Baude, on its PATH")
                Text("Over Tailscale, use the host's Tailscale name or address.", style = Type.caption, color = c.dim)
            }
        },
        confirmButton = { Button("Add", suggested = true) { if (host != null) { Mesh.addHost(host); done() } } },
        dismissButton = { Button("Cancel", onClick = done) },
        containerColor = c.dialog,
    )
}

@Composable
private fun Field(value: String, set: (String) -> Unit, hint: String) {
    val c = LocalBaude.current
    Box(Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).background(c.fg.copy(alpha = 0.08f))
        .padding(horizontal = 10.dp, vertical = 9.dp)) {
        if (value.isEmpty()) Text(hint, style = Type.body, color = c.dim)
        BasicTextField(value, set, singleLine = true, textStyle = Type.body.copy(color = c.fg),
            cursorBrush = SolidColor(c.accentBg), modifier = Modifier.fillMaxWidth())
    }
}
