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
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import org.json.JSONObject

/** This phone as a host: its agents, whether it's in the mesh, and what you've let them have (the models that may
 * serve them, who else may talk to them, what they reach). */
@Composable
fun PhoneScreen(back: () -> Unit, open: (String) -> Unit, make: () -> Unit) {
    val c = LocalBaude.current
    val ctx = LocalContext.current
    Mesh.tick.value // (a host's state, or the agents', changed: drawn again)
    var deleting by remember { mutableStateOf<JSONObject?>(null) }
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
                    Button("New Agent", suggested = Inference.allowed().isNotEmpty(), onClick = make)
                }
                agents.forEachIndexed { i, s ->
                    if (i > 0) Rule()
                    val name = s.optJSONObject("bot")?.optString("name")?.ifBlank { null } ?: "Agent"
                    Row(Modifier.fillMaxWidth().clickable { open(s.optString("id")) }.padding(start = 12.dp, top = 8.dp, bottom = 8.dp, end = 4.dp),
                        verticalAlignment = Alignment.CenterVertically) {
                        Avatar(name, null)
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
            Text("A model call goes through the mesh to the machine that has the model, and runs there as that " +
                "machine's own settings say. None serves this phone's agents till you allow it.",
                style = Type.caption, color = c.dim, modifier = Modifier.padding(horizontal = 4.dp))
            val served = Inference.found().groupBy { it.key }
            Panel(rows = true) {
                if (served.isEmpty()) Text("No machine in reach serves a model over an API (Ollama, or an " +
                    "OpenAI-compatible server).", style = Type.body, color = c.dim, modifier = Modifier.padding(12.dp))
                served.entries.forEachIndexed { i, (key, models) ->
                    if (i > 0) Rule()
                    val first = models.first()
                    val on = key in Trust.inference.value
                    Row(Modifier.fillMaxWidth().clickable { Trust.setServes(ctx, key, !on) }.padding(12.dp),
                        verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text("${first.providerName} on ${first.memberName}", style = Type.bold, color = c.fg)
                            Text(models.joinToString(", ") { it.display }, style = Type.caption, color = c.dim,
                                maxLines = 2, overflow = TextOverflow.Ellipsis)
                        }
                        Spacer(Modifier.width(8.dp))
                        Switch(on, { Trust.setServes(ctx, key, it) }, colors = SwitchDefaults.colors(
                            checkedTrackColor = c.accentBg, checkedThumbColor = c.accentFg,
                            uncheckedTrackColor = c.raised, uncheckedThumbColor = c.dim, uncheckedBorderColor = c.rule))
                    }
                }
            }

            Heading("Who may talk to them")
            Text("Anyone in the mesh sees their chats, as it sees any host's. This says who else may start agents " +
                "here, write to them, answer what they ask, or stop or delete them.",
                style = Type.caption, color = c.dim, modifier = Modifier.padding(horizontal = 4.dp))
            Panel(rows = true) {
                val now = Trust.talk.value
                listOf("phone" to ("This phone only" to "Nobody else, from anywhere"),
                    "hosts" to ("Your hosts too" to "Heads on the machines you've added here"),
                    "mesh" to ("Anyone in the mesh" to "Every member, and heads on any of them")).forEachIndexed { i, (k, said) ->
                    if (i > 0) Rule()
                    Pick(now == k, said.first, said.second) { Trust.setTalk(ctx, k) }
                }
            }

            Heading("What they reach")
            Panel {
                Text("Files in each agent's own folder on this phone; questions to you; and the mesh's hosts and " +
                    "chats, through this phone's connections to them. Nothing else of the phone's. Each use is asked " +
                    "of you first.", style = Type.body, color = c.dim)
            }
        }
    }
    deleting?.let { s ->
        val name = s.optJSONObject("bot")?.optString("name")?.ifBlank { null } ?: "this agent"
        AlertDialog(onDismissRequest = { deleting = null }, title = { Text("Delete $name?") },
            text = { Text("Its chat, its memory and the files in its folder are deleted from this phone. This can't be undone.") },
            confirmButton = {
                Button("Delete", destructive = true) {
                    Mesh.conn("phone")?.request(JSONObject().put("op", "delete").put("session", s.optString("id"))) {}
                    deleting = null
                }
            },
            dismissButton = { Button("Cancel") { deleting = null } }, containerColor = c.dialog)
    }
}

/** A new agent on this phone: its name, the model that serves it (of those you've allowed), and what it's to do
 * first. */
@Composable
fun NewAgentScreen(back: () -> Unit, made: (String) -> Unit) {
    val c = LocalBaude.current
    BackHandler(onBack = back)
    Mesh.tick.value
    var name by rememberSaveable { mutableStateOf("") }
    var model by rememberSaveable { mutableStateOf<String?>(null) }
    var prompt by rememberSaveable { mutableStateOf("") }
    var said by remember { mutableStateOf<String?>(null) }
    var making by remember { mutableStateOf(false) }
    val allowed = Inference.allowed()
    if (model == null || allowed.none { it.value == model }) model = allowed.firstOrNull()?.value
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
                if (allowed.isEmpty()) Text("No model serves this phone yet: allow one under Models, on This phone.",
                    style = Type.body, color = c.dim, modifier = Modifier.padding(12.dp))
                allowed.forEachIndexed { i, m ->
                    if (i > 0) Rule()
                    Pick(model == m.value, m.display, "${m.providerName} on ${m.memberName}") { model = m.value }
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
                Mesh.conn("phone")?.request(JSONObject().put("op", "start").put("model", model)
                    .put("bot", JSONObject().put("name", name.trim())).put("prompt", prompt.trim())) { r ->
                    main.post {
                        making = false
                        if (r.optString("op") == "started") made(r.optString("session"))
                        else said = r.optString("error", "It couldn't be made")
                    }
                }
            }
        }
    }
}

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
