package dev.baude.android

import org.json.JSONArray
import org.json.JSONObject

/** How many times each thing in this app was used or run, and how long it took (baudelib/counters.py, the same): a
 * row tapped (by the screen it's on and its words' copy key, "Entity.kind|copy:key", which a computer's host names as
 * the entity's part), a screen (opened, and its time in front), a wire op this phone's host answered. Told as a row
 * about once a minute (Telemetry's), kept by this phone's host. */
object PhoneCounts {
    const val FLUSH_MS = 60_000L
    private val counts = LinkedHashMap<Pair<String, String>, DoubleArray>() // (count, seconds, the longest)
    private var since = System.currentTimeMillis() / 1000.0
    private var shown: String? = null
    private var shownAt = 0.0

    @Synchronized
    fun add(kind: String, name: String, seconds: Double = 0.0, count: Int = 1) {
        val c = counts.getOrPut(kind to name.take(200)) { DoubleArray(3) }
        c[0] += count.toDouble()
        c[1] += seconds
        if (seconds > c[2]) c[2] = seconds
    }

    /** The screen in front now (null: none, the app in the background): one come counted as opened, one gone given
     * its time. */
    @Synchronized
    fun front(entity: String?) {
        val now = System.currentTimeMillis() / 1000.0
        if (entity == shown) return
        shown?.let { add("screen", it, now - shownAt, 0) }
        shown = entity
        shownAt = now
        entity?.let { add("screen", it) }
    }

    /** What's counted since the last take, as a row (TelemetryCounters), the screen in front given its time so far;
     * null if nothing was. */
    @Synchronized
    fun take(): JSONObject? {
        val now = System.currentTimeMillis() / 1000.0
        shown?.let { add("screen", it, now - shownAt, 0); shownAt = now }
        if (counts.isEmpty()) { since = now; return null }
        val rows = counts.entries.sortedByDescending { it.value[0] }.map { (k, v) ->
            JSONObject().put("kind", k.first).put("name", k.second).put("count", v[0].toLong())
                .put("seconds", Math.round(v[1] * 1e6) / 1e6).put("max", Math.round(v[2] * 1e6) / 1e6) }
        val out = JSONObject().put("at", now).put("from", since).put("app", "phone").put("counts", JSONArray(rows))
        counts.clear()
        since = now
        return out
    }

    /** A row taken but not kept: counted again. */
    @Synchronized
    fun giveBack(row: JSONObject) {
        since = minOf(since, row.optDouble("from", since))
        for (c in row.optJSONArray("counts").list())
            add(c.getString("kind"), c.getString("name"), c.optDouble("seconds"), c.optInt("count"))
    }
}
