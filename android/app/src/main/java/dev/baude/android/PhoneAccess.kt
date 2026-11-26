package dev.baude.android

import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** Who in the mesh may use what of this phone as a host (baudelib/access.py's, the same rules and the same ops: access,
 * access_set): a rule for each capability, everybody, nobody or the members chosen (a tunnel's origin), and a chat's
 * own rule for managing it (its meta's access), which goes before the host's. This app's own head (no origin) is never
 * refused. A rule not set is everybody's, but access's (these rules themselves), which is nobody's: only this app
 * changes them till members are chosen. A member whose tunnel isn't signed (no key pinned for it: verified false) is let
 * in by an everybody rule only. Kept in phone/access.json. Of the capabilities, the phone host has new agents
 * (start: spawn) and its agents as they are (manage); a member a rule refuses is answered access_denied. */
object PhoneAccess {
    val CAPABILITIES = listOf("shell", "browser", "providers", "model_files", "spawn", "manage", "kube", "databases", "access")
    val WHO = listOf("everybody", "nobody", "chosen")
    private val rules = HashMap<String, JSONObject>()
    private var file: File? = null

    @Synchronized fun start(root: File) {
        file = File(root, "access.json")
        val kept = try { JSONObject(file!!.readText()).optJSONObject("rules") } catch (_: Exception) { null } ?: JSONObject()
        for (cap in CAPABILITIES) rules[cap] = if (kept.has(cap)) clean(kept.optJSONObject(cap)) ?: JSONObject().put("who", "nobody")
            else JSONObject().put("who", if (cap == "access") "nobody" else "everybody")
    }

    /** A rule as it's kept (who; chosen's members, each once), or null for one that isn't. */
    fun clean(rule: JSONObject?): JSONObject? {
        val who = rule?.optString("who")?.takeIf { it in WHO } ?: return null
        if (who != "chosen") return JSONObject().put("who", who)
        val members = rule.optJSONArray("members") ?: JSONArray()
        return JSONObject().put("who", who).put("members", JSONArray((0 until members.length()).map { members.optString(it) }
            .filter(::isId).distinct().take(256)))
    }

    fun lets(rule: JSONObject?, member: String?): Boolean = when (rule?.optString("who") ?: "everybody") {
        "everybody" -> true
        "chosen" -> rule!!.optJSONArray("members")?.let { m -> (0 until m.length()).any { m.optString(it) == member } } == true
        else -> false
    }

    /** Of caps, the first head may not use (allowed's), if there's one. */
    fun firstRefused(head: Head, vararg caps: String): String? = caps.firstOrNull { !allowed(head, it) }

    /** May head use cap? A chat's own rule (meta: its meta) goes before the host's for manage. */
    @Synchronized fun allowed(head: Head, cap: String, meta: JSONObject? = null): Boolean {
        val member = (head.origin ?: return true).takeIf { head.verified }
        val own = meta?.optJSONObject("access")
        return if (cap == "manage" && own != null) lets(own, member) else lets(rules[cap], member)
    }

    fun refused(req: Any?, cap: String): JSONObject = WireCheck.refused(WireCheck.DENIED, req, named(cap)).put("denied", cap)

    /** cap as every app names it (accessview's). */
    fun named(cap: String): String = Wording.byKey[COPY[cap] ?: "access.$cap"] ?: cap
    private val COPY = mapOf("shell" to "kube.shell", "providers" to "deps.providers", "kube" to "window.kubernetes")

    /** cap's rule set and kept: false if it isn't one. */
    @Synchronized fun set(cap: String, rule: JSONObject?): Boolean {
        val r = clean(rule) ?: return false
        if (cap !in CAPABILITIES) return false
        rules[cap] = r
        file?.writeText(JSONObject().put("rules", JSONObject(rules.mapValues { it.value })).toString())
        return true
    }

    /** As the wire's AccessState has it, for head (extra: ids a chat's rule names). */
    @Synchronized fun state(head: Head, extra: List<String> = emptyList()): JSONObject {
        val named = rules.values.flatMap { r -> r.optJSONArray("members")?.let { m -> (0 until m.length()).map { m.optString(it) } }.orEmpty() }
        val ids = (PhoneLink.members() + named + extra).filter { it != PhoneHost.id }.distinct()
        return JSONObject().put("host", PhoneHost.name)
            .put("rules", JSONObject().apply { for (cap in CAPABILITIES) put(cap, JSONObject(rules.getValue(cap).toString())) })
            .put("members", JSONArray(ids.map { JSONObject().put("id", it).put("name", PhoneLink.nameOf(it)) }))
            .put("editable", allowed(head, "access"))
    }
}
