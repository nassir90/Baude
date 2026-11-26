package dev.baude.android

import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** Who in the mesh may use what of this phone as a host (baudelib/access.py's, the same rules and the same ops: access,
 * access_set): rules for each capability, everybody, nobody or the members chosen (a tunnel's origin), a capability
 * whose ops part into reading and changing (SPLIT) having two, <cap>_read and <cap>_write (RULES: every rule's name),
 * write letting a member read too, and the rules kept so read's lets in everyone write's does (keptTogether); and a
 * chat's own rules for managing it (its meta's access, AccessChat's), which go before the host's. This app's own head
 * (no origin) is never refused. A rule not set is everybody's, but access_write's (changing these rules), which is
 * nobody's: only this app changes them till members are chosen. A member whose tunnel isn't signed (no key pinned for
 * it: verified false) is let in by an everybody rule only. Kept in phone/access.json; a rule kept before its capability
 * was split is both of its sides' (Access Rules': write's). Of the capabilities, the phone host has new agents (start:
 * spawn) and its agents as they are (manage, each op's side: side); a member a rule refuses is answered access_denied. */
object PhoneAccess {
    val CAPABILITIES = listOf("shell", "browser", "providers", "model_files", "spawn", "manage", "kube", "databases",
        "subscriptions", "access")
    val SPLIT = listOf("browser", "manage", "kube", "subscriptions", "access")
    val RULES = CAPABILITIES.flatMap { rulesOf(it) }
    val WHO = listOf("everybody", "nobody", "chosen")
    private val rules = HashMap<String, JSONObject>()
    private var file: File? = null

    /** cap's rules: its read and write, or the one. */
    fun rulesOf(cap: String): List<String> = if (cap in SPLIT) listOf("${cap}_read", "${cap}_write") else listOf(cap)

    /** The capability a rule's for: kube_write's kube. */
    fun capability(rule: String): String = rule.removeSuffix("_read").removeSuffix("_write")

    /** The rule each of a chat's ops this host takes is checked for, by its fields (access.OPS'). */
    fun side(op: String, msg: JSONObject): String = when (op) {
        Op.ATTACH -> if (Msg.Attach.from(msg).alwaysAllow.isNullOrEmpty()) "manage_read" else "manage_write"
        Op.MEMORY -> if (msg.has("text") && !msg.isNull("text") || msg.has("seen") && !msg.isNull("seen")) "manage_write"
            else "manage_read"
        Op.DETACH, Op.TRANSCRIPT, Op.CHAT_SIZE, Op.SUBAGENTS, Op.SENDERS, Op.BOT_PICTURE, Op.READ_FILE, Op.LIST_DIR,
        Op.CATALOGUE -> "manage_read"
        else -> "manage_write" // (input, stop, rename, archive, unarchive, delete, always_allow, bot)
    }

    @Synchronized fun start(root: File) {
        file = File(root, "access.json")
        val kept = try { JSONObject(file!!.readText()).optJSONObject("rules") } catch (_: Exception) { null } ?: JSONObject()
        for (r in RULES) {
            val cap = capability(r)
            val was = if (kept.has(r)) r else if (kept.has(cap) && (cap != "access" || r == "access_write")) cap else null
            rules[r] = if (was != null) clean(kept.optJSONObject(was)) ?: nobody()
                else JSONObject().put("who", if (r == "access_write") "nobody" else "everybody")
        }
        for (cap in SPLIT) keptTogether(rules, "${cap}_write")
    }

    private fun nobody() = JSONObject().put("who", "nobody")

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

    private fun ids(rule: JSONObject) = rule.optJSONArray("members")?.let { m -> (0 until m.length()).map { m.optString(it) } }.orEmpty()

    /** A rule letting in everyone a or b does. */
    private fun wider(a: JSONObject, b: JSONObject): JSONObject {
        val wa = a.optString("who")
        val wb = b.optString("who")
        if (wa == "everybody" || wb == "everybody") return JSONObject().put("who", "everybody")
        if (wa == "nobody") return JSONObject(b.toString())
        if (wb == "nobody") return JSONObject(a.toString())
        return JSONObject().put("who", "chosen").put("members", JSONArray((ids(a) + ids(b)).distinct().take(256)))
    }

    /** A rule letting in only those both a and b do. */
    private fun narrower(a: JSONObject, b: JSONObject): JSONObject {
        val wa = a.optString("who")
        val wb = b.optString("who")
        if (wa == "everybody" || wb == "nobody") return JSONObject(b.toString())
        if (wb == "everybody" || wa == "nobody") return JSONObject(a.toString())
        val both = ids(a).filter { it in ids(b) }
        return if (both.isEmpty()) nobody() else JSONObject().put("who", "chosen").put("members", JSONArray(both))
    }

    /** rules (a capability's, by name; base: what one not in rules is) with name's just set, its other side made to
     * match: read widened to let in everyone write does, write narrowed to those read does. Changed in place. */
    private fun keptTogether(rules: MutableMap<String, JSONObject>, name: String, base: Map<String, JSONObject> = emptyMap()) {
        val cap = capability(name)
        if (cap == name) return
        val read = "${cap}_read"
        val write = "${cap}_write"
        fun get(r: String) = rules[r] ?: base[r] ?: JSONObject().put("who", "everybody")
        if (name == write && rules[write] != null) {
            val r = wider(get(read), rules.getValue(write))
            if (r.toString() != get(read).toString()) rules[read] = r
        } else if (name == read && rules[read] != null) {
            val w = narrower(get(write), rules.getValue(read))
            if (w.toString() != get(write).toString()) rules[write] = w
        }
    }

    /** A chat's own rules (its meta's access), by name: one kept before Agent Management was split, both sides'. */
    fun own(meta: JSONObject?): MutableMap<String, JSONObject> {
        val a = meta?.optJSONObject("access") ?: return LinkedHashMap()
        if (a.has("who")) return rulesOf("manage").associateWithTo(LinkedHashMap()) { clean(a) ?: nobody() }
        return rulesOf("manage").filter { a.has(it) }.associateWithTo(LinkedHashMap()) { clean(a.optJSONObject(it)) ?: nobody() }
    }

    /** Of rules, the first head may not use (allowed's), if there's one. */
    fun firstRefused(head: Head, vararg names: String): String? = names.firstOrNull { !allowed(head, it) }

    /** May head use what rule name guards? A chat's own rules (meta: its meta) go before the host's for manage's; one
     * let write is let read. */
    @Synchronized fun allowed(head: Head, name: String, meta: JSONObject? = null): Boolean {
        val member = (head.origin ?: return true).takeIf { head.verified }
        val mine = if (capability(name) == "manage") own(meta) else emptyMap()
        val sides = if (name.endsWith("_read")) listOf(name, capability(name) + "_write") else listOf(name)
        return sides.any { lets(mine[it] ?: rules[it], member) }
    }

    fun refused(req: Any?, name: String): JSONObject = WireCheck.refused(WireCheck.DENIED, req, named(name)).put("denied", name)

    /** A rule as every app names it (accessview's): its capability's name, and Read or Write for a split one's. */
    fun named(name: String): String {
        val cap = capability(name)
        val said = Wording.byKey[COPY[cap] ?: "access.$cap"] ?: cap
        return if (cap == name) said
            else Wording.Access.side(capability = said, side = if (name.endsWith("_read")) Wording.Access.read else Wording.Access.write)
    }
    private val COPY = mapOf("shell" to "kube.shell", "providers" to "deps.providers", "kube" to "window.kubernetes",
        "subscriptions" to "subscriptions.title")

    /** Rule name set and kept, its other side kept with it: false if it isn't one. */
    @Synchronized fun set(name: String, rule: JSONObject?): Boolean {
        val r = clean(rule) ?: return false
        if (name !in RULES) return false
        rules[name] = r
        keptTogether(rules, name)
        file?.writeText(JSONObject().put("rules", JSONObject(rules.mapValues { it.value })).toString())
        return true
    }

    /** A chat's own rule name (manage_read or manage_write) set in its meta (rule null: the host's again), its other
     * side kept with it: false if it isn't one. */
    @Synchronized fun setOwn(meta: JSONObject, name: String, rule: JSONObject?): Boolean {
        if (name !in rulesOf("manage")) return false
        val mine = own(meta)
        if (rule == null) mine.remove(name) else {
            mine[name] = clean(rule) ?: return false
            keptTogether(mine, name, rules)
        }
        if (mine.isEmpty()) meta.remove("access") else meta.put("access", JSONObject(mine as Map<*, *>))
        return true
    }

    /** A chat's own rules as the wire's AccessChat has them, or null for none. */
    fun ownJson(meta: JSONObject?): JSONObject? = own(meta).takeIf { it.isNotEmpty() }?.let { JSONObject(it as Map<*, *>) }

    /** As the wire's AccessState has it, for head (extra: ids a chat's rules name). */
    @Synchronized fun state(head: Head, extra: List<String> = emptyList()): JSONObject {
        val named = rules.values.flatMap { ids(it) }
        val ids = (PhoneLink.members() + named + extra).filter { it != PhoneHost.id }.distinct()
        return JSONObject().put("host", PhoneHost.name)
            .put("rules", JSONObject().apply { for (r in RULES) put(r, JSONObject(rules.getValue(r).toString())) })
            .put("members", JSONArray(ids.map { JSONObject().put("id", it).put("name", PhoneLink.nameOf(it)) }))
            .put("editable", allowed(head, "access_write"))
    }
}
