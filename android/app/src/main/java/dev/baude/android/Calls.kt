package dev.baude.android

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import org.json.JSONArray
import org.json.JSONObject

/** One piece of what a call returned: its text (as code, tagged tag; a Read's lines numbered), or a subagent's report
 * (markdown). */
class Out(val text: String, val tag: String = "", val markdown: Boolean = false, val numbered: Boolean = false)

/** A tool call as its card has it (baude.ToolCard): its input, how it stands (running, background, done, error,
 * interrupted), what came back, the permission answered for it, and the background task it became. */
class Call(val id: String, val name: String, input: JSONObject = JSONObject()) {
    val base = name.substringAfterLast("__")
    var input by mutableStateOf(input)
    var state by mutableStateOf("running")
    var decision by mutableStateOf<String?>(null)
    val outputs = mutableStateListOf<Out>()
    val images = mutableStateListOf<String>() // (base64)
    var subagent by mutableStateOf(false)
    var inputOpen by mutableStateOf(false)
    var outputOpen by mutableStateOf(false)
    private var report: String? = null
    private var taskId: String? = null
    private var taskType: String? = null
    private var taskStatus: String? = null

    /** Its result, as ToolCard.set_result takes it. */
    fun result(text: String, isError: Boolean, pictures: List<String>) {
        if (taskId == null) boundBy(text)
        if (isAgent(base) && text.isNotEmpty() && !text.startsWith("Async agent launched")) {
            if (text.trim() != report) {
                report = text.trim()
                outputs.add(Out(text, markdown = true))
            }
        } else if (text.isNotEmpty() && !saidNothing(base, text, isError)) {
            outputs.add(Out(cut(text), numbered = base == "Read" && input.optString("file_path").isNotBlank()))
        }
        images.addAll(pictures)
        state = if (isError) "error" else if (taskId != null && taskStatus == "running") "background" else "done"
    }

    /** A result that only says it's a task started (task_from_result). */
    private fun boundBy(text: String) {
        BACKGROUND_COMMAND.find(text)?.let { bind(it.groupValues[1], "local_bash"); return }
        if ("Async agent launched" in text) AGENT_ID.find(text)?.let { bind(it.groupValues[1], "local_agent") }
    }

    /** It became a background task (a command or a subagent). */
    fun bind(id: String, type: String) {
        taskId = id
        taskType = type
        taskStatus = "running"
        if (type == "local_agent") subagent = true
        if (state == "done") state = "background"
    }

    /** The task ended (ToolCard.finish_task): its report, or a command's summary, and how it ended. */
    fun finish(status: String?, summary: String?, type: String?) {
        if (taskId != null && state != "running" && state != "background" && state != "interrupted") return
        val how = status?.ifBlank { null } ?: "completed"
        val kind = type ?: taskType ?: if (isAgent(base)) "local_agent" else "local_bash"
        if (kind == "local_agent") subagent = true
        if (!summary.isNullOrBlank() && kind != "local_bash" && summary.trim() != report) {
            report = summary.trim()
            outputs.add(Out(summary, markdown = true))
        } else if (kind == "local_bash" && !summary.isNullOrBlank()) outputs.add(Out(cut(summary), tag = how))
        taskStatus = how
        state = if (how == "completed") "done" else "error"
    }

    /** A server tool's result (a web search): only how it went. */
    fun served(isError: Boolean) {
        state = if (isError) "error" else "done"
    }
}

/** A chat's calls by id, and which groups of them are opened (by the first call's id). */
class ChatCalls {
    val byId = mutableStateMapOf<String, Call>()
    val opened = mutableStateMapOf<String, Boolean>()
    val autoOpened = HashMap<String, String>() // (a group: what it was last opened by itself for)
}

private val BACKGROUND_COMMAND = Regex("""Command running in background with ID: (\S+?)\.?\s+Output is being written to: (\S+)""")
private val AGENT_ID = Regex("""agentId: (\w+)""")

private fun cut(text: String) = if (text.length < 200_000) text
    else text.take(200_000) + "\n" + Wording.ToolCalls.cutChars(n = text.length)

private val FILE_WRITTEN = Regex("""(?:The file (?:(?! \()[^\n])+ has been updated successfully\.|File created successfully at: (?:(?! \()[^\n])+)(?:\s+\(file state is current in your context[^()\n]*\))?""")
private val SKILL_LAUNCHED = Regex("""Launching skill: [^\n]+""")

/** Is a call's whole result only that it went through (said_nothing): no Output to open. */
fun saidNothing(base: String, text: String, isError: Boolean): Boolean {
    if (isError) return false
    val t = text.trim()
    return base in setOf("Edit", "MultiEdit", "Write") && FILE_WRITTEN.matches(t) || base == "Skill" && SKILL_LAUNCHED.matches(t)
}

/** The mesh's tools that start a subagent on a host or message a chat there, which Baude draws as cards of their own. */
val MESH_CHAT_TOOLS = setOf("mcp__mesh__start_subagent", "mcp__mesh__message_chat")

/** A call's input line, as Baude's summarize_tool makes it: Claude's description, or what it touches. */
fun summary(name: String, input: JSONObject?): String {
    input ?: return ""
    fun first(v: String) = v.trim().lines().firstOrNull().orEmpty().take(120)
    val qs = input.optJSONArray("questions")
    if (name == "AskUserQuestion" && qs != null && qs.length() > 0 && qs.optJSONObject(0) != null) {
        val answers = input.optJSONObject("answers")
        if (answers != null && answers.length() > 0) return (0 until qs.length()).mapNotNull { qs.optJSONObject(it) }.filter { answers.has(it.optString("question")) }
            .joinToString(" · ") { q -> "${(q.opt("header") as? String)?.ifEmpty { null } ?: q.optString("question")}: ${answers.opt(q.optString("question"))}" }
        return first(qs.optJSONObject(0)!!.optString("question")) + if (qs.length() > 1) " (+${qs.length() - 1} more)" else ""
    }
    fun text(k: String) = input.opt(k) as? String
    if (text("command") != null && text("description").orEmpty().isBlank()) return ""
    for (k in listOf("description", "command", "file_path", "notebook_path", "pattern", "url", "query", "prompt", "path", "skill"))
        text(k)?.takeIf { it.isNotBlank() }?.let { return first(it) }
    return ""
}

/** Python's str.splitlines for the breaks that matter. */
fun splitLines(s: String): List<String> {
    if (s.isEmpty()) return emptyList()
    val out = ArrayList<String>()
    var start = 0
    var i = 0
    while (i < s.length) {
        val ch = s[i]
        if (ch == '\n' || ch == '\r') {
            out.add(s.substring(start, i))
            if (ch == '\r' && i + 1 < s.length && s[i + 1] == '\n') i++
            start = i + 1
        }
        i++
    }
    if (start < s.length) out.add(s.substring(start))
    return out
}

val LANG_BY_EXT = mapOf(".py" to "python", ".js" to "javascript", ".ts" to "typescript", ".sh" to "bash", ".rs" to "rust",
    ".go" to "go", ".json" to "json", ".md" to "markdown", ".html" to "html", ".css" to "css", ".c" to "c", ".h" to "c",
    ".cpp" to "cpp", ".java" to "java", ".rb" to "ruby", ".toml" to "toml", ".yaml" to "yaml", ".yml" to "yaml",
    ".sql" to "sql", ".tsx" to "tsx", ".jsx" to "jsx")

/** A line of a call, a result, a task's end or a stop taken into the chat's lines and calls (the kinds parse gives). */
fun took(calls: ChatCalls, lines: MutableList<Line>, line: Line) {
    when (line.kind) {
        "call" -> {
            val o = JSONObject(line.text)
            val id = o.optString("id")
            calls.byId[id] = Call(id, o.optString("name"), o.optJSONObject("input") ?: JSONObject())
            lines.add(Line("call", id, line.uuid))
        }
        "result" -> {
            val o = try { JSONObject(line.text) } catch (_: Exception) { return }
            val call = calls.byId[o.optString("id")] ?: return
            if (o.optBoolean("server")) call.served(o.optBoolean("is_error"))
            else call.result(o.optString("text"), o.optBoolean("is_error"), o.optJSONArray("images").list().map { it.optString("data") })
        }
        "task" -> {
            val o = try { JSONObject(line.text) } catch (_: Exception) { return }
            val call = calls.byId[o.optString("tool_use_id")]
            if (call != null) call.finish(o.optString("status"), o.optString("result").ifBlank { o.optString("summary") }, null)
            else lines.add(Line("tasknote", line.text, line.uuid))
        }
        "stopped" -> {
            val since = lines.takeLastWhile { it.kind != "you" }
            val cut = since.any { l -> l.kind == "call" && calls.byId[l.text]?.state.let { it == "running" || it == "interrupted" } }
            if (!cut) lines.add(Line("note", Wording.Meta.interrupted, line.uuid))
        }
    }
}

/** The calls a chat's host says aren't going on settled: with its session idle or stopped, those running never
 * will be (stopped: nor those in the background). */
fun settle(calls: ChatCalls, state: String?) {
    if (state != "idle" && state != "stopped") return
    for (call in calls.byId.values) {
        if (call.state == "running" || state == "stopped" && call.state == "background") call.state = "interrupted"
    }
}
