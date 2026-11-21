package dev.baude.android

import androidx.compose.runtime.mutableIntStateOf
import org.json.JSONObject

/** What @ lists of a chat's folder, made on its host (the catalogue op: its files, branches, Kubernetes objects,
 * pull requests...), kept per host and folder for CATALOGUE_TTL, asked again when a list shows it after that
 * (baude.HostFolder). */
object Catalogue {
    private const val TTL_MS = 120_000L
    private const val GONE_RETRY_MS = 5_000L
    private val GONE = setOf("the server isn't there", "the server's gone", "the server didn't answer")

    private class Held(var items: List<Ref> = emptyList(), var at: Long = 0, var busy: Boolean = false)

    private val held = HashMap<String, Held>()

    /** Bumped as an answer comes in: what shows the list reads it, and is drawn again. */
    val version = mutableIntStateOf(0)

    private fun key(hostId: String, cwd: String) = "$hostId\u0000$cwd"

    fun items(hostId: String, cwd: String): List<Ref> {
        version.intValue
        return held[key(hostId, cwd)]?.items.orEmpty()
    }

    fun busy(hostId: String, cwd: String): Boolean {
        version.intValue
        return held[key(hostId, cwd)]?.busy == true
    }

    /** The folder's names asked for if it's been a while. */
    fun soon(hostId: String, cwd: String) {
        if (cwd.isBlank() || !takes(hostId, "catalogue")) return
        val h = held.getOrPut(key(hostId, cwd)) { Held() }
        if (h.busy || System.currentTimeMillis() - h.at <= TTL_MS) return
        h.busy = true
        version.intValue++
        ask(hostId, cwd, h, wait = false)
    }

    private fun ask(hostId: String, cwd: String, h: Held, wait: Boolean) {
        Mesh.ask(hostId, Msg.Catalogue(cwd = cwd, wait = wait).json()) { r ->
            val reply = Msg.CatalogueReply.from(r)
            val ok = r.optString("op") == Op.CATALOGUE && reply.error.isNullOrBlank() && r.optJSONArray("items") != null
            if (ok) h.items = r.optJSONArray("items").list().mapNotNull { Ref.of(it, hostId) }
            if (ok && r.optBoolean("busy")) {
                version.intValue++
                ask(hostId, cwd, h, wait = true) // (not all made yet: what there is is listed, the rest waited for)
                return@ask
            }
            val gone = r.optString("op") == Op.ERROR && Msg.Error.from(r).error in GONE
            h.at = System.currentTimeMillis() - (if (gone) TTL_MS - GONE_RETRY_MS else 0)
            h.busy = false
            version.intValue++
        }
    }
}

/** One thing @ can name: a chat, or a name the chat's folder has (lookup.items' shape). */
class Ref(
    val surface: String, val label: String?, val canonical: String, val icon: String, val what: String,
    val summary: String, val where: String?, val at: Double?, val faint: Boolean, val later: Boolean,
) {
    companion object {
        /** An item of a host's catalogue, with the desktop's defaults for what a host leaves out. */
        fun of(o: JSONObject, hostId: String): Ref? {
            val surface = o.opt("surface") as? String ?: return null
            fun str(k: String, d: String) = (o.opt(k) as? String) ?: d
            val entry = o.optJSONObject("entry")
            val at = listOf(o.opt("at"), entry?.opt("at")).firstOrNull { it is Number }?.let { (it as Number).toDouble() }
                ?: entry?.optJSONObject("github")?.optString("updated")?.takeIf { it.isNotBlank() }?.let {
                    try { java.time.Instant.parse(it).epochSecond.toDouble() } catch (_: Exception) { null }
                }
            return Ref(surface, (o.opt("label") as? String)?.ifBlank { null }, str("canonical", ""), str("icon", "code"),
                str("what", "Name"), str("summary", ""), null, at, o.opt("faint") as? Boolean ?: false, false)
        }
    }
}
