package dev.baude.android

import android.os.Handler
import android.os.Looper
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
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
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import org.json.JSONObject

/** This phone as a host: its agents, whether it's in the mesh, the models the mesh serves them, and what they
 * reach. */
@Composable
fun PhoneScreen(back: () -> Unit, open: (String) -> Unit, make: () -> Unit) {
    val c = LocalBaude.current
    Mesh.tick.value // (a host's state, or the agents', changed: drawn again)
    var deleting by remember { mutableStateOf<JSONObject?>(null) }
    val ctx = androidx.compose.ui.platform.LocalContext.current
    var picturing by remember { mutableStateOf<JSONObject?>(null) } // (the agent whose picture's being picked)
    val pick = androidx.activity.compose.rememberLauncherForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.GetContent()) { uri ->
        val s = picturing ?: return@rememberLauncherForActivityResult
        picturing = null
        if (uri != null) kotlin.concurrent.thread(name = "picture", isDaemon = true) { picture(ctx, uri)?.let { setPicture(s, it) } }
    }
    Column(Modifier.fillMaxSize().background(c.frame)) {
        Bar("This phone", start = { IconButton(Icons.AutoMirrored.Filled.ArrowBack, "Back", onClick = back) },
            end = { IconButton(Icons.Filled.Add, "New Agent", onClick = make) })
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).navigationBarsPadding().padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Panel {
                Text(PhoneHost.name, style = Type.title, color = c.fg)
                val (said, colour) = when (PhoneLink.state.value) {
                    "linked" -> "In the mesh, through ${PhoneLink.via.value ?: "your host"}" to c.success
                    "linking" -> "Joining the mesh through ${PhoneLink.via.value ?: "your host"}…" to c.dim
                    else -> (if (Mesh.hosts.isEmpty()) "Not in the mesh: add a host first"
                        else "Not in the mesh" + (PhoneLink.why.value?.let { ": $it" } ?: ": none of your hosts is connected")) to c.dim
                }
                Text(said, style = Type.caption, color = colour)
                Text("Agents made here run on this phone: their chats, their tools and their files are here. Their " +
                    "models run on a machine of the mesh that serves them.", style = Type.body, color = c.dim)
            }

            Heading("Agents")
            val agents = Mesh.sessions["phone"].orEmpty().filter { !it.optBoolean("archived") }
            Panel(rows = true) {
                if (agents.isEmpty()) Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("No agents on this phone yet.", style = Type.body, color = c.dim)
                    Button("New Agent", suggested = Inference.found().isNotEmpty(), onClick = make)
                }
                agents.forEachIndexed { i, s ->
                    if (i > 0) Rule()
                    val name = s.optJSONObject("bot")?.optString("name")?.ifBlank { null } ?: "Agent"
                    Row(Modifier.fillMaxWidth().clickable { open(s.optString("id")) }.padding(start = 12.dp, top = 8.dp, bottom = 8.dp, end = 4.dp),
                        verticalAlignment = Alignment.CenterVertically) {
                        val contact = Mesh.contact("phone", s.optString("id"))
                        androidx.compose.runtime.LaunchedEffect(contact?.pictureKey) { contact?.let { Mesh.picture(it) } }
                        Box(Modifier.clip(CircleShape).clickable(enabled = !s.isNull("bot")) { // (a Bot's picture: picked)
                            picturing = s
                            pick.launch("image/*")
                        }) { Avatar(name, contact?.let { Mesh.pictures[it.pictureKey] }) }
                        Spacer(Modifier.width(Gap.avatarGap))
                        Column(Modifier.weight(1f)) {
                            Text(name, style = Type.bold, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            val what = (if (s.isNull("title")) "" else s.optString("title")).ifBlank { modelSaid(s.optString("model")) }
                            Text(what, style = Type.caption, color = c.dim, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        }
                        StatusDot(s.optString("state"), (s.optJSONArray("asks")?.length() ?: 0) > 0)
                        IconButton(Icons.Filled.Delete, "Delete") { deleting = s }
                    }
                }
            }

            Heading("Models")
            Text("What the mesh's machines serve over an API. A model call goes through the mesh to the machine that " +
                "has the model, and runs there as that machine's own settings say.",
                style = Type.caption, color = c.dim, modifier = Modifier.padding(horizontal = 4.dp))
            val served = Inference.found().groupBy { it.key }
            Panel(rows = true) {
                if (served.isEmpty()) Text("No machine in reach serves a model over an API (Ollama, or an " +
                    "OpenAI-compatible server).", style = Type.body, color = c.dim, modifier = Modifier.padding(12.dp))
                served.values.forEachIndexed { i, models ->
                    if (i > 0) Rule()
                    val first = models.first()
                    Column(Modifier.fillMaxWidth().padding(12.dp)) {
                        Text("${first.providerName} on ${first.memberName}", style = Type.bold, color = c.fg)
                        Text(models.joinToString(", ") { it.display }, style = Type.caption, color = c.dim,
                            maxLines = 2, overflow = TextOverflow.Ellipsis)
                    }
                }
            }

            val bin = PhoneHost.trashed()
            if (bin.isNotEmpty()) {
                Heading("Trash")
                Panel(rows = true) {
                    bin.forEachIndexed { i, m ->
                        if (i > 0) Rule()
                        Row(Modifier.fillMaxWidth().padding(start = 12.dp, top = 6.dp, bottom = 6.dp, end = 8.dp),
                            verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text(m.optJSONObject("bot")?.optString("name")?.ifBlank { null } ?: "Agent", style = Type.bold,
                                    color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                Text((if (m.isNull("title")) "" else m.optString("title")).ifBlank { modelSaid(m.optString("model")) },
                                    style = Type.caption, color = c.dim, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            }
                            Button("Put Back") { PhoneHost.putBack(m.optString("id")) }
                        }
                    }
                }
            }

            Heading("What they reach")
            Panel {
                Text("Files in each agent's own folder on this phone; questions to you; and the mesh's hosts and " +
                    "chats, through this phone's connections to them. Nothing else of the phone's. What it asks you " +
                    "first is as its permission mode says, as in Baude.", style = Type.body, color = c.dim)
            }
        }
    }
    deleting?.let { s ->
        val name = s.optJSONObject("bot")?.optString("name")?.ifBlank { null } ?: "this agent"
        AlertDialog(onDismissRequest = { deleting = null }, title = { Text("Move $name to the Trash?") },
            text = { Text("Its chat, its memory and the files in its folder go to this phone's trash, under Trash here, " +
                "where you can put it back.") },
            confirmButton = {
                Button("Move to Trash", destructive = true) {
                    Mesh.conn("phone")?.request(JSONObject().put("op", "delete").put("session", s.optString("id"))) {}
                    deleting = null
                }
            },
            dismissButton = { Button("Cancel") { deleting = null } }, containerColor = c.dialog)
    }
}

/** A picture picked, as a Bot's is sent: a PNG, 256 across at most (null if it can't be read). */
private fun picture(ctx: android.content.Context, uri: android.net.Uri): ByteArray? = try {
    val bmp = ctx.contentResolver.openInputStream(uri)?.use { android.graphics.BitmapFactory.decodeStream(it) }
    bmp?.let {
        val k = minOf(1f, 256f / maxOf(it.width, it.height))
        val small = android.graphics.Bitmap.createScaledBitmap(it, maxOf(1, (it.width * k).toInt()), maxOf(1, (it.height * k).toInt()), true)
        java.io.ByteArrayOutputStream().also { o -> small.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, o) }.toByteArray()
    }
} catch (_: Exception) {
    null
}

/** A Bot's picture set, as Baude's Bot Settings sets one (bot, its picture with it). */
private fun setPicture(s: JSONObject, png: ByteArray) {
    val bot = s.optJSONObject("bot") ?: return
    Mesh.conn("phone")?.request(JSONObject().put("op", "bot").put("session", s.optString("id")).put("bot", JSONObject()
        .put("name", bot.optString("name")).put("picture", java.util.Base64.getEncoder().encodeToString(png)))) {}
}

/** A phone agent's permission mode, in its chat's bar: tapped, the modes to pick from, as Baude's menu has them
 * (set_permission_mode). */
@Composable
fun PhoneMode(sid: String) {
    val c = LocalBaude.current
    Mesh.tick.value
    val mode = Mesh.sessions["phone"].orEmpty().firstOrNull { it.optString("id") == sid }?.optString("mode") ?: return
    var open by remember { mutableStateOf(false) }
    Box {
        Text(PhoneTools.MODES.firstOrNull { it.first == mode }?.second ?: mode, style = Type.caption, color = c.fg,
            modifier = Modifier.clip(RoundedCornerShape(8.dp)).background(c.raised).clickable { open = true }
                .padding(horizontal = 10.dp, vertical = 6.dp))
        androidx.compose.material3.DropdownMenu(open, { open = false }, containerColor = c.dialog) {
            for ((value, label) in PhoneTools.MODES) androidx.compose.material3.DropdownMenuItem(
                text = { Text(label, style = Type.body, color = c.fg) },
                onClick = {
                    open = false
                    Mesh.conn("phone")?.send(JSONObject().put("op", "input").put("session", sid).put("msg", JSONObject()
                        .put("type", "control_request").put("request_id", java.util.UUID.randomUUID().toString())
                        .put("request", JSONObject().put("subtype", "set_permission_mode").put("mode", value))))
                },
                leadingIcon = if (value == mode) ({ androidx.compose.material3.Icon(Icons.Filled.Check, null, tint = c.accent) }) else null)
        }
    }
}

/** A new agent on this phone: its name, the model that serves it (of those the mesh serves), and what it's to do
 * first. */
@Composable
fun NewAgentScreen(back: () -> Unit, made: (String) -> Unit) {
    val c = LocalBaude.current
    BackHandler(onBack = back)
    Mesh.tick.value
    var name by rememberSaveable { mutableStateOf("") }
    var model by rememberSaveable { mutableStateOf<String?>(null) }
    var prompt by rememberSaveable { mutableStateOf("") }
    var mode by rememberSaveable { mutableStateOf("default") }
    var said by remember { mutableStateOf<String?>(null) }
    var making by remember { mutableStateOf(false) }
    val models = Inference.found()
    if (model == null || models.none { it.value == model }) model = models.firstOrNull()?.value
    Column(Modifier.fillMaxSize().background(c.frame).imePadding()) {
        Bar("New Agent", start = { IconButton(Icons.AutoMirrored.Filled.ArrowBack, "Back", onClick = back) })
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).navigationBarsPadding().padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Panel {
                Text("Its name", style = Type.bold, color = c.fg)
                Entry(name, { name = it }, "Juniper, say")
                Text("An agent here is a Bot: its name and its memory (a file in its folder) last.",
                    style = Type.caption, color = c.dim)
            }
            Heading("Its model")
            Panel(rows = true) {
                if (models.isEmpty()) Text("No machine in reach serves a model over an API yet (Ollama, or an " +
                    "OpenAI-compatible server).",
                    style = Type.body, color = c.dim, modifier = Modifier.padding(12.dp))
                models.forEachIndexed { i, m ->
                    if (i > 0) Rule()
                    Pick(model == m.value, m.display, "${m.providerName} on ${m.memberName}") { model = m.value }
                }
            }
            Heading("Its permission mode")
            Panel(rows = true) {
                PhoneTools.MODES.forEachIndexed { i, (value, label) ->
                    if (i > 0) Rule()
                    Pick(mode == value, label, MODE_SAID[value].orEmpty()) { mode = value }
                }
            }
            Panel {
                Text("What it's to do first", style = Type.bold, color = c.fg)
                Entry(prompt, { prompt = it }, "Optional", lines = 5)
            }
            said?.let { Text(it, style = Type.caption, color = c.destructive, modifier = Modifier.padding(horizontal = 4.dp)) }
            val ready = name.isNotBlank() && model != null && !making
            Button(if (making) "Making…" else "Make Agent", suggested = ready) {
                if (!ready) return@Button
                making = true
                said = null
                val main = Handler(Looper.getMainLooper())
                Mesh.conn("phone")?.request(JSONObject().put("op", "start").put("model", model).put("mode", mode)
                    .put("bot", JSONObject().put("name", name.trim())).put("prompt", prompt.trim())) { r ->
                    main.post {
                        making = false
                        if (r.optString("op") == "started") made(r.optJSONObject("session")?.optString("id") ?: "")
                        else said = r.optString("error", "It couldn't be made")
                    }
                }
            }
        }
    }
}

/** What each permission mode lets an agent here do without asking you (reads, and a Bot's mesh tools, always). */
private val MODE_SAID = mapOf("default" to "Asks before it changes a file",
    "acceptEdits" to "Changes files in its folder without asking", "plan" to "Looks only, then puts its plan to you",
    "auto" to "Does what it needs without asking", "dontAsk" to "Does only what's always allowed, never asks",
    "bypassPermissions" to "Asks you nothing but its own questions")

/** A model's value as the agents list says it, by its name in its provider's API. */
private fun modelSaid(value: String) = value.substringAfterLast("/")

@Composable
private fun Heading(text: String) {
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

/** One of a group's choices: a round mark, what it is, and what that means. */
@Composable
private fun Pick(on: Boolean, label: String, about: String, pick: () -> Unit) {
    val c = LocalBaude.current
    Row(Modifier.fillMaxWidth().clickable(onClick = pick).padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(18.dp).clip(CircleShape).background(if (on) c.accentBg else c.inside)
            .border(1.5.dp, if (on) c.accentBg else c.dim, CircleShape), contentAlignment = Alignment.Center) {
            if (on) Box(Modifier.size(6.dp).clip(CircleShape).background(c.accentFg))
        }
        Spacer(Modifier.width(10.dp))
        Column {
            Text(label, style = Type.body, color = c.fg)
            if (about.isNotBlank()) Text(about, style = Type.caption, color = c.dim)
        }
    }
}

@Composable
private fun Entry(value: String, set: (String) -> Unit, hint: String, lines: Int = 1) {
    val c = LocalBaude.current
    Box(Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).background(c.fg.copy(alpha = 0.08f))
        .padding(horizontal = 10.dp, vertical = 9.dp)) {
        if (value.isEmpty()) Text(hint, style = Type.body, color = c.dim)
        BasicTextField(value, set, singleLine = lines == 1, minLines = lines, textStyle = Type.body.copy(color = c.fg),
            cursorBrush = SolidColor(c.accentBg), modifier = Modifier.fillMaxWidth())
    }
}
