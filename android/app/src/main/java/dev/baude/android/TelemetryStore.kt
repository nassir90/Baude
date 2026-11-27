package dev.baude.android

import android.app.ActivityManager
import android.content.Context
import android.os.Process
import android.system.Os
import android.system.OsConstants
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit

/** This phone's host's telemetry (baudelib/telemetry.py, the same): what this app saw done in chats (the telemetry
 * op's events), its own turns' ends, samples of the phone and this app's process every SAMPLE_S while it's on, read
 * back over a stretch by telemetry_read and switched by telemetry_set. Kept in the app's folder, phone/telemetry/
 * <events|metrics>/YYYY-MM-DD.jsonl (this phone's time) and phone/telemetry/state.json. The phone has no Python: it
 * profiles nothing. Nothing here is sent anywhere. */
object TelemetryStore {
    private const val SAMPLE_S = 5L
    private const val LIMIT = 5000
    private const val ERRORS_KEPT = 1000 // (errors kept: the latest by when they last happened)
    private val KEEP = mapOf("events" to 400L, "metrics" to 30L, "profiles" to 30L, "errors" to 90L)
    private val KINDS = setOf("chat_opened", "chat_front", "chat_left", "turn_sent", "turn_ended")
    private val APPS = setOf("baude", "record", "phone")
    /** A turn's tokens, each kind apart (telemetry.TOKENS), and usage's names for an event's tokens (telemetry._KINDS). */
    val TOKENS = arrayOf("tokens_input", "tokens_cache_write", "tokens_cache_read", "tokens_out")
    private val NAMES = mapOf("tokens_input" to "input", "tokens_cache_write" to "cache_write",
        "tokens_cache_read" to "cache_read", "tokens_out" to "output", "tokens_in" to "input_with_cache")
    private val work = Executors.newSingleThreadScheduledExecutor { r -> Thread(r, "telemetry").apply { isDaemon = true } }
    private var sampling: ScheduledFuture<*>? = null
    private var root: File? = null
    private var hostId = ""
    private var pruned: LocalDate? = null
    private var cpuBefore: Pair<Long, Long>? = null // (when, this process's CPU ms: the sample before's)
    @Volatile var on = true
        private set
    @Volatile private var front: Pair<String, String>? = null // (this phone's app's chat in front, as its last
    // chat_front said, till its chat_left: Info's front)

    private fun dir() = File(root!!, "telemetry")
    /** The version of this app's code, on each row it makes (telemetry.version's): its commit and wire version. */
    private val ver = JSONObject().put("commit", BuildConfig.COMMIT.take(12).ifEmpty { null } ?: JSONObject.NULL)
        .put("wire", WireConstants.VERSION)
    private var errs: LinkedHashMap<String, JSONObject>? = null // (errors.json's, as read: by process and fingerprint)
    private val LATEST = listOf("message", "file", "line", "function", "stack", "commit", "wire", "screen", "part",
        "wire_op", "code", "crumbs") // (an error's, as it was the last time)

    /** As PhoneHost starts: its state read, sampling started if it's on, and a crash as the app last went kept. */
    @Synchronized
    fun start(phoneRoot: File, id: String) {
        root = phoneRoot
        hostId = id
        on = try { JSONObject(File(dir(), "state.json").readText()).optBoolean("on", true) } catch (_: Exception) { true }
        if (on) run()
        PhoneErrors.crashed()?.let { if (on) keepErrors(listOf(it), "phone", null) }
    }

    private fun run() {
        if (sampling == null) sampling = work.scheduleWithFixedDelay({ sampleNow() }, SAMPLE_S, SAMPLE_S, TimeUnit.SECONDS)
    }

    /** The wire's TelemetryState: on or off; nothing profiled here. */
    fun state(): JSONObject = JSONObject().put("on", on).put("line_profiler", false).put("line_profiling", false)
        .put("profiling", JSONArray())

    /** telemetry_set's: on or off (profiling isn't on this phone: an error, if it's asked for). */
    @Synchronized
    fun set(msg: JSONObject): String? {
        val m = Msg.TelemetrySet.from(msg)
        if (m.lineProfiling == true || m.app != null && !m.functions.isNullOrEmpty()) return Wording.Telemetry.noProfiling
        m.on?.let { now ->
            if (now != on) {
                on = now
                if (on) run() else { sampling?.cancel(false); sampling = null; cpuBefore = null; front = null }
            }
        }
        dir().mkdirs()
        File(dir(), "state.json").writeText(JSONObject().put("on", on).toString())
        return null
    }

    /** An app's telling (this phone's app's): its events kept, while it's on. */
    fun keep(msg: JSONObject) {
        if (!on) return
        val app = msg.optString("app").takeIf { it in APPS } ?: return
        val told = JSONObject().put("commit", msg.optString("commit").takeIf { !msg.isNull("commit") && it.isNotEmpty() }?.take(12)
            ?: JSONObject.NULL).put("wire", (msg.opt("wire") as? Number)?.toInt() ?: JSONObject.NULL)
        msg.optJSONArray("errors")?.let { keepErrors(it.list(), app, told) }
        val rows = msg.optJSONArray("events").list().take(1000).mapNotNull { e -> event(e, app)?.also { r ->
            r.put("commit", told.get("commit")).put("wire", told.get("wire")) } }
        write("events", rows)
        if (follow(rows)) PhoneHost.changed() // (every head told: none notifies of the chat in front)
    }

    /** The chat in front followed through what's told: whether it's changed. */
    @Synchronized
    private fun follow(rows: List<JSONObject>): Boolean {
        val before = front
        for (e in rows) {
            val chat = e.getString("host") to e.getString("session")
            if (e.getString("kind") == "chat_front") front = chat
            else if (e.getString("kind") == "chat_left" && front == chat) front = null
        }
        return front != before
    }

    /** Errors told (telemetry.Telemetry.keep_errors, the same): each added to what's kept of it, by its process and
     * fingerprint, written at once (they're few). told: the telling's version, for one that doesn't say its own. */
    @Synchronized
    fun keepErrors(errors: List<JSONObject>, app: String, told: JSONObject?) {
        val kept = errors()
        for (e in errors.take(500)) {
            val fp = e.optString("fingerprint").takeIf { it.isNotEmpty() } ?: continue
            if (e.optString("type").isEmpty() || e.optInt("count", 0) < 1 || e.opt("first") !is Number || e.opt("last") !is Number) continue
            val r = JSONObject(e.toString()).put("app", app)
            if (told != null && (r.isNull("commit") || !r.has("commit")) && (r.isNull("wire") || !r.has("wire")))
                r.put("commit", told.opt("commit")).put("wire", told.opt("wire"))
            if (!r.has("first_commit") || r.isNull("first_commit")) r.put("first_commit", r.opt("commit") ?: JSONObject.NULL)
            val key = "$app:$fp"
            val got = kept[key]
            if (got == null) { kept[key] = r; continue }
            if (r.getDouble("last") >= got.getDouble("last")) for (k in LATEST) got.put(k, r.opt(k) ?: JSONObject.NULL)
            got.put("count", got.getInt("count") + r.getInt("count")).put("first", minOf(got.getDouble("first"), r.getDouble("first")))
                .put("last", maxOf(got.getDouble("last"), r.getDouble("last")))
        }
        val old = System.currentTimeMillis() / 1000.0 - KEEP.getValue("errors") * 86400
        kept.entries.removeAll { it.value.optDouble("last", 0.0) < old }
        if (kept.size > ERRORS_KEPT) kept.entries.sortedByDescending { it.value.optDouble("last", 0.0) }.drop(ERRORS_KEPT)
            .forEach { kept.remove(it.key) }
        if (root == null) return
        dir().mkdirs()
        val tmp = File(dir(), "errors.json.tmp")
        tmp.writeText(JSONObject().put("errors", JSONArray(kept.values.toList())).toString())
        tmp.renameTo(File(dir(), "errors.json"))
    }

    private fun errors(): LinkedHashMap<String, JSONObject> = errs ?: LinkedHashMap<String, JSONObject>().also { m ->
        try {
            JSONObject(File(dir(), "errors.json").readText()).optJSONArray("errors").list()
                .forEach { m["${it.optString("app")}:${it.optString("fingerprint")}"] = it }
        } catch (_: Exception) {
        }
        errs = m
    }

    /** The errors that happened from start to end, the last to happen first, the latest limit of them. */
    @Synchronized
    fun errorRows(start: Double, end: Double, limit: Int): List<JSONObject> = errors().values
        .filter { it.optDouble("last") >= start && it.optDouble("first") <= end }.sortedByDescending { it.optDouble("last") }
        .take(limit)

    /** The chats in front on this phone now (Info's front, a TelemetryFront each): what no head notifies of. */
    fun fronts(): JSONArray = JSONArray(listOfNotNull(front).map { (h, s) -> JSONObject().put("host", h).put("session", s) })

    private fun event(e: JSONObject, app: String): JSONObject? {
        val kind = e.optString("kind").takeIf { it in KINDS } ?: return null
        val at = (e.opt("at") as? Number)?.toDouble() ?: return null
        val host = e.optString("host").takeIf { it.isNotEmpty() } ?: return null
        val session = e.optString("session").takeIf { it.isNotEmpty() } ?: return null
        val out = JSONObject().put("at", Math.round(at * 1000) / 1000.0).put("app", app).put("kind", kind)
            .put("host", host.take(200)).put("session", session.take(200))
        for (k in listOf("turn", "model", "effort")) e.optString(k).takeIf { !e.isNull(k) && it.isNotEmpty() }?.let { out.put(k, it.take(200)) }
        (e.opt("seconds") as? Number)?.toDouble()?.takeIf { it >= 0 }?.let { out.put("seconds", Math.round(it * 1000) / 1000.0) }
        for (k in listOf(*TOKENS, "tokens_in")) (e.opt(k) as? Number)?.toLong()?.takeIf { it >= 0 }?.let { out.put(k, it) }
        return out
    }

    /** A turn of one of this phone's sessions ended: the host's own turn_ended (what tokens are added up from). */
    fun turn(session: String, seconds: Double, model: String?, effort: String?, tokens: LongArray) {
        if (!on) return
        event(JSONObject().put("at", System.currentTimeMillis() / 1000.0).put("kind", "turn_ended").put("host", hostId)
            .put("session", session).put("seconds", seconds).put("model", model ?: JSONObject.NULL)
            .put("effort", effort ?: JSONObject.NULL).apply { TOKENS.forEachIndexed { i, k -> put(k, tokens[i]) } }, "phone")
            ?.let { write("events", listOf(it.put("app", "host"))) }
    }

    private fun day(at: Double) = Instant.ofEpochMilli((at * 1000).toLong()).atZone(ZoneId.systemDefault()).toLocalDate()

    @Synchronized
    private fun write(kind: String, rows: List<JSONObject>) {
        if (rows.isEmpty() || root == null) return
        for (r in rows) if (!r.has("wire")) r.put("commit", ver.get("commit")).put("wire", ver.get("wire")) // (the host's own)
        val where = File(dir(), kind).apply { mkdirs() }
        for ((d, these) in rows.groupBy { day(it.getDouble("at")) })
            File(where, "$d.jsonl").appendText(these.joinToString("") { "$it\n" })
        val today = LocalDate.now()
        if (pruned != today) {
            pruned = today
            for ((k, days) in KEEP) for (f in File(dir(), k).listFiles().orEmpty()) {
                val d = try { LocalDate.parse(f.name.removeSuffix(".jsonl")) } catch (_: Exception) { continue }
                if (d.plusDays(days).isBefore(today)) f.delete()
            }
        }
    }

    /** One sample: the phone's memory, swap and low memory, and this app's process (its host too) CPU and memory. No load average: Android
     * keeps it from apps. */
    private fun sampleNow() {
        try {
            val now = System.currentTimeMillis()
            val cpu = Process.getElapsedCpuTime()
            val before = cpuBefore
            cpuBefore = now to cpu
            val pct = before?.takeIf { now > it.first }?.let { Math.round((cpu - it.second) * 1000.0 / (now - it.first)) / 10.0 }
            val rss = try {
                File("/proc/self/statm").readText().trim().split(" ")[1].toLong() * Os.sysconf(OsConstants._SC_PAGESIZE)
            } catch (_: Exception) { null }
            val mem = ActivityManager.MemoryInfo()
            (PhoneHost.ctx.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager).getMemoryInfo(mem)
            val proc = JSONObject().put("app", "phone").put("pid", Process.myPid()).put("cpu", pct ?: JSONObject.NULL)
                .put("rss", rss ?: JSONObject.NULL)
            val swap = swap()
            write("metrics", listOf(JSONObject().put("at", now / 1000.0).put("load", JSONObject.NULL)
                .put("memory_total", mem.totalMem).put("memory_used", mem.totalMem - mem.availMem)
                .put("swap_total", swap?.first ?: JSONObject.NULL).put("swap_used", swap?.second ?: JSONObject.NULL)
                .put("memory_pressure", if (mem.lowMemory) "critical" else "normal").put("memory_stall", JSONObject.NULL)
                .put("procs", JSONArray().put(proc))))
        } catch (_: Exception) {
        }
    }

    /** The phone's swap (zram), total and in use, in bytes, from /proc/meminfo; null if it can't be read. */
    private fun swap(): Pair<Long, Long>? = try {
        val kb = File("/proc/meminfo").readLines().mapNotNull { l ->
            l.split(":", limit = 2).takeIf { it.size == 2 }?.let { (k, v) -> v.trim().split(" ")[0].toLongOrNull()?.let { k to it * 1024 } }
        }.toMap()
        kb["SwapTotal"]?.let { t -> t to t - (kb["SwapFree"] ?: 0L) }
    } catch (_: Exception) { null }

    /** kind's rows from start to end, the earliest first, the latest limit of them (0: all). */
    @Synchronized
    fun rows(kind: String, start: Double, end: Double, limit: Int): List<JSONObject> {
        if (root == null) return emptyList()
        val out = ArrayList<JSONObject>()
        val last = day(end)
        var d = day(maxOf(0.0, start)).let { if (it.plusDays(KEEP.getValue(kind)).isBefore(last)) last.minusDays(KEEP.getValue(kind)) else it }
        while (!d.isAfter(last)) {
            File(File(dir(), kind), "$d.jsonl").takeIf { it.exists() }?.forEachLine { line ->
                val r = try { JSONObject(line) } catch (_: Exception) { return@forEachLine }
                val at = r.optDouble("at", -1.0)
                if (at in start..end) out.add(r)
            }
            d = d.plusDays(1)
        }
        out.sortBy { it.getDouble("at") }
        return if (limit > 0 && out.size > limit) out.subList(out.size - limit, out.size) else out
    }

    /** telemetry_read's answer's fields. */
    fun read(msg: JSONObject): Array<Pair<String, Any?>> {
        val m = Msg.TelemetryRead.from(msg)
        val now = System.currentTimeMillis() / 1000.0
        val start = m.from?.toDouble() ?: (now - 86400)
        val end = m.to?.toDouble() ?: now
        val limit = m.limit?.toInt()?.coerceIn(1, 100_000) ?: LIMIT
        val what = m.what.orEmpty()
        val out = arrayListOf<Pair<String, Any?>>("host" to hostId, "state" to state())
        for (kind in listOf("events", "metrics", "profiles")) if (kind in what) out.add(kind to JSONArray(rows(kind, start, end, limit)))
        if ("usage" in what) out.add("usage" to usage(rows("events", start, end, 0)))
        if ("errors" in what) out.add("errors" to JSONArray(errorRows(start, end, limit)))
        return out.toTypedArray()
    }

    /** A turn's result's tokens, each kind apart, as TOKENS (telemetry.tokens_of, the same). */
    fun tokensOf(m: JSONObject): LongArray {
        val byModel = m.optJSONObject("model_usage_since_last_result")
        val (parts, keys) = if (byModel != null)
            byModel.keys().asSequence().mapNotNull { byModel.optJSONObject(it) }.toList() to
                listOf("inputTokens", "cacheCreationInputTokens", "cacheReadInputTokens", "outputTokens")
        else listOfNotNull(m.optJSONObject("usage")) to
            listOf("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
        return LongArray(4) { i -> parts.sumOf { (it.opt(keys[i]) as? Number)?.toLong()?.coerceAtLeast(0) ?: 0 } }
    }

    /** The hosts' own turn_ended events added up (telemetry.usage, the same). */
    fun usage(events: List<JSONObject>): JSONObject {
        val seen = HashSet<String>()
        val days = sortedMapOf<String, HashMap<String, Long>>()
        val models = HashMap<Pair<String, String?>, HashMap<String, Long>>()
        val combos = HashMap<Pair<String, String?>, HashMap<String, Long>>()
        fun add(t: HashMap<String, Long>, e: JSONObject) {
            t["turns"] = (t["turns"] ?: 0) + 1
            for ((field, name) in NAMES) {
                val n = (e.opt(field) as? Number)?.toLong()?.takeIf { it > 0 } ?: continue
                t[name] = (t[name] ?: 0) + n
                t["tokens"] = (t["tokens"] ?: 0) + n
            }
        }
        for (e in events) {
            if (e.optString("kind") != "turn_ended" || e.optString("app") != "host") continue
            val key = "${e.optString("host")}/${e.optString("session")}/${e.optString("turn").ifEmpty { Math.round(e.getDouble("at")).toString() }}"
            if (!seen.add(key)) continue
            add(days.getOrPut(day(e.getDouble("at")).toString()) { HashMap() }, e)
            val model = e.optString("model").takeIf { !e.isNull("model") && it.isNotEmpty() } ?: continue
            val effort = e.optString("effort").takeIf { !e.isNull("effort") && it.isNotEmpty() }
            add(models.getOrPut(model to null) { HashMap() }, e)
            add(combos.getOrPut(model to effort) { HashMap() }, e)
        }
        fun counts(o: JSONObject, t: Map<String, Long>) = o.apply {
            for (k in listOf("turns", "tokens", *NAMES.values.toTypedArray())) put(k, t[k] ?: 0L)
        }
        fun ranked(t: Map<Pair<String, String?>, HashMap<String, Long>>) = JSONArray(t.entries.sortedWith(compareBy(
            { -(it.value["turns"] ?: 0) }, { -(it.value["tokens"] ?: 0) }, { it.key.toString() })).map {
            counts(JSONObject().put("model", it.key.first).put("effort", it.key.second ?: JSONObject.NULL), it.value) })
        return JSONObject().put("days", JSONArray(days.map { (d, v) -> counts(JSONObject().put("day", d), v) }))
            .put("models", ranked(models)).put("combos", ranked(combos))
    }

    /** A chat's size from its transcript as it is (telemetry.chat_size, the same). */
    fun chatSize(f: File): Array<Pair<String, Any?>> {
        if (!f.exists()) return arrayOf("bytes" to 0, "messages" to 0, "images" to 0)
        var messages = 0
        var images = 0
        val replies = HashSet<String>()
        fun imagesIn(a: JSONArray?): Int = a.list().sumOf { b -> (if (b.optString("type") == "image") 1 else 0) + imagesIn(b.optJSONArray("content")) }
        f.forEachLine { line ->
            val d = try { JSONObject(line) } catch (_: Exception) { return@forEachLine }
            val type = d.optString("type")
            if (type != "user" && type != "assistant" || d.optBoolean("isMeta")) return@forEachLine
            val m = d.optJSONObject("message") ?: JSONObject()
            val content = m.optJSONArray("content")
            images += imagesIn(content)
            if (type == "assistant") {
                if (replies.add(m.optString("id").ifEmpty { d.optString("uuid") })) messages++
            } else if (m.opt("content") is String || content.list().any { it.optString("type") in setOf("text", "image") }) messages++
        }
        return arrayOf("bytes" to f.length(), "messages" to messages, "images" to images)
    }
}
