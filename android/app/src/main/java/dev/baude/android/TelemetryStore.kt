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
    private val KEEP = mapOf("events" to 400L, "metrics" to 30L, "profiles" to 30L)
    private val KINDS = setOf("chat_opened", "chat_front", "chat_left", "turn_sent", "turn_ended")
    private val APPS = setOf("baude", "record", "phone")
    private val work = Executors.newSingleThreadScheduledExecutor { r -> Thread(r, "telemetry").apply { isDaemon = true } }
    private var sampling: ScheduledFuture<*>? = null
    private var root: File? = null
    private var hostId = ""
    private var pruned: LocalDate? = null
    private var cpuBefore: Pair<Long, Long>? = null // (when, this process's CPU ms: the sample before's)
    @Volatile var on = true
        private set

    private fun dir() = File(root!!, "telemetry")

    /** As PhoneHost starts: its state read, and sampling started if it's on. */
    @Synchronized
    fun start(phoneRoot: File, id: String) {
        root = phoneRoot
        hostId = id
        on = try { JSONObject(File(dir(), "state.json").readText()).optBoolean("on", true) } catch (_: Exception) { true }
        if (on) run()
    }

    private fun run() {
        if (sampling == null) sampling = work.scheduleWithFixedDelay({ sampleNow() }, SAMPLE_S, SAMPLE_S, TimeUnit.SECONDS)
    }

    /** The wire's TelemetryState: on or off; nothing profiled here. */
    fun state(): JSONObject = JSONObject().put("on", on).put("line_profiler", false).put("profiling", JSONArray())

    /** telemetry_set's: on or off (profiling isn't on this phone: an error, if it's asked for). */
    @Synchronized
    fun set(msg: JSONObject): String? {
        val m = Msg.TelemetrySet.from(msg)
        if (m.app != null && !m.functions.isNullOrEmpty()) return Wording.Telemetry.noProfiling
        m.on?.let { now ->
            if (now != on) {
                on = now
                if (on) run() else { sampling?.cancel(false); sampling = null; cpuBefore = null }
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
        val rows = msg.optJSONArray("events").list().take(1000).mapNotNull { e -> event(e, app) }
        write("events", rows)
    }

    private fun event(e: JSONObject, app: String): JSONObject? {
        val kind = e.optString("kind").takeIf { it in KINDS } ?: return null
        val at = (e.opt("at") as? Number)?.toDouble() ?: return null
        val host = e.optString("host").takeIf { it.isNotEmpty() } ?: return null
        val session = e.optString("session").takeIf { it.isNotEmpty() } ?: return null
        val out = JSONObject().put("at", Math.round(at * 1000) / 1000.0).put("app", app).put("kind", kind)
            .put("host", host.take(200)).put("session", session.take(200))
        for (k in listOf("turn", "model", "effort")) e.optString(k).takeIf { !e.isNull(k) && it.isNotEmpty() }?.let { out.put(k, it.take(200)) }
        (e.opt("seconds") as? Number)?.toDouble()?.takeIf { it >= 0 }?.let { out.put("seconds", Math.round(it * 1000) / 1000.0) }
        for (k in listOf("tokens_in", "tokens_out")) (e.opt(k) as? Number)?.toLong()?.takeIf { it >= 0 }?.let { out.put(k, it) }
        return out
    }

    /** A turn of one of this phone's sessions ended: the host's own turn_ended (what tokens are added up from). */
    fun turn(session: String, seconds: Double, model: String?, effort: String?, tokensIn: Long, tokensOut: Long) {
        if (!on) return
        event(JSONObject().put("at", System.currentTimeMillis() / 1000.0).put("kind", "turn_ended").put("host", hostId)
            .put("session", session).put("seconds", seconds).put("model", model ?: JSONObject.NULL)
            .put("effort", effort ?: JSONObject.NULL).put("tokens_in", tokensIn).put("tokens_out", tokensOut), "phone")
            ?.let { write("events", listOf(it.put("app", "host"))) }
    }

    private fun day(at: Double) = Instant.ofEpochMilli((at * 1000).toLong()).atZone(ZoneId.systemDefault()).toLocalDate()

    @Synchronized
    private fun write(kind: String, rows: List<JSONObject>) {
        if (rows.isEmpty() || root == null) return
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
                .put("memory_available", mem.availMem)
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
        return out.toTypedArray()
    }

    /** The hosts' own turn_ended events added up (telemetry.usage, the same). */
    fun usage(events: List<JSONObject>): JSONObject {
        val seen = HashSet<String>()
        val days = sortedMapOf<String, LongArray>()
        val models = HashMap<Pair<String, String?>, LongArray>()
        val combos = HashMap<Pair<String, String?>, LongArray>()
        for (e in events) {
            if (e.optString("kind") != "turn_ended" || e.optString("app") != "host") continue
            val key = "${e.optString("host")}/${e.optString("session")}/${e.optString("turn").ifEmpty { Math.round(e.getDouble("at")).toString() }}"
            if (!seen.add(key)) continue
            val tokens = e.optLong("tokens_in") + e.optLong("tokens_out")
            days.getOrPut(day(e.getDouble("at")).toString()) { LongArray(2) }.let { it[0] += tokens; it[1] += 1 }
            val model = e.optString("model").takeIf { !e.isNull("model") && it.isNotEmpty() } ?: continue
            val effort = e.optString("effort").takeIf { !e.isNull("effort") && it.isNotEmpty() }
            models.getOrPut(model to null) { LongArray(2) }.let { it[0] += 1; it[1] += tokens }
            combos.getOrPut(model to effort) { LongArray(2) }.let { it[0] += 1; it[1] += tokens }
        }
        fun ranked(t: Map<Pair<String, String?>, LongArray>) = JSONArray(t.entries.sortedWith(compareBy(
            { -it.value[0] }, { -it.value[1] }, { it.key.toString() })).map {
            JSONObject().put("model", it.key.first).put("effort", it.key.second ?: JSONObject.NULL)
                .put("turns", it.value[0]).put("tokens", it.value[1]) })
        return JSONObject().put("days", JSONArray(days.map { (d, v) ->
            JSONObject().put("day", d).put("tokens", v[0]).put("turns", v[1]) }))
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
