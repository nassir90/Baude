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
        ?: file?.replace(Regex("[-_]+"), " ")?.trim()?.replaceFirstChar(Char::uppercase)?.ifBlank { null } ?: "Artifact"
    return Artifact(url, title, Regex("""\(Version (\d+)""").find(text)?.groupValues?.get(1),
        Regex("""Icon: "([\w-]+)"""").find(text)?.groupValues?.get(1) ?: input.optString("icon").ifBlank { null },
        input.optString("description").ifBlank { null }, path)
}

/** A background task's end, as claude tells it (<task-notification>): its task's id, its call's, how it ended. */
fun taskNote(t: String): JSONObject {
    fun tag(name: String) = Regex("<$name>(.*?)</$name>", RegexOption.DOT_MATCHES_ALL).find(t)?.groupValues?.get(1)?.trim()
    return JSONObject().put("task_id", tag("task-id")).put("tool_use_id", tag("tool-use-id")).put("status", tag("status"))
}

/** A tool result's text: a string, or its text blocks'. */
fun resultText(content: Any?): String = when (content) {
    is String -> content
    is JSONArray -> content.list().filter { it.optString("type") == "text" }.joinToString("\n") { it.optString("text") }
    else -> ""
}
