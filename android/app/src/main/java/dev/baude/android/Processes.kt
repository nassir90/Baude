package dev.baude.android

import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import org.json.JSONObject
import java.util.Base64

/** What a chat's Processes page keeps while other pages show: which rows are opened, which is shown whole, and the
 * output last read for each. */
class ProcessesState {
    val open = mutableStateMapOf<String, Boolean>()
    val shown = mutableStateOf<String?>(null)
    val outputs = mutableStateMapOf<String, String>()
}

private val processStates = java.util.WeakHashMap<Chat, ProcessesState>()

fun processesState(chat: Chat): ProcessesState = processStates.getOrPut(chat) { ProcessesState() }

private val STARTED = Regex("""Command (?:running in background|was manually backgrounded by user) with ID: (\S+?)\.?\s+Output is being written to: (\S+)""")
private val AGENT_ID = Regex("""agentId: (\w+)""")

/** The background tasks a transcript's result and task lines tell of (task_from_result, task_from_note): one the
 * chat doesn't know yet starts as a task, and its end ends it; a known one only gets its output file. */
fun tasksFrom(chat: Chat, line: Line) {
    val c = chat.chrome
    val o = try { JSONObject(line.text) } catch (_: Exception) { return }
    when (line.kind) {
        "result" -> {
            val call = chat.calls.byId[o.optString("id")] ?: return
            val text = o.optString("text")
            val info = STARTED.find(text)?.let { m ->
                JSONObject().put("task_id", m.groupValues[1]).put("task_type", "local_bash")
                    .put("output_file", m.groupValues[2].trimEnd('.'))
                    .put("description", call.input.optString("description").ifBlank { call.input.optString("command") })
            } ?: if ("Async agent launched" in text) AGENT_ID.find(text)?.let { m ->
                JSONObject().put("task_id", m.groupValues[1]).put("task_type", "local_agent")
                    .put("description", call.input.optString("description"))
                    .put("subagent_type", call.input.optString("subagent_type")).put("prompt", call.input.optString("prompt"))
            } else null
            info ?: return
            val id = info.getString("task_id")
            val known = c.tasks[id]
            if (known != null) {
                if (known.optString("output_file").isBlank() && info.has("output_file"))
                    c.tasks[id] = JSONObject(known.toString()).put("output_file", info.getString("output_file"))
                return
            }
            taskEvent(c, "task_started", info.put("tool_use_id", call.id).put("is_backgrounded", true))
            fromFile(c, id)
        }
        "task" -> {
            val id = o.optString("task_id").ifBlank { return }
            val use = o.optString("tool_use_id")
            if (c.tasks[id] == null) {
                val kind = if (chat.calls.byId[use]?.base?.let { isAgent(it) } == true) "local_agent" else "local_bash"
                taskEvent(c, "task_started", JSONObject().put("task_id", id).put("tool_use_id", use)
                    .put("task_type", kind).put("description", o.optString("summary")))
                fromFile(c, id)
            }
            taskEvent(c, "task_notification", JSONObject().put("task_id", id)
                .put("tool_use_id", use.ifBlank { c.tasks[id]?.optString("tool_use_id").orEmpty() })
                .put("status", o.optString("status").ifBlank { "completed" })
                .put("summary", o.optString("result").ifBlank { o.optString("summary") })
                .put("output_file", o.optString("output_file").ifBlank { c.tasks[id]?.optString("output_file").orEmpty() }))
        }
    }
}

private fun fromFile(c: Chrome, id: String) {
    c.tasks[id] = JSONObject(c.tasks[id].toString()).put("from_file", true)
}

/** Tasks the transcript started that never ended and the chat's host doesn't list as running: interrupted, not
 * running for ever (settle_tasks). */
fun settleTasks(chat: Chat) {
    val c = chat.chrome
    val listed = (Mesh.session(chat.hostId, chat.sid) ?: return).commands.orEmpty().mapNotNull { it.id }.toSet()
    for ((id, t) in c.tasks.toMap()) {
        if (t.optBoolean("from_file") && t.optString("status") == "running" && id !in listed)
            c.tasks[id] = JSONObject(t.toString()).put("status", "interrupted")
    }
}

/** A task's line under its title (describe, marked): its kind, how it ended if it went wrong, what it's used, and
 * what it's doing while it runs. */
fun describeTask(t: JSONObject): String {
    val u = t.optJSONObject("usage")
    val status = t.optString("status").ifBlank { "running" }
    return listOfNotNull(t.optString("subagent_type").ifBlank { null },
        status.takeIf { it != "running" && it != "completed" && it != "interrupted" },
        u?.optInt("tool_uses", 0)?.takeIf { it > 0 }?.let { Wording.SidePanel.toolUses(n = it) },
        u?.optInt("total_tokens", 0)?.takeIf { it > 0 }?.let { Wording.SidePanel.tokens(n = fmtTokens(it)) },
        u?.optLong("duration_ms", 0)?.takeIf { it > 0 }?.let {
            Wording.Time.seconds(seconds = String.format(java.util.Locale.ROOT, "%.1f", it / 1000.0)) },
        t.optString("activity").ifBlank { null }.takeIf { status == "running" }).joinToString(" · ")
}

/** A process's title (update_process): Claude's words for it, else its command's first line as code. Its text, and
 * whether it's code. */
private fun processTitle(chat: Chat, t: JSONObject, c: BaudeColors): Pair<AnnotatedString, Boolean> {
    val call = chat.calls.byId[t.optString("tool_use_id")]
    val cmd = call?.input?.optString("command").orEmpty()
    val said = call?.input?.optString("description").orEmpty()
    val desc = t.optString("description")
    if (said.isNotBlank() || (desc.isNotBlank() && desc.trim() != cmd.trim() && cmd.isEmpty()))
        return AnnotatedString(said.ifBlank { desc }) to false
    val whole = cmd.ifEmpty { desc.ifEmpty { t.optString("task_id") } }
    val first = whole.trim().lines().firstOrNull().orEmpty().take(300)
    if (cmd.isEmpty()) return AnnotatedString(first) to true
    return highlighted(first, codeSpans(first, lang = "bash"), c, c.inside.luminance() < 0.5f) to true
}

private fun commandOf(chat: Chat, t: JSONObject) = chat.calls.byId[t.optString("tool_use_id")]?.input?.optString("command").orEmpty()

private fun processes(chat: Chat): List<JSONObject> {
    val c = chat.chrome
    return c.taskOrder.reversed().mapNotNull { c.tasks[it] }
        .filter { it.optString("task_type").isNotBlank() && it.optString("task_type") != "local_agent" }
}

/** The task's output file's last part, asked of its host (the task_output op): now, when its status or file
 * changes, and every second while it runs and the page's shown. */
@Composable
private fun OutputReader(chat: Chat, t: JSONObject, state: ProcessesState, wanted: Boolean, here: Boolean) {
    val id = t.optString("task_id")
    val file = t.optString("output_file")
    val running = t.optString("status") == "running"
    LaunchedEffect(id, running, file, wanted, here) {
        if (!wanted || file.isBlank() || !takes(chat.hostId, Op.TASK_OUTPUT)) return@LaunchedEffect
        while (true) {
            val r = asked(chat.hostId, Msg.TaskOutput(session = chat.sid, path = file, limit = 4000).json())
            val f = Msg.File.from(r)
            if (r.optString("op") == Op.FILE && f.error.isNullOrBlank() && !f.data.isNullOrEmpty()) {
                val text = (if (f.truncated == true) "… " else "") + String(Base64.getDecoder().decode(f.data), Charsets.UTF_8)
                if (state.outputs[id] != text) state.outputs[id] = text
            }
            if (!running || !here) break
            delay(1000)
        }
    }
}

/** A process's command and what it's printed (the last of it), in code blocks. */
@Composable
private fun ProcessBody(chat: Chat, t: JSONObject, state: ProcessesState, under: androidx.compose.ui.graphics.Color,
                        modifier: Modifier) {
    val cmd = commandOf(chat, t)
    SelectionContainer {
        Column(modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            if (cmd.isNotEmpty()) CodeBlock(cmd, "bash", under = under)
            CodeBlock(state.outputs[t.optString("task_id")] ?: Wording.SidePanel.noOutputYet, lang = "output", syntax = null,
                under = under)
        }
    }
}

private fun stop(chat: Chat, t: JSONObject) {
    val id = t.optString("task_id")
    control(chat.hostId, chat.sid, JSONObject().put("subtype", "stop_task").put("task_id", id)) {}
    chat.lines.add(Line("note", Wording.SidePanel.stopRequested(task = t.optString("description").ifBlank { id })))
}

/** A card's Background (send_to_background): its command or subagent moved to the background (Claude Code's
 * background_tasks, by its call), where it goes on; the turn goes on, so what's sent at it reaches the agent. Said in
 * the chat if it can't be. */
fun moveToBackground(chat: Chat, call: Call) {
    call.moving = true
    controlAnswer(chat.hostId, chat.sid, JSONObject().put("subtype", "background_tasks").put("tool_use_id", call.id)) { r, why ->
        if (why == null && r?.optBoolean("backgrounded", true) != false) return@controlAnswer
        call.moving = false
        chat.lines.add(Line("note", if (why != null) Wording.ToolCalls.notMovedWhy(why = why)
            else Wording.ToolCalls.notMoved(what = toolTitle(call.name))))
    }
}

/** A process's title over its line (its kind, what it's used), as a row and the page's header have them. */
@Composable
private fun ProcessTitle(chat: Chat, t: JSONObject, modifier: Modifier, wrap: Boolean) {
    val c = LocalBaude.current
    val (title, code) = processTitle(chat, t, c)
    val sub = describeTask(t)
    Column(modifier, verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(title, style = if (code) Type.body.copy(fontFamily = CodeFont.family) else Type.body, color = c.fg, maxLines = 1,
            overflow = TextOverflow.Ellipsis)
        if (sub.isNotBlank()) Text(sub, style = Type.caption, color = c.dim, maxLines = if (wrap) Int.MAX_VALUE else 1,
            overflow = TextOverflow.Ellipsis)
    }
}

/** The chat's background processes (its commands' tasks, as claude tells them), each opening to its command and
 * output, or into a page of its own, as Baude's Processes page lists them. */
@Composable
fun ProcessesPage(chat: Chat, here: Boolean) {
    val c = LocalBaude.current
    val state = processesState(chat)
    val rows = processes(chat)
    val shown = state.shown.value?.let { id -> rows.firstOrNull { it.optString("task_id") == id } }
    if (shown != null) {
        ProcessPage(chat, shown, state, here)
        return
    }
    rows.forEach { t -> OutputReader(chat, t, state, state.open[t.optString("task_id")] == true, here) }
    Scrolled {
        BoxedList(rows, Wording.SidePanel.noProcesses) { t ->
            val id = t.optString("task_id")
            val opened = state.open[id] == true
            val running = t.optString("status") == "running"
            Column {
                Row(Modifier.fillMaxWidth().heightIn(min = 50.dp).clickable { state.open[id] = !opened }
                    .padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    AgentStatus(t.optString("status").ifBlank { "running" })
                    ProcessTitle(chat, t, Modifier.weight(1f), wrap = false)
                    if (running && takes(chat.hostId, "control"))
                        IconButton(Adwaita.MediaPlaybackStop, Wording.SidePanel.stopProcess) { stop(chat, t) }
                    IconButton(if (opened) Adwaita.ListRemove else Adwaita.PanDown,
                        if (opened) Wording.SidePanel.hideOutput else Wording.SidePanel.showOutput) { state.open[id] = !opened }
                    IconButton(Adwaita.WindowMaximize, Wording.SidePanel.openProcess) { state.shown.value = id }
                }
                AnimatedVisibility(opened) { ProcessBody(chat, t, state, c.card, Modifier.fillMaxWidth()) }
            }
        }
    }
}

/** One process filling the page: its status, title and line, stop and back, then its command and output. */
@Composable
private fun ProcessPage(chat: Chat, t: JSONObject, state: ProcessesState, here: Boolean) {
    val c = LocalBaude.current
    val back = { state.shown.value = null }
    BackHandler(onBack = back)
    OutputReader(chat, t, state, true, here)
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(start = 15.dp, end = 15.dp, top = 6.dp, bottom = 6.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            AgentStatus(t.optString("status").ifBlank { "running" })
            ProcessTitle(chat, t, Modifier.weight(1f).padding(start = 6.dp), wrap = true)
            if (t.optString("status") == "running" && takes(chat.hostId, "control"))
                IconButton(Adwaita.MediaPlaybackStop, Wording.SidePanel.stopProcess) { stop(chat, t) }
            IconButton(Adwaita.GoPrevious, Wording.SidePanel.allProcesses, onClick = back)
        }
        Column(Modifier.weight(1f).fillMaxWidth().padding(top = 6.dp).verticalScroll(rememberScrollState())) {
            ProcessBody(chat, t, state, c.frame, Modifier.fillMaxWidth())
        }
    }
}
