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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import org.json.JSONObject
import java.util.Base64

// ------------------------------------------------------------------------------------------------- boxed lists --

/** A side panel page's list (a boxed list: rounded 12, its edge, rows ruled apart), 10 in from the page's edges (or
 * inset: a dialog's, flush with its sides). */
@Composable
fun <T> BoxedList(rows: List<T>, empty: String, inset: Dp = 10.dp, row: @Composable (T) -> Unit) {
    val c = LocalBaude.current
    Column(Modifier.fillMaxWidth().padding(inset).clip(RoundedCornerShape(12.dp)).background(c.card)
        .border(1.dp, c.rule, RoundedCornerShape(12.dp))) {
        if (rows.isEmpty()) Empty(empty)
        rows.forEachIndexed { i, r ->
            if (i > 0) Box(Modifier.fillMaxWidth().height(1.dp).background(c.rule))
            row(r)
        }
    }
}

/** A dense list's row: the Fixes list's, and a task's inputs, triggers and runs (as Baude's navigation lists). */
val DENSE_ROW = 38.dp

/** A row of a boxed list (Adw.ActionRow): what's before it, its title and subtitle, what's after. compact: a dense
 * list's row, as high as the Fixes list's (DENSE_ROW), its text a little in from its edges. */
@Composable
fun ActionRow(title: String, subtitle: String?, onClick: (() -> Unit)? = null, prefix: (@Composable () -> Unit)? = null,
              suffix: (@Composable () -> Unit)? = null, titleLines: Int = 1, compact: Boolean = false, bold: Boolean = false) {
    val c = LocalBaude.current
    Row(Modifier.fillMaxWidth().heightIn(min = if (compact) DENSE_ROW else 50.dp)
        .then(if (onClick != null) Modifier.clickable(onClick = onClick.tapping(title)) else Modifier)
        .padding(horizontal = 12.dp, vertical = if (compact) 0.dp else 8.dp), verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        prefix?.invoke()
        Column(Modifier.weight(1f).padding(vertical = if (compact) 4.dp else 0.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(title, style = if (bold) Type.body.copy(fontWeight = FontWeight.Bold) else Type.body, color = c.fg, maxLines = titleLines,
                overflow = TextOverflow.Ellipsis)
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
        "completed" -> Icon(Adwaita.ObjectSelect, null, tint = c.success, modifier = Modifier.size(16.dp.icon))
        "interrupted" -> Icon(Adwaita.ProcessStop, null, tint = c.dim, modifier = Modifier.size(16.dp.icon))
        else -> Icon(Adwaita.DialogError, null, tint = c.destructive, modifier = Modifier.size(16.dp.icon))
    }
}

/** A count of tokens as chatparts.fmt_tokens says it: 950, 12.3k, 1M. */
fun fmtTokens(n: Int): String = fmtTokens(n.toLong())

/** A count of tokens as chatparts.fmt_tokens says it, past an Int's (a year's). */
fun fmtTokens(n: Long): String {
    fun one(x: Double) = String.format(java.util.Locale.ROOT, "%.1f", x).removeSuffix(".0")
    return if (n >= 1_000_000) Wording.Tokens.millions(n = one(n / 1_000_000.0))
        else if (n >= 1000) Wording.Tokens.thousands(n = one(n / 1000.0)) else "$n"
}

/** A subagent's line under its title, as Baude's describe(): its kind, how it ended if it went wrong, what it's
 * used, and what it's doing while it runs. */
fun describe(a: Subagent): String {
    val u = a.usage.value
    val status = a.status.value
    return listOfNotNull(a.kind.ifBlank { null }, status.takeIf { it == "failed" || it == "killed" },
        u?.optInt("tool_uses", 0)?.takeIf { it > 0 }?.let { Wording.SidePanel.toolUses(n = it) },
        u?.optInt("total_tokens", 0)?.takeIf { it > 0 }?.let { n -> Wording.SidePanel.tokens(n = fmtTokens(n)) },
        u?.optLong("duration_ms", 0)?.takeIf { it > 0 }?.let {
            Wording.Time.seconds(seconds = String.format(java.util.Locale.ROOT, "%.1f", it / 1000.0)) },
        a.activity.value.takeIf { status == "running" }).joinToString(" · ")
}

/** The subagents the chat's started (newest first), each opening into its own chat, read from its transcript on
 * the host (the subagents op), as Baude's Subagents page. */
@Composable
fun SubagentsPage(chat: Chat) {
    val held = shownAgents.getOrPut(chat) { mutableStateOf(null) } // (kept while other pages show)
    var shown by held
    LaunchedEffect(chat.showAgent.value) { // (View on a call's card: its subagent opened here)
        val id = chat.showAgent.value ?: return@LaunchedEffect
        chat.agents.firstOrNull { it.toolUseId == id }?.let { shown = it }
    }
    val a = shown
    if (a == null) {
        Scrolled {
            BoxedList(chat.agents.toList(), Wording.SidePanel.noSubagents) { agent ->
                ActionRow(agent.description, describe(agent), onClick = { shown = agent }, prefix = { AgentStatus(agent.status.value) },
                    suffix = { Icon(Adwaita.GoNext, null, tint = LocalBaude.current.dim, modifier = Modifier.size(16.dp.icon)) })
            }
        }
        return
    }
    AgentDetail(chat, a, open = { shown = it }) { shown = null }
}

private val shownAgents = java.util.WeakHashMap<Chat, androidx.compose.runtime.MutableState<Subagent?>>()

@Composable
private fun AgentDetail(chat: Chat, a: Subagent, open: (Subagent) -> Unit, back: () -> Unit) {
    val c = LocalBaude.current
    androidx.activity.compose.BackHandler(onBack = back)
    val lines = remember(a) { mutableStateListOf<Line>() }
    val calls = remember(a) { ChatCalls() }
    val held = remember(a) { HashMap<String, Pair<Long, Long>>() } // (a file's: its size and mtime, as held here)
    val text = remember(a) { StringBuilder() }
    val nested = remember(a) { HashMap<String, String>() } // (a subagent call's id: its agent's id)
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
                        val open = calls.byId.mapValues { it.value.inputOpen to it.value.outputOpen }
                        lines.clear()
                        calls.byId.clear()
                        nested.clear()
                        for (l in parse(text.toString(), cutFirst = false, sidechain = true)) when (l.kind) {
                            "call", "result", "task", "stopped" -> {
                                took(calls, lines, l)
                                if (l.kind == "result") try {
                                    val o = JSONObject(l.text)
                                    if (calls.byId[o.optString("id")]?.base?.let { isAgent(it) } == true)
                                        Regex("agentId: ([A-Za-z0-9_-]+)").find(o.optString("text"))?.groupValues?.get(1)
                                            ?.let { nested[o.optString("id")] = it }
                                } catch (_: Exception) {}
                            }
                            "you", "bot", "note", "error" -> lines.add(l)
                        }
                        for ((id, o) in open) calls.byId[id]?.let { it.inputOpen = o.first; it.outputOpen = o.second }
                        if (a.status.value != "running") settle(calls, "idle")
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
            IconButton(Adwaita.GoPrevious, Wording.SidePanel.allSubagents, onClick = back)
        }
        Box(Modifier.fillMaxSize().padding(top = 6.dp).inside(c)) {
            val parsed = lines.toList()
            val first = parsed.indexOfFirst { it.kind == "you" } // (the file's first user record: the task's prompt, drawn instead)
            val shown = if (a.prompt.isBlank()) parsed else listOf(Line("you", a.prompt)) +
                if (first >= 0 && parsed.take(first).none { it.kind == "bot" || it.kind == "call" })
                    parsed.filterIndexed { i, _ -> i != first } else parsed
            val view = { call: Call ->
                open(chat.agents.firstOrNull { it.toolUseId == call.id } ?: Subagent(call.id,
                    call.input.optString("description").ifBlank { Wording.SidePanel.subagent },
                    call.input.optString("subagent_type"), call.input.optString("prompt")).also { s ->
                    s.agentId.value = nested[call.id]
                    s.status.value = when (call.state) {
                        "running", "background" -> "running"
                        "error" -> "failed"
                        "interrupted" -> "interrupted"
                        else -> "completed"
                    }
                })
            }
            LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(12.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)) {
                drawn(shown, calls, view = view) { Said(it) }
            }
        }
    }
}

/** Mesh.ask, waited for. */
suspend fun asked(hostId: String, msg: JSONObject): JSONObject = kotlinx.coroutines.suspendCancellableCoroutine { k ->
    Mesh.ask(hostId, msg) { r -> if (k.isActive) k.resumeWith(Result.success(r)) }
}

// -------------------------------------------------------------------------------------------------- artifacts --

/** An artifact's icon, as Baude's ArtifactChip maps the one it's given (a globe for the rest). */
private fun artifactIcon(name: String?): ImageVector = when (name?.lowercase()) {
    "chart", "dashboard", "table" -> Adwaita.XOfficeSpreadsheet
    "calendar" -> Adwaita.XOfficeCalendar
    "map" -> Adwaita.Map
    "code" -> Adwaita.UtilitiesTerminal
    "doc", "document" -> Adwaita.XOfficeDocument
    "slides", "deck" -> Adwaita.XOfficePresentation
    "image" -> Adwaita.ImageXGeneric
    "music" -> Adwaita.AudioXGeneric
    "game" -> Adwaita.ApplicationsGames
    "mail" -> Adwaita.MailUnread
    else -> Adwaita.WebBrowser
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
        BoxedList(chat.artifacts.toList(), Wording.SidePanel.noArtifacts) { a ->
            ActionRow(a.title, a.url.removePrefix("https://").let { link ->
                a.version?.let { Wording.Artifacts.linkVersion(link = link, version = it) } ?: link }, onClick = { open(a.url) },
                prefix = { Icon(artifactIcon(a.icon), null, tint = c.accent, modifier = Modifier.size(16.dp.icon)) },
                suffix = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Button(Wording.Common.open) { open(a.url) }
                        IconButton(Adwaita.EditCopy, Wording.Artifacts.copyLink) {
                            ctx.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("Artifact", a.url))
                            Toast.makeText(ctx, Wording.Tabs.linkCopied, Toast.LENGTH_SHORT).show()
                        }
                    }
                })
        }
    }
}
