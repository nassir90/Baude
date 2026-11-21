package dev.baude.android

import android.os.Handler
import android.os.Looper
import androidx.compose.runtime.mutableIntStateOf
import org.json.JSONObject

// Signal on the phone is the desktop's halted Signal: its links are drawn from the names the folder's glossary already
// has (signal_names), kept per chat. What looks names up runs only where Baude does.

/** The wire's name, as the Signal page's ref has it: its data (what was found) and its locations, never null. */
val Msg.SignalRef.found: JSONObject get() = data ?: JSONObject()
val Msg.SignalRef.places: List<JSONObject> get() = locations.orEmpty()

/** lookup.REF_KINDS' plurals: what a group of names is headed. */
val KINDS = mapOf("file" to Wording.Lookup.files, "commit" to Wording.Lookup.commits,
    "branch" to Wording.Lookup.branches, "pr" to Wording.Lookup.pullRequests, "env" to Wording.Lookup.envVars,
    "constant" to Wording.Lookup.constants, "symbol" to Wording.Lookup.codeSymbols, "config" to Wording.Lookup.configKeys,
    "service" to Wording.Lookup.services, "k8s" to Wording.Lookup.kubernetesObjects, "package" to Wording.Lookup.packages,
    "cli" to Wording.Lookup.commands, "external" to Wording.Lookup.externalTools, "tool" to Wording.Lookup.tools,
    "subagent" to Wording.Lookup.subagentTypes, "skill" to Wording.Lookup.skills, "route" to Wording.Lookup.httpRoutes,
    "db" to Wording.Signal.databaseTables)

/** lookup.REF_KINDS' singulars. */
val KIND_ONE = mapOf("file" to Wording.Lookup.file, "commit" to Wording.Lookup.commit, "branch" to Wording.Lookup.branch,
    "pr" to Wording.Lookup.pullRequest, "env" to Wording.Lookup.envVar, "constant" to Wording.Lookup.constant,
    "symbol" to Wording.Lookup.codeSymbol, "config" to Wording.Lookup.configKey, "service" to Wording.Lookup.service,
    "k8s" to Wording.Lookup.kubernetesObject, "package" to Wording.Lookup.`package`, "cli" to Wording.Common.command,
    "external" to Wording.Lookup.externalTool, "tool" to Wording.Lookup.tool, "subagent" to Wording.Lookup.subagentType,
    "skill" to Wording.Lookup.skill, "route" to Wording.Lookup.httpRoute)

/** The Signal page's groups, in its order: looking up, the kinds up to code symbols, the database's, then the rest. */
val GROUP_ORDER = listOf("pending", "file", "commit", "branch", "pr", "env", "constant", "symbol", "db", "config",
    "service", "k8s", "package", "cli", "external", "tool", "subagent", "skill", "route")

/** SignalPage.ICONS' names and colours are the page's; these are what a status is called. */
const val RESOLVED = "resolved"
const val AMBIGUOUS = "ambiguous"
const val PENDING = "pending"
const val NOT_FOUND = "not_found"
const val STALE = "stale"

// ------------------------------------------------------------------------------------------------------ per chat --

/** Each chat's two Signal switches, (on, links), as the desktop keeps them (BaudeWindow.chat_signal): a new chat's are
 * (not activated, links shown); the last 500 changed are kept. On the phone `on` stays off: nothing here looks names
 * up. version changes as one is set: what's drawn from it is drawn again. */
object SignalChats {
    val version = mutableIntStateOf(0)
    private const val KEPT = 500

    private fun prefs() = Mesh.app.getSharedPreferences("signal", android.content.Context.MODE_PRIVATE)

    private fun all(): JSONObject = try { JSONObject(prefs().getString("chats", null) ?: "{}") } catch (_: Exception) { JSONObject() }

    fun get(hostId: String, sid: String): Pair<Boolean, Boolean> {
        val kept = all().optJSONArray("$hostId/$sid") ?: return false to true
        return (kept.length() == 2).let { if (it) kept.optBoolean(0) to kept.optBoolean(1) else false to true }
    }

    fun set(hostId: String, sid: String, on: Boolean, links: Boolean) {
        val now = all()
        val key = "$hostId/$sid"
        val rest = now.keys().asSequence().filter { it != key }.toList().takeLast(KEPT - 1) // (re-inserted: the newest)
        val out = JSONObject()
        for (k in rest) out.put(k, now.get(k))
        out.put(key, org.json.JSONArray().put(on).put(links))
        prefs().edit().putString("chats", out.toString()).apply()
        version.intValue++
    }

    /** Signal is off for the chat (the desktop's win.signal is None): nothing on, nothing shown, or no folder. */
    fun off(hostId: String, sid: String): Boolean {
        val (on, links) = get(hostId, sid)
        return !on && !links || Mesh.session(hostId, sid)?.cwd.isNullOrBlank()
    }
}

// ------------------------------------------------------------------------------------------------------- names --

/** What a folder's glossary has for a chat (signal_names), read for each chat when it's opened, when the Signal page
 * is shown and as a turn ends; version changes as an answer comes. */
object SignalNames {
    val version = mutableIntStateOf(0)
    private val main = Handler(Looper.getMainLooper())
    private class Held(val reply: Msg.SignalNamesReply, val by: Map<String, Msg.SignalRef>)
    private val held = HashMap<String, Held>()
    private val asked = HashSet<String>()
    private var chat: Chat? = null

    private fun key(hostId: String, cwd: String, sid: String) = "$hostId|$cwd|$sid"

    /** The reply a chat last got, or null while there's none. */
    fun reply(hostId: String, sid: String): Msg.SignalNamesReply? {
        version.intValue
        val cwd = Mesh.session(hostId, sid)?.cwd?.ifBlank { null } ?: return null
        return held[key(hostId, cwd, sid)]?.reply
    }

    /** The names a chat's glossary has, by how they're written (none while it's not read). */
    fun known(hostId: String, sid: String): Map<String, Msg.SignalRef> {
        version.intValue
        val cwd = Mesh.session(hostId, sid)?.cwd?.ifBlank { null } ?: return emptyMap()
        return held[key(hostId, cwd, sid)]?.by.orEmpty()
    }

    /** The chat's replies' texts, joined: where a name first comes in them says its place in the Signal page. */
    fun texts(): String = chat?.lines?.filter { it.kind == "bot" }?.joinToString("\n") { it.text }.orEmpty()

    /** Ask the host again for the chat's names. */
    fun read(hostId: String, sid: String) {
        val s = Mesh.session(hostId, sid)
        val cwd = s?.cwd?.ifBlank { null } ?: return
        if (!takes(hostId, "signal_names")) return
        val k = key(hostId, cwd, sid)
        main.post {
            val msg = Msg.SignalNames(cwd = cwd, session = sid).json()
            s.cliId?.ifBlank { null }?.let { msg.put("cli_id", it) }
            Mesh.ask(hostId, msg) { r ->
                main.post {
                    asked.remove(k)
                    val got = if (r.optString("op") == Op.ERROR) Msg.SignalNamesReply(error = said(r, null)) else Msg.SignalNamesReply.from(r)
                    held[k] = Held(got, got.refs.orEmpty().filter { !it.surface.isNullOrEmpty() }.associateBy { it.surface!! })
                    version.intValue++
                }
            }
        }
    }

    /** The names a chat's links are drawn from, by how they're written; null while they're off, or not read yet (the
     * first asks for them). */
    fun refsFor(place: ChatPlace): Map<String, Msg.SignalRef>? {
        val sid = place.sid ?: return null
        val cwd = place.cwd ?: return null
        if (SignalChats.off(place.hostId, sid) || !SignalChats.get(place.hostId, sid).second) return null
        val k = key(place.hostId, cwd, sid)
        val got = held[k]
        if (got == null && asked.add(k)) read(place.hostId, sid)
        return got?.by
    }

    /** The names a chat's replies backtick (Signal.strong): they're linked whatever they came to. */
    fun strong(): Set<String> {
        val c = chat ?: return emptySet()
        return c.lines.filter { it.kind == "bot" }.flatMap { l -> BACKTICKED.findAll(l.text).map { refKey(it.groupValues[1]) }.toList() }.toSet()
    }

    /** Hear the chat's turns end (its names are read again); the way to stop hearing. */
    fun listen(chat: Chat): () -> Unit {
        this.chat = chat
        val l: (String, JSONObject) -> Unit = { at, msg ->
            if (at == chat.hostId && msg.optString("op") == Op.TURN_ENDED && Msg.TurnEnded.from(msg).session.orEmpty() == chat.sid)
                read(chat.hostId, chat.sid)
        }
        Mesh.listeners.add(l)
        return {
            Mesh.listeners.remove(l)
            if (this.chat === chat) this.chat = null
        }
    }
}

private val BACKTICKED = Regex("`([^`\\n]+)`")

// ------------------------------------------------------------------------------------------------------- ports --

/** lookup.ref_key: the one spelling of a name as written: trimmed, an ellipsised hash without its dots. */
fun refKey(text: String): String =
    text.trim().replace(Regex("\\s+"), " ").replace(Regex("(?<=[0-9a-f]{7})(?:…|\\.\\.\\.)$"), "")

private val CLOUD_PROVIDERS = listOf("Cloudflare", "Vercel", "Netlify", "Supabase", "Firebase", "Shopify",
    "AWS", "Amazon Web Services", "GCP", "Google Cloud", "Google Cloud Platform", "Azure", "Microsoft Azure",
    "Heroku", "DigitalOcean", "Digital Ocean", "Fly.io", "Linode", "Akamai", "Fastly", "Hetzner", "OVH", "OVHcloud",
    "Render.com", "Railway.app", "PlanetScale", "Upstash", "Appwrite", "Auth0", "Okta", "Stripe", "Twilio", "SendGrid",
    "Mailgun", "Sentry", "Datadog", "New Relic", "GitHub Pages", "Oracle Cloud", "IBM Cloud", "Alibaba Cloud",
    "Backblaze").map { it.lowercase() }.toSet()

/** widgets.ref_cloud_provider: a cloud provider's name, or one and a word after it, written with a capital. */
fun refCloudProvider(surface: String): Boolean {
    val name = surface.trim()
    if (name == name.lowercase()) return false
    var head = name.lowercase()
    if (head in CLOUD_PROVIDERS) return true
    head = head.substringBeforeLast(' ', "")
    while (head.isNotEmpty()) {
        if (head in CLOUD_PROVIDERS) return true
        head = head.substringBeforeLast(' ', "")
    }
    return false
}

/** widgets.ref_hashlike: a commit hash as people write one, with letters and digits both. */
fun refHashlike(t: String) = Regex("[0-9a-f]{7,12}|[0-9a-f]{40}|[0-9a-f]{64}").matches(t) &&
    Regex("[a-f]").containsMatchIn(t) && Regex("\\d").containsMatchIn(t)

private const val ID = "[A-Za-z_]\\w*"
private val SYMBOLISH = Regex("($ID(?:\\[\\d*\\*?\\])?(?:\\.$ID(?:\\[\\d*\\*?\\])?)*)(\\([^()]*\\))?")

/** widgets.ref_symbolish: whether a name looks like code. */
private fun refSymbolish(t: String): Boolean {
    val m = SYMBOLISH.matchEntire(t) ?: return false
    val name = m.groupValues[1].replace(Regex("\\[[^\\]]*]"), "")
    return m.groupValues[2].isNotEmpty() || "." in name || Regex("[a-z0-9]_\\w|^_+[a-z]").containsMatchIn(name) ||
        Regex("[a-z]+[A-Z]\\w*").matches(name) || Regex("[A-Z][a-z0-9]+[A-Z]\\w*").matches(name)
}

private const val METHODS = "GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS"

/** widgets.ref_span's excerpt: code as it's written (a name with a value, a call, a subscript). */
private fun refExcerpt(t: String): Boolean = !refSymbolish(t) && Regex("[A-Za-z_]\\w").containsMatchIn(t) &&
    Regex("[=\\[\"'(]|->").containsMatchIn(t) && "://" !in t && !Regex("(?:-|\\$|(?:$METHODS)\\s).*").matches(t) &&
    t.replace(Regex("\\.\\.\\.|…|\\W"), "").length >= 3

/** widgets.ref_kind's command-line case: a flag, or a command with its flags. */
private fun refCli(t: String): Boolean {
    if (Regex("--[A-Za-z][\\w-]*").matches(t)) return true
    if (' ' !in t) return false
    val words = t.split(" ")
    return Regex("[\\w./~-]+").matches(words[0]) && (Regex("(?:[\\w./~-]*/)?(?:kubectl|kubectx|oc|helm)").matches(words[0]) ||
        words.drop(1).any { it.startsWith("-") })
}

/** widgets.ref_quiet: a span linked only once it's found: code, a command or flag, or a bare kebab-case name. */
fun refQuiet(surface: String): Boolean = refExcerpt(surface) || refCli(surface) ||
    Regex("[a-z0-9]+(?:-[a-z0-9]+)+").matches(surface)

/** lookup.ref_icon: the logo a link to this kind of thing has before it, or null. */
fun refIcon(kind: String?, data: JSONObject, roles: List<String> = emptyList()): String? {
    val live = data.optJSONObject("db")
    val state = data.str("state")
    if (kind == "pr" && state != null) {
        val issue = data.optBoolean("issue")
        val s = state.lowercase()
        return if (issue) (if (s == "closed") "issue_closed" else "issue") else when (s) {
            "merged" -> "pr_merged"
            "closed" -> "pr_closed"
            else -> "pr_open"
        }
    }
    if (live != null || roles.any { it == "table" || it == "column" }) return if (live?.optString("engine") == "ArangoDB") "arango" else "db"
    if (kind == "file") return if ("folder" in roles) "folder" else "file"
    return mapOf("commit" to "git", "branch" to "git", "pr" to "git", "k8s" to "k8s", "symbol" to "code", "constant" to "code",
        "env" to "env", "route" to "route", "config" to "config", "cli" to "cli", "package" to "package",
        "service" to "service", "tool" to "claude", "subagent" to "claude", "skill" to "claude", "external" to "external")[kind]
}

/** baude.ref_kind_label: what a name is, as a popover's title says it (none while it's looked up). */
fun refKindLabel(r: Msg.SignalRef): String? {
    if (r.status == PENDING) return null
    val d = r.found
    val live = d.optJSONObject("db")
    if (live != null) return if (live.str("table") == null) Wording.Databases.database else Wording.Databases.databaseTable
    val roles = r.places.mapNotNull { it.str("role") }.filter { it != "used" }
    if (r.kind == "symbol" && roles.isNotEmpty() && roles.all { it == "table" || it == "column" })
        return if (roles.all { it == "column" }) Wording.Signal.databaseColumn else Wording.Databases.databaseTable
    if (d.str("context") != null) return if (d.optBoolean("cluster_only")) Wording.Lookup.kubeconfigCluster else Wording.Lookup.kubeconfigContext
    if (d.optBoolean("issue")) return Wording.Lookup.issue
    return KIND_ONE[r.kind] ?: Wording.Common.name
}

/** The group a name is listed under (SignalPage.rebuild's group): looking up, the database's, or its kind. */
fun refGroup(r: Msg.SignalRef): String {
    if (r.status == PENDING) return "pending"
    val label = refKindLabel(r)
    return if (label == Wording.Databases.databaseTable || label == Wording.Signal.databaseColumn || label == Wording.Databases.database) "db"
        else r.kind.orEmpty()
}

/** baude.ref_status_text: the status line of a popover or a card (the late-lookup note needs a time the phone lacks). */
fun refStatusText(r: Msg.SignalRef): String = when (r.status) {
    PENDING -> Wording.Lookup.lookingItUp
    NOT_FOUND -> Wording.Signal.statusNotFound(reason = r.reason.orEmpty())
    STALE -> Wording.Signal.statusStale
    AMBIGUOUS -> Wording.Signal.statusMatches(n = r.places.size, why = r.reason.orEmpty())
    else -> r.summary.orEmpty().let { it + if (it.endsWith(".") || it.endsWith(")")) "" else "." }
}

private val WORKLOADS = setOf("Deployment", "StatefulSet", "DaemonSet", "Rollout", "CronJob", "Job", "ReplicaSet", "Pod")

/** baude.ref_run_names: the names a link's thing could be running under in a cluster. */
fun refRunNames(r: Msg.SignalRef): List<String> {
    val d = r.found
    if ((r.status != RESOLVED && r.status != AMBIGUOUS) || d.str("context") != null || d.optJSONObject("db") != null) return emptyList()
    if (r.kind == "k8s" && d.str("kind") !in WORKLOADS + "Service") return emptyList()
    val names = (if (r.kind == "k8s" || r.kind == "service") listOf(d.str("name"), r.surface, r.canonical) else emptyList()) +
        r.places.filter { it.str("role") == "folder" }.mapNotNull { it.str("path")?.trimEnd('/')?.substringAfterLast('/') }
    return names.filterNotNull().filter { Regex("[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?").matches(it) }.distinct()
}

/** lookup.ref_date: an ISO time as local '17 Oct 2026, 14:03'. */
fun refDate(iso: String?): String {
    if (iso.isNullOrEmpty()) return ""
    return try {
        val t = java.time.OffsetDateTime.parse(iso.replace("Z", "+00:00")).atZoneSameInstant(java.time.ZoneId.systemDefault())
        t.format(java.time.format.DateTimeFormatter.ofPattern("d MMM yyyy, HH:mm", java.util.Locale.ENGLISH))
    } catch (_: Exception) {
        iso
    }
}

private const val REF_EXT = "py|pyi|ts|tsx|js|jsx|mjs|cjs|rs|go|java|kt|rb|sh|bash|ya?ml|toml|json|md|css|scss|html|sql|tf|tsl|tsp|mmd|svg|ini|cfg|conf"
private const val WORD = "\\p{L}\\p{N}_"

/** widgets._REF_PLAIN: names worth looking up in plain text, only shapes that are rarely ordinary words. */
val REF_PLAIN = Regex("(?<![$WORD/.@`$-])(?:" +
    "(?:[A-Za-z_]\\w*(?:\\.[A-Za-z_]\\w*)*\\(\\))" +
    "|(?:(?:$METHODS)\\s+(?:…|\\.\\.\\.)?/[\\w./{}<>:~*-]*[\\w}>*-])" +
    "|(?:(?:PR|pull request) #\\d+|[\\w.-]+/[\\w.-]+#\\d+)" +
    "|(?:[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)" +
    "|(?:[0-9a-f]{7,40})" +
    "|(?:(?:[\\w.@+-]+/)*[\\w@+-][\\w.@+-]*\\.(?:$REF_EXT)(?::\\d+(?::\\d+)?)?)" +
    ")(?![$WORD/-])")

private val BARE_NUMBER = Regex("#\\d+")

/** widgets.ref_markup: inner as a Signal link, drawn by how far its lookup got (no reader decisions, no pill). */
private fun refMarkup(r: Msg.SignalRef, inner: String): String {
    val surface = r.surface.orEmpty()
    val icon = if (r.status == RESOLVED || r.status == AMBIGUOUS || r.status == STALE)
        refIcon(r.kind, r.found, r.places.mapNotNull { it.str("role") }).orEmpty() else ""
    return "<a kind=\"ref\" href=\"${surface.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;")}\"" +
        " status=\"${r.status}\" icon=\"$icon\" n=\"${r.places.size}\">$inner</a>"
}

/** widgets.ref_link: inner linked to what the chat's Signal knows about text, or null. */
fun refLink(text: String, inner: String, backticked: Boolean, place: ChatPlace?): String? {
    place ?: return null
    val refs = SignalNames.refsFor(place) ?: return null
    val key = refKey(text)
    if (refCloudProvider(key)) return null
    if (backticked && BARE_NUMBER.matches(key)) return null
    val r = refs[key] ?: return null
    if (r.kind in setOf("commit", "branch", "pr") && r.status == RESOLVED) return refMarkup(r, inner)
    val known = r.status == RESOLVED || r.status == AMBIGUOUS || r.status == STALE
    if (!(backticked || key in SignalNames.strong() || known)) return null
    if (r.found.optBoolean("plain")) return null
    if (!known && refQuiet(key) && !(r.kind == "k8s" && key in SignalNames.strong())) return null
    return refMarkup(r, inner)
}
