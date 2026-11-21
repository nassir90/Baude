package dev.baude.android

import android.graphics.BitmapFactory
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import kotlinx.coroutines.launch
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Base64
import java.util.Date
import java.util.Locale

// ------------------------------------------------------------------------------------------------ side questions --

/** A side question (/btw): asked of the chat's conversation without taking it up (start's ephemeral, forked from the
 * chat), its answer, and what's asked after it; kept while the chat's open, as Baude keeps them while its window is. */
class SideQuestion(val question: String) {
    var sid = ""
    val thread = mutableStateListOf<Line>()   // (its answers, and what's asked after: you and bot)
    val status = mutableStateOf("running")    // (running, done, error, interrupted)
    val text = StringBuilder()                // (the answer coming, as it streams)
}

/** (the chat open) What its side questions' sessions say: their answers, their turns' ends; what they ask, denied. */
fun sideQuestions(chat: Chat): (String, JSONObject) -> Unit = l@{ at, msg ->
    if (at != chat.hostId) return@l
    if (msg.optString("op") == "connected") { // (its host back after a drop: those it was answering were let go of)
        chat.btw.filter { it.status.value == "running" }.forEach { it.status.value = "interrupted" }
        return@l
    }
    val op = msg.optString("op")
    val session = when (op) {
        Op.EXITED -> Msg.Exited.from(msg).session
        Op.DELETED -> Msg.Deleted.from(msg).session
        Op.CLAUDE -> Msg.Claude.from(msg).session
        else -> return@l
    }
    val q = chat.btw.firstOrNull { it.sid.isNotEmpty() && it.sid == session.orEmpty() } ?: return@l
    when (op) {
        Op.EXITED, Op.DELETED -> if (q.status.value == "running") q.status.value = "interrupted"
        Op.CLAUDE -> {
            val m = Msg.Claude.from(msg).msg ?: return@l
            Ask.of(m)?.let { a -> // (no tools that ask: denied, as Baude's side questions deny them)
                Mesh.ask(at, Msg.Input(session = q.sid, msg = JSONObject()
                    .put("type", "control_response").put("response", JSONObject().put("subtype", "success")
                        .put("request_id", a.rid).put("response", JSONObject().put("behavior", "deny")
                            .put("message", "Side questions can't run tools that need permission.")))).json()) {}
            }
            when (m.optString("type")) {
                "assistant" -> if (m.isNull("parent_tool_use_id")) for (b in m.optJSONObject("message")
                    ?.optJSONArray("content").list()) if (b.optString("type") == "text") q.text.append(b.optString("text"))
                "result" -> {
                    val said = q.text.toString().trim()
                    q.text.setLength(0)
                    q.thread.add(Line(if (m.optBoolean("is_error")) "error" else "bot",
                        said.ifEmpty { if (m.optBoolean("is_error")) m.optString("result").ifBlank { Wording.SideQuestions.failed } else "" }))
                    q.status.value = if (m.optBoolean("is_error")) "error" else "done"
                }
            }
        }
    }
}

private const val PREFACE = "(This is a side question, asked while you work on the conversation above. Answer it " +
    "briefly from what you already know; don't continue the task or use tools.)\n\n"

/** Side questions, as Baude's page has them: a box to ask one, then each (newest first), opening into its thread
 * with a box to ask after it. Each is a session of its host's of its own (ephemeral, forked from the chat, in plan
 * mode), let go of with the chat. */
@Composable
fun SideQuestionsPage(chat: Chat) {
    val c = LocalBaude.current
    val hostId = chat.hostId
    var shown by remember(chat) { mutableStateOf<SideQuestion?>(null) }
    fun ask(q: SideQuestion, text: String) {
        q.thread.add(Line("you", text))
        q.status.value = "running"
        val msg = JSONObject().put("type", "user").put("message", JSONObject().put("role", "user")
            .put("content", (if (q.thread.count { it.kind == "you" } == 1) PREFACE else "") + text))
        Mesh.ask(hostId, Msg.Input(session = q.sid, msg = msg).json()) { r ->
            if (r.optString("op") == Op.ERROR) { q.status.value = "error"; q.thread.add(Line("error", Msg.Error.from(r).said.orEmpty())) }
        }
    }
    fun start(text: String) {
        val s = Mesh.session(hostId, chat.sid)
        val q = SideQuestion(text)
        chat.btw.add(0, q)
        val model = s?.model?.ifBlank { null }
        val msg = Msg.Start(ephemeral = true, prompt = "", forkFrom = chat.sid, model = model,
            mode = if (program(model) == "codex") "default" else "plan", attach = true, answers = true).json()
        Mesh.ask(hostId, msg) { r ->
            val id = Msg.Started.from(r).session?.id
            if (r.optString("op") != Op.STARTED || id == null) {
                q.status.value = "error"
                q.thread.add(Line("error", Wording.SideQuestions.notAsked(why = Msg.Error.from(r).said.orEmpty())))
                return@ask
            }
            q.sid = id
            ask(q, text)
        }
    }
    val q = shown
    if (q != null) {
        BackHandler { shown = null }
        Column(Modifier.fillMaxSize()) {
            Row(Modifier.fillMaxWidth().padding(start = 13.dp, end = 7.dp, top = 8.dp, bottom = 8.dp),
                verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                if (q.status.value != "done") AgentStatus(if (q.status.value == "error") "failed" else q.status.value)
                Text(q.question, style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f))
                IconButton(Adwaita.GoPrevious, Wording.SideQuestions.all) { shown = null }
            }
            Column(Modifier.weight(1f).padding(top = 6.dp).inside(c)) {
                LazyColumn(Modifier.weight(1f), contentPadding = PaddingValues(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    items(q.thread.drop(1)) { Said(it) }
                    if (q.status.value == "running") item { Text("Working…", style = Type.caption, color = c.dim) }
                }
                AskBox("Ask after it", enabled = q.sid.isNotEmpty() && q.status.value != "running" && q.status.value != "interrupted") { ask(q, it) }
            }
        }
        return
    }
    Scrolled {
        Box(Modifier.padding(start = 10.dp, end = 10.dp, top = 10.dp)) {
            AskBox("Ask a side question", enabled = takes(hostId, "ephemeral") && Mesh.session(hostId, chat.sid) != null, ::start)
        }
        if (chat.btw.isNotEmpty()) BoxedList(chat.btw.toList(), "") { sq ->
            ActionRow(sq.question, sq.thread.getOrNull(1)?.text?.lines()?.firstOrNull(), onClick = { shown = sq },
                prefix = if (sq.status.value != "done") ({ AgentStatus(if (sq.status.value == "error") "failed" else sq.status.value) }) else null,
                suffix = { androidx.compose.material3.Icon(Adwaita.GoNext, null, tint = c.dim) })
        }
    }
}

/** A box to ask in (the composer's look: rounded 12, its ring), its send button off while it's empty. */
@Composable
private fun AskBox(hint: String, enabled: Boolean = true, send: (String) -> Unit) {
    val c = LocalBaude.current
    var text by remember { mutableStateOf("") }
    Row(Modifier.fillMaxWidth().padding(8.dp).clip(RoundedCornerShape(Gap.composerRadius)).background(c.inside)
        .border(1.dp, c.rule, RoundedCornerShape(Gap.composerRadius)).padding(6.dp), verticalAlignment = Alignment.Bottom) {
        Box(Modifier.weight(1f).padding(horizontal = 6.dp, vertical = 7.dp)) {
            if (text.isEmpty()) Text(hint, style = Type.body, color = c.dim)
            BasicTextField(text, { text = it }, enabled = enabled, textStyle = Type.body.copy(color = c.fg),
                cursorBrush = SolidColor(c.accentBg), modifier = Modifier.fillMaxWidth().heightIn(max = 140.dp))
        }
        val ready = enabled && text.isNotBlank()
        IconButton(Adwaita.Return, "Ask", enabled = ready, tint = if (ready) c.accentFg else c.fg,
            background = if (ready) c.accentBg else null) { send(text.trim()); text = "" }
    }
}

// --------------------------------------------------------------------------------------------------------- signal --

private val KINDS = mapOf("file" to Wording.Lookup.files, "commit" to Wording.Lookup.commits,
    "branch" to Wording.Lookup.branches, "pr" to Wording.Lookup.pullRequests, "env" to Wording.Lookup.envVars,
    "constant" to Wording.Lookup.constants, "symbol" to Wording.Lookup.codeSymbols, "config" to Wording.Lookup.configKeys,
    "service" to Wording.Lookup.services, "k8s" to Wording.Lookup.kubernetesObjects, "package" to Wording.Lookup.packages,
    "cli" to Wording.Lookup.commands, "external" to Wording.Lookup.externalTools, "tool" to Wording.Lookup.tools,
    "subagent" to Wording.Lookup.subagentTypes, "skill" to Wording.Lookup.skills, "route" to Wording.Lookup.httpRoutes,
    "db" to Wording.Signal.databaseTables)

/** Signal's names for the chat, as Baude's Signal page lists them: those its replies named that the folder's glossary
 * has, by kind, each how it stands (linked, several matches, looking up, not found, stale) and what it is. Read from
 * its host (signal_names), as Signal last left them there: the reader runs where Baude does. */
@Composable
fun SignalPage(hostId: String, sid: String) {
    val c = LocalBaude.current
    val s = Mesh.session(hostId, sid)
    val cwd = s?.cwd?.ifBlank { null }
    if (cwd == null) return
    var got by remember(cwd, sid) { mutableStateOf<Msg.SignalNamesReply?>(null) }
    var filter by remember { mutableStateOf("") }
    var again by remember { mutableStateOf(0) }
    LaunchedEffect(cwd, sid, again) {
        val msg = Msg.SignalNames(cwd = cwd, session = sid).json()
        s.cliId?.ifBlank { null }?.let { msg.put("cli_id", it) }
        Mesh.ask(hostId, msg) { r ->
            got = Msg.SignalNamesReply.from(r).let { g -> if (r.optString("op") == Op.ERROR) Msg.SignalNamesReply(error = said(r, null)) else g }
        }
    }
    val g = got
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.padding(start = 8.dp, end = 6.dp, top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            SearchField(filter, { filter = it }, Wording.Signal.filter, Modifier.weight(1f))
            IconButton(Adwaita.ViewRefresh, "Read them again") { got = null; again++ }
        }
        when {
            g == null || !g.error.isNullOrBlank() -> {}
            else -> {
                val refs = g.refs.orEmpty()
                fun n(st: String) = refs.count { it.status == st }
                Scrolled {
                    Text(listOf<Pair<String, (Int) -> String>>("resolved" to { Wording.Signal.linked(n = it) },
                        "ambiguous" to { Wording.Signal.several(n = it) }, "pending" to { Wording.Signal.lookingUp(n = it) },
                        "not_found" to { Wording.Signal.notFound(n = it) }, "stale" to { Wording.Signal.stale(n = it) })
                        .filter { n(it.first) > 0 }.joinToString(" · ") { it.second(n(it.first)) } +
                        (g.glossary?.ifBlank { null }?.let { "\n" + Wording.Signal.glossary(path = it) } ?: ""), style = Type.caption, color = c.dim,
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp))
                    if (refs.isEmpty()) Empty(Wording.Signal.empty)
                    val f = filter.trim().lowercase()
                    for ((kind, rows) in refs.filter { f.isEmpty() || it.surface.orEmpty().lowercase().contains(f) }
                        .groupBy { it.kind.orEmpty() }) {
                        Text(KINDS[kind] ?: kind, style = Type.folder, color = c.dim, modifier = Modifier.padding(start = 14.dp, top = 8.dp))
                        BoxedList(rows, "") { r ->
                            val st = r.status.orEmpty()
                            ActionRow(r.surface.orEmpty(), r.summary.orEmpty(), prefix = {
                                androidx.compose.material3.Icon(when (st) {
                                    "resolved" -> Adwaita.ObjectSelect
                                    "pending" -> Adwaita.ContentLoading
                                    "ambiguous" -> Adwaita.ViewMore
                                    else -> Adwaita.DialogWarning
                                }, st, tint = when (st) {
                                    "resolved" -> c.success
                                    "ambiguous", "pending" -> c.dim
                                    else -> c.warning
                                }, modifier = Modifier.size(16.dp.icon))
                            })
                        }
                    }
                }
            }
        }
    }
}
