package dev.baude.android

import android.os.SystemClock
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale

/** What a chat's status bar, banner and stop button keep: what it's spent, its last turn, its context's usage, its
 * background tasks, whether its host still has it, the API retrying, an interrupt waiting. Compose state, changed on
 * the main thread. */
class Chrome {
    val cost = mutableStateOf(0.0)
    var costSeen = 0.0                              // (the last total claude gave: spent_since)
    val lastTurn = mutableStateOf("")
    val usage = mutableStateOf<JSONObject?>(null)   // (what its session said of its context)
    val outgrown = mutableStateOf(false)            // (a turn ended saying the prompt's too long)
    val retrying = mutableStateOf<JSONObject?>(null) // (an api_retry, till what it says next)
    val tasks = mutableStateMapOf<String, JSONObject>() // (a task's id: its fields, replaced whole as they change)
    val missing = mutableStateOf(false)             // (its host answered the attach: no such session)
    var interrupting = 0L                           // (an interrupt waiting: its token)
    var contextSoon = false                         // (the meter asked again a moment from now)
}

/** What claude's retrying, and what a host said as it stopped, per host: what it said and when (elapsed ms), kept
 * from the first of its words till it's back. */
object Stopping {
    val said = mutableStateMapOf<String, Pair<Msg.Stopping, Long>>()
    const val SAID_MS = 60_000L // (how long a host's last word as it stopped is said while it isn't back)
    private var listening = false

    fun listen() {
        if (listening) return
        listening = true
        Mesh.listeners.add { at, msg ->
            when (msg.optString("op")) {
                Op.STOPPING -> said[at] = Msg.Stopping.from(msg) to (said[at]?.second ?: SystemClock.elapsedRealtime())
                "connected" -> said.remove(at)
            }
        }
    }

    /** Milliseconds it's still said for, or 0 (nothing said, or said long ago). */
    fun left(at: String): Long = said[at]?.let { (SAID_MS - (SystemClock.elapsedRealtime() - it.second)).coerceAtLeast(0) } ?: 0

    /** What it said as it stopped, as a banner says it (client.stopping_line), or null. */
    fun line(at: String): String? {
        if (left(at) <= 0) return null
        val q = said[at]!!.first
        val name = Mesh.label(at)
        val restart = q.restart == true
        val left = q.left?.toInt()
        val total = q.procs?.toInt() ?: 0
        return when {
            left != null && left > 0 -> if (restart) Wording.HostStatus.restartingLeft(host = name, left = left, total = total)
                else Wording.HostStatus.stoppingLeft(host = name, left = left, total = total)
            left != null && total > 0 -> if (restart) Wording.HostStatus.restartingAllStopped(host = name)
                else Wording.HostStatus.stoppingAllStopped(host = name)
            else -> if (restart) Wording.HostStatus.restarting(host = name) else Wording.HostStatus.stopping(host = name)
        }
    }
}

/** Whether the chat is at a turn: its session lists it working or waiting, or it's Meta's, thinking. */
fun isWorking(chat: Chat): Boolean {
    val state = Mesh.session(chat.hostId, chat.sid)?.state
    return state == "working" || state == "waiting" || chat.thinking.value
}

/** A turn's error that says the window was outgrown (meta.OVERFLOW_RE). */
private val OVERFLOW = Regex("prompt is too long|context (window|length|size)|exceed\\w*[^.]{0,40}\\b(context|tokens)\\b|" +
    "too many tokens|maximum context", RegexOption.IGNORE_CASE)

/** What a result adds to what's been spent (claude.spent_since): its total is the session's so far; one lower than
 * the last seen is a count started again. */
fun spentSince(chrome: Chrome, m: JSONObject): Double {
    val spent = m.optDouble("total_cost_usd", 0.0).let { if (it.isNaN()) 0.0 else it }
    val seen = chrome.costSeen
    chrome.costSeen = spent
    return if (spent >= seen) spent - seen else spent
}

/** (the chat open) What its host says of it as it's heard: results, tasks, usage, retries; and its host back. */
fun chrome(chat: Chat): (String, JSONObject) -> Unit = l@{ at, msg ->
    if (at != chat.hostId) return@l
    when (msg.optString("op")) {
        "connected" -> refreshContext(chat)
        Op.CLAUDE -> {
            val said = Msg.Claude.from(msg)
            if (said.session.orEmpty() == chat.sid) said.msg?.let { heard(chat, it) }
        }
    }
}

private fun heard(chat: Chat, m: JSONObject) {
    val c = chat.chrome
    val type = m.optString("type")
    val top = m.str("parent_tool_use_id") == null
    when (type) {
        "result" -> if (top) result(chat, m)
        "system" -> when (val sub = m.optString("subtype")) {
            "task_started", "task_progress", "task_updated", "task_notification" -> task(c, sub, m)
            "background_tasks_changed" -> for (t in m.optJSONArray("tasks").list()) {
                val id = t.str("task_id") ?: continue
                c.tasks[id] = merge(c.tasks[id] ?: JSONObject().put("status", "running"), t)
            }
            "codex_context", "baude_context" -> m.optJSONObject("usage")?.let { showUsage(chat, it) }
            "compact_boundary" -> refreshContext(chat)
            "api_retry" -> if (top) c.retrying.value = m
        }
        "assistant", "user" -> if (type == "assistant" || carriesResult(m)) contextSoon(chat)
    }
    if (top && type in setOf("stream_event", "assistant")) c.retrying.value = null
}

private fun carriesResult(m: JSONObject) = m.optJSONObject("message")?.optJSONArray("content").list()
    .any { it.optString("type") == "tool_result" }

private fun merge(into: JSONObject, from: JSONObject, skip: Set<String> = emptySet()): JSONObject {
    val o = JSONObject(into.toString())
    for (k in from.keys()) if (k !in skip) o.put(k, from.get(k))
    return o
}

/** A turn's result (h_result): what it spent, its summary, whether the window's outgrown; what waited on it let go
 * of; the context asked again. */
private fun result(chat: Chat, m: JSONObject) {
    val c = chat.chrome
    c.cost.value += spentSince(c, m)
    val u = m.optJSONObject("usage") ?: JSONObject()
    val used = u.optInt("input_tokens") + u.optInt("cache_read_input_tokens") + u.optInt("cache_creation_input_tokens")
    c.lastTurn.value = Wording.StatusBar.lastTurn(turns = m.optInt("num_turns", 0),
        seconds = String.format(Locale.ROOT, "%.1f", m.optDouble("duration_ms", 0.0) / 1000), used = fmtTokens(used),
        written = fmtTokens(u.optInt("output_tokens")))
    if (m.optBoolean("is_error") && OVERFLOW.containsMatchIn(m.optString("result"))) c.outgrown.value = true
    c.interrupting = 0
    c.retrying.value = null
    refreshContext(chat)
}

/** A background task's event (on_task_event): its fields kept, its status as the event says. */
private fun task(c: Chrome, kind: String, m: JSONObject) {
    val id = m.str("task_id") ?: return
    var t = c.tasks[id] ?: JSONObject().put("task_id", id).put("status", "running")
    val skip = setOf("type", "subtype", "uuid", "session_id")
    t = when (kind) {
        "task_updated" -> m.optJSONObject("patch")?.let { merge(t, it) } ?: t
        "task_progress" -> merge(t, JSONObject().also { f ->
            for (k in m.keys()) if (k !in skip && k != "description") f.put(k, m.get(k))
            f.put("activity", m.opt("description") ?: JSONObject.NULL)
        })
        else -> merge(t, m, skip)
    }
    if (kind == "task_notification") t = merge(t, JSONObject().put("status", m.str("status") ?: "completed"))
    c.tasks[id] = t
}

/** The tasks that run now: (processes, subagents). */
fun runningTasks(c: Chrome): List<JSONObject> = c.tasks.values.filter { it.optString("status") == "running" }

// ---- context

/** The context's usage asked of the chat's session (a stopped one's host answers {}, which changes nothing). */
fun refreshContext(chat: Chat, model: String? = null) {
    val at = chat.hostId
    if (!takes(at, "control")) return
    val asked = model ?: Mesh.session(at, chat.sid)?.model?.ifBlank { null }
    controlAnswer(at, chat.sid, JSONObject().put("subtype", "get_context_usage")) { r, _ ->
        if (r != null && r.length() > 0) showUsage(chat, r, asked)
    }
}

/** The meter asked again a moment from now, at most once a second, while a turn runs (refresh_context_soon). */
private fun contextSoon(chat: Chat) {
    val c = chat.chrome
    if (c.contextSoon || !isWorking(chat)) return
    c.contextSoon = true
    Mesh.later(1000) {
        c.contextSoon = false
        refreshContext(chat)
    }
}

/** A usage the session said: its window kept for the next session of the model's, and shown (set_usage: one with
 * neither a window nor a wish to be shown without one changes nothing). */
fun showUsage(chat: Chat, usage: JSONObject, model: String? = null) {
    val total = usage.optInt("maxTokens", 0)
    if (total <= 0 && !usage.optBoolean("showWithoutLimit")) return
    val m = model ?: Mesh.session(chat.hostId, chat.sid)?.model?.ifBlank { null }
    if (total > 0) rememberWindow(chat.hostId, m, total)
    chat.chrome.usage.value = usage
}

// ---- interrupt

/** The turn interrupted (BaudeWindow.interrupt): asked of its claude once; stopped outright if it doesn't end within
 * five seconds, or at once if its host says nothing's running. */
fun interrupt(chat: Chat) {
    val c = chat.chrome
    chat.stopAsked = true
    if (c.interrupting != 0L) return
    val token = SystemClock.elapsedRealtime()
    c.interrupting = token
    controlAnswer(chat.hostId, chat.sid, JSONObject().put("subtype", "interrupt")) { r, _ ->
        if (r != null && r.has("running") && !r.optBoolean("running") && c.interrupting == token) stopStuck(chat, false)
    }
    Mesh.later(5000) { if (c.interrupting == token) stopStuck(chat, true) }
}

/** An interrupted turn not over in time (ran) or with nothing running to end it: said, and its session stopped. */
private fun stopStuck(chat: Chat, ran: Boolean) {
    chat.chrome.interrupting = 0
    if (!isWorking(chat)) return
    val model = Mesh.session(chat.hostId, chat.sid)?.model?.ifBlank { null }
    val program = when (program(model)) {
        "pi" -> Wording.Programs.pi
        "apple" -> Wording.Programs.apple
        "codex" -> Wording.Programs.codex
        else -> Wording.Programs.claude
    }
    chat.said.value = if (ran) Wording.Interrupt.notStopped(program = program, seconds = 5) else Wording.Interrupt.notRunning(program = program)
    if (ran) Mesh.conn(chat.hostId)?.send(Msg.Stop(session = chat.sid).json())
}

/** A new chat on the host of one it no longer has (new_session_on): its folder the last used there, its model the
 * chat's if claude runs it; opened once the host has started it. */
fun newChatOn(chat: Chat, open: (String, String) -> Unit) {
    val at = chat.hostId
    val s = Mesh.session(at, chat.sid)
    val model = s?.model?.ifBlank { null }?.takeIf { program(it) == "claude" }
    val msg = Msg.Start(prompt = "", cwd = HomePrefs.folder(Mesh.app, at) ?: "~", model = model,
        effort = if (model != null) s?.effort?.ifBlank { null } else null, mode = "auto", draft = true,
        group = s?.group).json()
    Mesh.ask(at, msg) { r ->
        val id = Msg.Started.from(r).session?.id
        if (r.optString("op") == Op.STARTED && id != null) open(at, id)
        else chat.said.value = Wording.NewSession.hostRefused(host = Mesh.label(at), why = Msg.Error.from(r).said.orEmpty())
    }
}
