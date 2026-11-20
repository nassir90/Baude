package dev.baude.android

import androidx.compose.runtime.mutableStateOf
import org.json.JSONArray
import org.json.JSONObject

/** An artifact a chat published (its Artifact call and what it answered), or linked to, as Baude's Artifacts page
 * lists it (ArtifactChip): its link, title, version and icon. */
data class Artifact(
    val url: String,
    val title: String,
    val version: String? = null,
    val icon: String? = null,
    val description: String? = null,
    val path: String? = null,
)

/** A subagent a chat started (its Agent call): what it's for, its kind, what it was told, how it stands, and the id
 * its own transcript's named by (subagents/agent-<id>.jsonl), once its call's answered. */
class Subagent(val toolUseId: String, val description: String, val kind: String, val prompt: String) {
    val status = mutableStateOf("running") // (running, completed, failed, killed, interrupted)
    val agentId = mutableStateOf<String?>(null)
    val activity = mutableStateOf<String?>(null)
    val usage = mutableStateOf<JSONObject?>(null)
}

/** Is it claude's subagent tool (Agent, or Task as it was)? */
fun isAgent(name: String) = name == "Agent" || name == "Task"

val ARTIFACT_URL = Regex("""https://claude\.ai/(?:code/)?artifact/[A-Za-z0-9_-]+""")
private val PUBLISHED = Regex("""Published (\S+) at (https://claude\.ai/(?:code/)?artifact/[A-Za-z0-9_-]+)""")

/** What an Artifact call published, from its result, as Baude reads it (artifact_from_call): nothing if it didn't. */
fun artifact(use: JSONObject, text: String): Artifact? {
    val input = use.optJSONObject("input") ?: JSONObject()
    val m = PUBLISHED.find(text)
    val url = if (input.optString("type_url").isNotBlank()) ARTIFACT_URL.find(text)?.value else m?.groupValues?.get(2)
    url ?: return null
    val path = m?.groupValues?.get(1) ?: input.optString("file_path").ifBlank { null }
    val file = path?.substringAfterLast('/')?.substringBeforeLast('.')
    val title = input.optString("title").ifBlank { null }
        ?: file?.replace(Regex("[-_]+"), " ")?.trim()?.replaceFirstChar(Char::uppercase)?.ifBlank { null } ?: Wording.Artifacts.untitled
    return Artifact(url, title, Regex("""\(Version (\d+)""").find(text)?.groupValues?.get(1),
        Regex("""Icon: "([\w-]+)"""").find(text)?.groupValues?.get(1) ?: input.optString("icon").ifBlank { null },
        input.optString("description").ifBlank { null }, path)
}

/** A tag's text in a <task-notification>, trimmed: null if it isn't there. */
private fun taskTag(t: String, name: String) =
    Regex("<$name>(.*?)</$name>", RegexOption.DOT_MATCHES_ALL).find(t)?.groupValues?.get(1)?.trim()

/** A background task's end, as claude tells it (<task-notification>): its task's id, its call's, how it ended. */
fun taskNote(t: String): JSONObject = JSONObject().put("task_id", taskTag(t, "task-id"))
    .put("tool_use_id", taskTag(t, "tool-use-id")).put("status", taskTag(t, "status"))

/** A background task's end as one line, as Baude's chat and Record say it (widgets.task_note_line): "Background
 * command · what it was — finished (exit 0)", or "Agent · …" for a subagent's. */
fun taskLine(t: String): String {
    val status = taskTag(t, "status").orEmpty().ifBlank { "completed" }
    val summary = unescape(taskTag(t, "summary").orEmpty().ifBlank { Wording.TranscriptNotes.taskFinished })
    val what = if (summary.startsWith("Agent") || summary.startsWith("Subagent")) Wording.Common.agent
        else Wording.TaskNotes.backgroundCommand
    val quoted = Regex("^[^\"]*\"(.*)\"", RegexOption.DOT_MATCHES_ALL).find(summary)?.groupValues?.get(1)
    var desc = (quoted ?: summary).trim().lines().first().ifEmpty { Wording.TaskNotes.unnamed }
    if (desc.length > 80) desc = desc.take(79) + "…"
    var ending = when (status) {
        "completed" -> Wording.TaskNotes.finished
        "failed", "error" -> Wording.TaskNotes.failed
        "killed" -> Wording.TaskNotes.stopped
        else -> status
    }
    Regex("exit code (\\d+)").find(summary)?.let { ending = Wording.TaskNotes.endingExit(ending = ending, code = it.groupValues[1]) }
    return "$what · $desc — $ending"
}

/** Text with its HTML character references read (html.unescape's, for the ones claude's notes have): named ones and
 * numbered ones, in one pass. */
private fun unescape(s: String): String = Regex("&(amp|lt|gt|quot|apos|#[0-9]+|#[xX][0-9a-fA-F]+);").replace(s) { m ->
    when (val e = m.groupValues[1]) {
        "amp" -> "&"
        "lt" -> "<"
        "gt" -> ">"
        "quot" -> "\""
        "apos" -> "'"
        else -> (if (e[1] == 'x' || e[1] == 'X') e.substring(2).toIntOrNull(16) else e.substring(1).toIntOrNull())
            ?.takeIf { Character.isValidCodePoint(it) }?.let { String(Character.toChars(it)) } ?: m.value
    }
}

/** A tool result's text: a string, or its text blocks'. */
fun resultText(content: Any?): String = when (content) {
    is String -> content
    is JSONArray -> content.list().filter { it.optString("type") == "text" }.joinToString("\n") { it.optString("text") }
    else -> ""
}
