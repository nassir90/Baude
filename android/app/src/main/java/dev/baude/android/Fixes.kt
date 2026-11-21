package dev.baude.android

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** Ongoing Feedback's data (fixview.FixesPage's): the feedback and where each stands, read through the first host of
 * yours that's up and takes fixes (as it has the gh), and the notes kept on this phone, not filed yet. */
object Fixes {
    val ONGOING = listOf("ready", "working", "open", "filed", "saved")
    private const val POLL_WORKING_MS = 15_000L
    private const val POLL_MS = 60_000L

    var repo by mutableStateOf<String?>(null)
    var items by mutableStateOf<List<JSONObject>>(emptyList())
    var canWrite by mutableStateOf(false)
    var locations by mutableStateOf<List<String>>(emptyList())
    var readAt by mutableStateOf(0L)
    var loading by mutableStateOf(false)
    var busy by mutableStateOf(false)
    var error by mutableStateOf<String?>(null)
    var firstRead by mutableStateOf(true)
    var lastRead by mutableStateOf(0L)
    private var again = false
    private var shown = false

    fun host(): String? = Mesh.reached("fixes").firstOrNull { (h, _) -> Mesh.hosts.any { it.id == h } }?.first

    private fun file() = File(Mesh.app.filesDir, "fixes.json")

    /** What was read last time, shown while it's read again. */
    fun last() {
        if (shown || readAt != 0L || items.isNotEmpty()) return
        shown = true
        val o = try { JSONObject(file().readText()) } catch (_: Exception) { return }
        val a = o.optJSONArray("items") ?: return
        repo = o.str("repo")
        items = (0 until a.length()).mapNotNull { a.optJSONObject(it) }
        canWrite = o.optBoolean("can_write")
        locations = o.optJSONArray("locations")?.strings().orEmpty()
        lastRead = (o.optDouble("read", 0.0) * 1000).toLong()
    }

    private fun save() {
        try {
            file().writeText(JSONObject().put("repo", repo ?: JSONObject.NULL).put("items", JSONArray(items))
                .put("can_write", canWrite).put("locations", JSONArray(locations))
                .put("read", System.currentTimeMillis() / 1000.0).toString())
        } catch (_: Exception) {
        }
    }

    /** What a note is as an item of the list (fixview.note_item): its first line the title, the rest the body. */
    private fun noteItem(n: JSONObject): JSONObject {
        val text = n.optString("text")
        val first = text.lines().map { it.trim().trimStart('#').trim() }.firstOrNull { it.isNotEmpty() }.orEmpty()
        val title = if (first.length <= 90) first else first.take(89).trimEnd() + "…"
        val at = n.optLong("kept") / 1000.0
        val id = n.optString("id")
        return JSONObject().put("number", id).put("key", id).put("title", title)
            .put("body", text.substringAfter('\n', "").trim()).put("author", "").put("url", "").put("created", at)
            .put("updated", at).put("pr", JSONObject.NULL).put("thread", JSONArray()).put("rounds", 0)
            .put("working", JSONObject.NULL).put("here", false).put("error", JSONObject.NULL).put("status", "saved")
    }

    /** The issues and the notes, in the order they're listed (fixes.sort_key). */
    fun all(): List<JSONObject> {
        val noted = Outbox.listed().map(::noteItem)
        return (items + noted).sortedWith(compareBy({ ONGOING.indexOf(it.optString("status")).let { i -> if (i < 0) ONGOING.size else i } },
            { -it.optDouble("updated") }))
    }

    fun key(item: JSONObject): String = (item.opt("key")?.takeIf { it != JSONObject.NULL } ?: item.opt("number")).toString()

    /** Where its issue is, as a row names it (Baude#12); "" for a note not filed. */
    fun location(item: JSONObject): String {
        val where = item.str("issue_location").orEmpty()
        val n = item.opt("number")
        return if (where.isNotEmpty() && (n is Int || n is Long)) "${where.substringAfterLast('/')}#$n" else ""
    }

    /** What the feedback was read from, as the foot says it. */
    fun readFrom(): String = (locations.ifEmpty { listOfNotNull(repo) }).joinToString(", ") { it.substringAfterLast('/') }

    /** Whether its open PR is from this repository's fix/<key>, so a round can go on from it (fixes.ours). */
    fun ours(item: JSONObject): Boolean {
        val pr = item.optJSONObject("pr") ?: return false
        return pr.optString("state") == "OPEN" && pr.optString("head") == "fix/${key(item)}" && !pr.optBoolean("cross")
    }

    fun refresh() {
        val h = host() ?: return
        if (loading) return
        loading = true
        Mesh.ask(h, Msg.Fixes(what = "list").json()) { r ->
            loading = false
            readAt = System.currentTimeMillis()
            firstRead = false
            if (r.optString("op") == Op.ERROR) {
                error = Msg.Error.from(r).said.orEmpty().ifBlank { Wording.BotBrowser.hostSaidNo }
                return@ask
            }
            val f = Msg.FixesReply.from(r)
            error = f.error?.ifBlank { null }
            repo = f.repo
            val got = f.items
            if (got != null) {
                items = got
                canWrite = f.canWrite == true
                locations = f.locations.orEmpty()
                save()
            } else if (f.repo == null && error == null) {
                items = emptyList()
                canWrite = false
            }
        }
    }

    /** Something done, then what's on GitHub read again, a moment later too (a round's first comment takes a few
     * seconds). done runs if it worked. */
    fun run(what: String, item: JSONObject, text: String? = null, done: (() -> Unit)? = null) {
        val h = host() ?: return
        busy = true
        Mesh.ask(h, Msg.Fixes(what = what, item = item, text = text).json()) { r ->
            val said = if (r.optString("op") == Op.ERROR) Msg.Error.from(r).said.orEmpty() else Msg.FixesReply.from(r).error.orEmpty()
            if (said.isNotEmpty()) toast(said.lines().first().take(160)) else done?.invoke()
            busy = false
            refresh()
            if (!again) {
                again = true
                Mesh.later(6000) {
                    again = false
                    refresh()
                }
            }
        }
    }

    /** Read again while the page is shown: often while a round runs, else every minute (fixview.tick). */
    suspend fun poll() {
        while (true) {
            kotlinx.coroutines.delay(POLL_WORKING_MS)
            if (items.any { it.optString("status") == "working" } || System.currentTimeMillis() - readAt >= POLL_MS) refresh()
        }
    }
}
