package dev.baude.android

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.net.Uri
import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.CalendarMonth
import androidx.compose.material.icons.filled.Cancel
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Email
import androidx.compose.material.icons.filled.Image
import androidx.compose.material.icons.filled.Language
import androidx.compose.material.icons.filled.Map
import androidx.compose.material.icons.filled.MusicNote
import androidx.compose.material.icons.filled.RemoveCircleOutline
import androidx.compose.material.icons.filled.SportsEsports
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.TableChart
import androidx.compose.material.icons.filled.Terminal
import androidx.compose.material.icons.filled.Slideshow
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
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
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import org.json.JSONObject
import java.util.Base64

// ------------------------------------------------------------------------------------------------- boxed lists --

/** A side panel page's list (a boxed list: rounded 12, its edge, rows ruled apart), 10 in from the page's edges. */
@Composable
fun <T> BoxedList(rows: List<T>, empty: String, row: @Composable (T) -> Unit) {
    val c = LocalBaude.current
    Column(Modifier.fillMaxWidth().padding(10.dp).clip(RoundedCornerShape(12.dp)).background(c.card)
        .border(1.dp, c.rule, RoundedCornerShape(12.dp))) {
        if (rows.isEmpty()) Empty(empty)
        rows.forEachIndexed { i, r ->
            if (i > 0) Box(Modifier.fillMaxWidth().height(1.dp).background(c.rule))
            row(r)
        }
    }
}

/** A row of a boxed list (Adw.ActionRow): what's before it, its title and subtitle, what's after. */
@Composable
fun ActionRow(title: String, subtitle: String?, onClick: (() -> Unit)? = null, prefix: (@Composable () -> Unit)? = null,
              suffix: (@Composable () -> Unit)? = null) {
    val c = LocalBaude.current
    Row(Modifier.fillMaxWidth().heightIn(min = 50.dp).then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier)
        .padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        prefix?.invoke()
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(title, style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (!subtitle.isNullOrBlank()) Text(subtitle, style = Type.caption, color = c.dim, maxLines = 2,
                overflow = TextOverflow.Ellipsis)
        }
        suffix?.invoke()
    }
}

/** A page that scrolls, its list at the top. */
@Composable
fun Scrolled(content: @Composable ColumnScope.() -> Unit) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()), content = content)
}

// -------------------------------------------------------------------------------------------------- subagents --

/** How a subagent stands, as Baude's StatusSlot: working (a spinner), done, interrupted (grey), or gone wrong. */
@Composable
fun AgentStatus(status: String) {
    val c = LocalBaude.current
    when (status) {
        "running" -> CircularProgressIndicator(Modifier.size(16.dp), color = c.fg.copy(alpha = 0.55f), strokeWidth = 2.dp)
        "completed" -> Icon(Icons.Filled.CheckCircle, "Done", tint = c.success, modifier = Modifier.size(16.dp))
        "interrupted" -> Icon(Icons.Filled.RemoveCircleOutline, "Interrupted", tint = c.dim, modifier = Modifier.size(16.dp))
        else -> Icon(Icons.Filled.Cancel, "Failed", tint = c.destructive, modifier = Modifier.size(16.dp))
    }
}

/** A subagent's line under its title, as Baude's describe(): its kind, how it ended if it went wrong, what it's
 * used, and what it's doing while it runs. */
fun describe(a: Subagent): String {
    val u = a.usage.value
    val status = a.status.value
    return listOfNotNull(a.kind.ifBlank { null }, status.takeIf { it == "failed" || it == "killed" },
        u?.optInt("tool_uses", 0)?.takeIf { it > 0 }?.let { "$it tool use" + if (it == 1) "" else "s" },
        u?.optInt("total_tokens", 0)?.takeIf { it > 0 }?.let { "%,d tokens".format(it) },
        u?.optLong("duration_ms", 0)?.takeIf { it > 0 }?.let { "%.1fs".format(it / 1000.0) },
        a.activity.value.takeIf { status == "running" }).joinToString(" · ")
}

/** The subagents the chat's started (newest first), each opening into its own chat, read from its transcript on
 * the host (the subagents op), as Baude's Subagents page. */
@Composable
fun SubagentsPage(chat: Chat) {
    var shown by remember(chat) { mutableStateOf<Subagent?>(null) }
    val a = shown
    if (a == null) {
        Scrolled {
            BoxedList(chat.agents.toList(), "No subagents yet") { agent ->
                ActionRow(agent.description, describe(agent), onClick = { shown = agent }, prefix = { AgentStatus(agent.status.value) },
                    suffix = { Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, null, tint = LocalBaude.current.dim) })
            }
        }
        return
    }
    AgentDetail(chat, a) { shown = null }
}

@Composable
private fun AgentDetail(chat: Chat, a: Subagent, back: () -> Unit) {
    val c = LocalBaude.current
    androidx.activity.compose.BackHandler(onBack = back)
    val lines = remember(a) { mutableStateListOf<Line>() }
    val held = remember(a) { HashMap<String, Pair<Long, Long>>() } // (a file's: its size and mtime, as held here)
    val text = remember(a) { StringBuilder() }
    LaunchedEffect(a, a.agentId.value) {
        val id = a.agentId.value ?: return@LaunchedEffect
        val name = "agent-$id.jsonl"
        while (true) { // (each file held said, so only what's new comes; one request at a time)
            val have = JSONObject()
            for ((n, sm) in held) have.put(n, org.json.JSONArray(listOf(sm.first, sm.second)))
            val r = asked(chat.hostId, Msg.Subagents(session = chat.sid, have = have, limit = 4 shl 20).json())
            if (r.optString("op") != Op.SUBAGENTS) { delay(5_000); continue }
            val got = Msg.SubagentsReply.from(r)
            for (f in got.files.orEmpty()) {
                val n = f.name.orEmpty()
                val data = f.data.orEmpty()
                if (n == name && data.isNotEmpty()) {
                    val offset = f.offset?.toLong() ?: 0L
                    if (offset == 0L) text.setLength(0)
                    if (offset == 0L || offset == (held[n]?.first ?: 0L)) {
                        text.append(String(Base64.getDecoder().decode(data), Charsets.UTF_8))
                        lines.clear()
                        lines.addAll(parse(text.toString(), cutFirst = false, sidechain = true)
                            .filter { it.kind in setOf("you", "bot", "tool", "note", "error") })
                    }
                }
                held[n] = ((f.next ?: f.size)?.toLong() ?: 0L) to (f.mtime?.toLong() ?: 0L)
            }
            if (got.more == true) continue
            if (a.status.value != "running" && name in held) break
            delay(2_000)
        }
    }
    Column(Modifier.fillMaxSize()) {
        // its row, as its header: its status, its title and under it what it's done, back at the end
        Row(Modifier.fillMaxWidth().padding(start = 15.dp, end = 12.dp, top = 6.dp, bottom = 6.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            AgentStatus(a.status.value)
            Column(Modifier.weight(1f).padding(start = 6.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(a.description, style = Type.title, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis)
                val d = describe(a)
                if (d.isNotBlank()) Text(d, style = Type.caption, color = c.dim)
            }
            IconButton(Icons.AutoMirrored.Filled.ArrowBack, "All subagents", onClick = back)
        }
        Box(Modifier.fillMaxSize().padding(top = 6.dp).inside(c)) {
            val shown = if (lines.isEmpty() && a.prompt.isNotBlank()) listOf(Line("you", a.prompt)) else lines.toList()
            LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(12.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(shown) { Said(it) }
            }
        }
    }
}

/** Mesh.ask, waited for. */
suspend fun asked(hostId: String, msg: JSONObject): JSONObject = kotlinx.coroutines.suspendCancellableCoroutine { k ->
    Mesh.ask(hostId, msg) { r -> if (k.isActive) k.resumeWith(Result.success(r)) }
}

// -------------------------------------------------------------------------------------------------- processes --

/** The chat's background processes (its commands, as its host lists them), each stopped from here, as Baude's
 * Processes page lists them. */
@Composable
fun ProcessesPage(hostId: String, sid: String) {
    val c = LocalBaude.current
    Mesh.tick.value
    val commands = Mesh.session(hostId, sid)?.commands.orEmpty()
    var said by remember { mutableStateOf<String?>(null) }
    Scrolled {
        BoxedList(commands, "No background processes") { cmd ->
            val since = cmd.since?.toDouble() ?: 0.0
            val sub = listOfNotNull(if (cmd.background == true) "In the background" else null,
                if (since > 0) "since " + java.text.SimpleDateFormat("HH:mm", java.util.Locale.ENGLISH)
                    .format(java.util.Date((since * 1000).toLong())) else null).joinToString(" · ")
            ActionRow(cmd.what.orEmpty().lines().first(), sub, prefix = { AgentStatus("running") }, suffix = {
                if (takes(hostId, "control")) IconButton(Icons.Filled.Stop, "Stop this process") {
                    control(hostId, sid, JSONObject().put("subtype", "stop_task").put("task_id", cmd.id.orEmpty())) { e ->
                        if (e != null) said = "Couldn't stop it: $e"
                    }
                }
            })
        }
        said?.let { Text(it, style = Type.caption, color = c.dim, modifier = Modifier.padding(horizontal = 10.dp)) }
    }
}

// -------------------------------------------------------------------------------------------------- artifacts --

/** An artifact's icon, as Baude's ArtifactChip maps the one it's given (a globe for the rest). */
private fun artifactIcon(name: String?): ImageVector = when (name?.lowercase()) {
    "chart", "dashboard", "table" -> Icons.Filled.TableChart
    "calendar" -> Icons.Filled.CalendarMonth
    "map" -> Icons.Filled.Map
    "code" -> Icons.Filled.Terminal
    "doc", "document" -> Icons.Filled.Description
    "slides", "deck" -> Icons.Filled.Slideshow
    "image" -> Icons.Filled.Image
    "music" -> Icons.Filled.MusicNote
    "game" -> Icons.Filled.SportsEsports
    "mail" -> Icons.Filled.Email
    else -> Icons.Filled.Language
}

/** What the chat published (or linked to), newest first: each opened in the browser, or its link copied, as Baude's
 * Artifacts page lists them. */
@Composable
fun ArtifactsPage(chat: Chat) {
    val c = LocalBaude.current
    val ctx = LocalContext.current
    fun open(url: String) = try {
        ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
    } catch (_: Exception) {
        Toast.makeText(ctx, "No browser to open it in", Toast.LENGTH_SHORT).show()
    }
    Scrolled {
        BoxedList(chat.artifacts.toList(), "No artifacts yet") { a ->
            ActionRow(a.title, a.url.removePrefix("https://") + (a.version?.let { " · v$it" } ?: ""), onClick = { open(a.url) },
                prefix = { Icon(artifactIcon(a.icon), null, tint = c.accent, modifier = Modifier.size(18.dp)) },
                suffix = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Button("Open") { open(a.url) }
                        IconButton(Icons.Filled.ContentCopy, "Copy link") {
                            ctx.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("Artifact", a.url))
                            Toast.makeText(ctx, "Link copied", Toast.LENGTH_SHORT).show()
                        }
                    }
                })
        }
    }
}
