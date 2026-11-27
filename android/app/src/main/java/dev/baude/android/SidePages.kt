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

/** A question asked after a side question's answer (its thread, in its own session). */
fun askAfter(chat: Chat, q: SideQuestion, text: String) {
    q.thread.add(Line("you", text))
    q.status.value = "running"
    val msg = JSONObject().put("type", "user").put("message", JSONObject().put("role", "user")
        .put("content", (if (q.thread.count { it.kind == "you" } == 1) PREFACE else "") + text))
    Mesh.ask(chat.hostId, Msg.Input(session = q.sid, msg = msg).json()) { r ->
        if (r.optString("op") == Op.ERROR) { q.status.value = "error"; q.thread.add(Line("error", Msg.Error.from(r).said.orEmpty())) }
    }
}

/** A side question asked of the chat: a session of its host's of its own, forked from it, and the question put to it. */
fun askSide(chat: Chat, text: String) {
    val hostId = chat.hostId
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
        askAfter(chat, q, text)
    }
}

/** Side questions, as Baude's page has them: a box to ask one, then each (newest first), opening into its thread
 * with a box to ask after it. Each is a session of its host's of its own (ephemeral, forked from the chat, in plan
 * mode), let go of with the chat. */
@Composable
fun SideQuestionsPage(chat: Chat) {
    val c = LocalBaude.current
    val hostId = chat.hostId
    var shown by remember(chat) { mutableStateOf<SideQuestion?>(null) }
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
                AskBox("Ask after it", enabled = q.sid.isNotEmpty() && q.status.value != "running" && q.status.value != "interrupted", compact = true) { askAfter(chat, q, it) }
            }
        }
        return
    }
    Scrolled {
        Box(Modifier.padding(start = 10.dp, end = 10.dp, top = 10.dp)) {
            AskBox("Ask a side question", enabled = takes(hostId, "ephemeral") && Mesh.session(hostId, chat.sid) != null) { askSide(chat, it) }
        }
        if (chat.btw.isNotEmpty()) BoxedList(chat.btw.toList(), "") { sq ->
            ActionRow(sq.question, sq.thread.getOrNull(1)?.text?.lines()?.firstOrNull(), onClick = { shown = sq },
                prefix = if (sq.status.value != "done") ({ AgentStatus(if (sq.status.value == "error") "failed" else sq.status.value) }) else null,
                suffix = { androidx.compose.material3.Icon(Adwaita.GoNext, null, tint = c.dim, modifier = Modifier.size(16.dp.icon)) })
        }
    }
}

/** A box to ask in (the composer's look: rounded 12, its ring), its send button off while it's empty. compact: a line
 * tall wrapped close round its button (a follow-up's, the composer's); else the new question's, with room round it. */
@Composable
internal fun AskBox(hint: String, enabled: Boolean = true, compact: Boolean = false, sendLabel: String = "Ask",
                    send: (String) -> Unit) {
    val c = LocalBaude.current
    var text by remember { mutableStateOf("") }
    val line = with(androidx.compose.ui.platform.LocalDensity.current) { Tokens.Font.bodyLine.toDp() }
    val inset = if (compact) Gap.composerInset else 6.dp
    val side = if (compact) Gap.composerButton else Gap.barButton
    val pad = if (compact) ((Gap.composerButton - line) / 2).coerceAtLeast(0.dp) else 7.dp
    val lift = ((line + pad * 2 - side) / 2).coerceAtLeast(0.dp) // (the button centred on the last line, at any text size)
    Row(Modifier.fillMaxWidth().padding(8.dp).clip(RoundedCornerShape(Gap.composerRadius)).background(c.inside)
        .border(1.dp, c.rule, RoundedCornerShape(Gap.composerRadius)).padding(inset), verticalAlignment = Alignment.Bottom) {
        Box(Modifier.weight(1f).padding(horizontal = 6.dp, vertical = pad)) {
            if (text.isEmpty()) Text(hint, style = Type.body, color = c.dim)
            BasicTextField(text, { text = it }, enabled = enabled, textStyle = Type.body.copy(color = c.fg),
                cursorBrush = SolidColor(c.accentBg), modifier = Modifier.fillMaxWidth().heightIn(max = 140.dp))
        }
        val ready = enabled && text.isNotBlank()
        Box(Modifier.padding(bottom = lift)) {
            IconButton(Adwaita.Return, sendLabel, enabled = ready, tip = sendLabel.takeIf { it != "Ask" }, tint = if (ready) c.accentFg else c.fg,
                background = if (ready) c.accentBg else null, side = side) { send(text.trim()); text = "" }
        }
    }
}
