package dev.baude.android

import android.os.Handler
import android.os.Looper
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import org.json.JSONObject
import java.io.File
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId

/** What this app records of its own use (telemetryview.py's Recorder, the same): the time each place is on screen
 * (counted only while the app is in front and the screen was touched or typed on in the last IDLE_S), and the uses of
 * each ☰ item, told to this phone's own host (PhoneHost: the telemetry op) about once a minute and as the app goes to
 * the background. Nothing is sent anywhere else. A place is the schema/copy key that names it; a use is the key of
 * the item's words. */
object Telemetry {
    private const val TICK_MS = 5_000L
    private const val IDLE_MS = 60_000L
    private const val TELL_MS = 60_000L
    private val main = Handler(Looper.getMainLooper())
    private val seconds = HashMap<String, Double>()
    private val uses = HashMap<String, Int>()
    private var inputAt = 0L
    private var ticked = 0L
    private var told = 0L
    private var running = false
    @Volatile var screen: List<String> = listOf("contacts") // (MainActivity's: the screen shown)
    @Volatile var pages: List<String> = emptyList() // (a chat's: the titles of its pages on screen, ChatShell's)

    /** The screens' places (MainActivity's screen's first word): a chat's are its pages'. */
    private val SCREENS = mapOf("contacts" to "telemetry.home_screen", "phone" to "this_phone.title",
        "feedback" to "fixes.send_feedback", "settings" to "window.settings_title", "changelog" to "changelog.title",
        "mesh" to "mesh_versions.title", "databases" to "window.database_schemas", "kubernetes" to "window.kubernetes",
        "terminal" to "multiplex.terminal_menu", "fixes" to "window.ongoing_feedback")

    /** A chat's pages' places (Shell.kt's pages: their titles' keys). */
    private val PAGES = setOf("chats.chat", "meta_settings.title", "bot_memory.title", "bot_browser.title", "routines.title",
        "side_panel.subagents", "side_panel.processes", "side_panel.side_questions", "side_panel.artifacts", "lookup.files",
        "git.title", "side_panel.signal")

    fun places(): List<String> = if (screen.firstOrNull() == "chat")
        pages.mapNotNull { Wording.keyOf(it)?.takeIf { k -> k in PAGES } }.distinct()
        else listOfNotNull(SCREENS[screen.firstOrNull()])

    fun touched() {
        inputAt = System.currentTimeMillis()
    }

    /** A ☰ item picked, by its words: counted by their schema/copy key (words that aren't one, a host's name, say,
     * aren't counted). */
    fun used(label: String) {
        val key = Wording.keyOf(label) ?: return
        main.post { uses[key] = (uses[key] ?: 0) + 1 }
    }

    /** In front (MainActivity's onResume): counted from now. */
    fun resumed() {
        if (running) return
        running = true
        ticked = System.currentTimeMillis()
        told = ticked
        main.postDelayed(::tick, TICK_MS)
    }

    /** Gone to the background (onPause): what's been added up told now. */
    fun paused() {
        tick()
        running = false
        main.removeCallbacksAndMessages(null)
        tell()
    }

    private fun tick() {
        val now = System.currentTimeMillis()
        val step = minOf(now - ticked, 2 * TICK_MS) / 1000.0
        ticked = now
        if (running && now - inputAt < IDLE_MS) for (p in places()) seconds[p] = (seconds[p] ?: 0.0) + step
        if (now - told >= TELL_MS) tell()
        if (running) {
            main.removeCallbacksAndMessages(null)
            main.postDelayed(::tick, TICK_MS)
        }
    }

    private fun tell() {
        told = System.currentTimeMillis()
        val s = seconds.filterValues { it >= 0.5 }.mapValues { Math.round(it.value * 10) / 10.0 }
        val u = HashMap(uses)
        if (s.isEmpty() && u.isEmpty()) return
        seconds.clear()
        uses.clear()
        val msg = Msg.Telemetry(app = "phone", seconds = JSONObject(s), uses = JSONObject(u as Map<*, *>)).json()
        Mesh.ask("phone", msg) { r ->
            if (r.optString("op") == "telemetry_kept") return@ask
            for ((p, v) in s) seconds[p] = (seconds[p] ?: 0.0) + v // (not taken: told next time)
            for ((n, c) in u) uses[n] = (uses[n] ?: 0) + c
        }
    }
}

/** This phone's host's telemetry (baudelib/telemetry.py, the same): kept in the app's folder, phone/telemetry, a file
 * a day (YYYY-MM-DD.jsonl, this phone's time), a line a telling; added up for telemetry_read. */
object TelemetryStore {
    private const val SECONDS_MAX = 3600.0
    private const val USES_MAX = 10_000
    private val APPS = setOf("baude", "record", "phone")

    private fun file(root: File, day: LocalDate) = File(File(root, "telemetry"), "$day.jsonl")

    /** A telemetry request's counts (as the wire has them: WireCheck's refusal looked at them), added to today's file. */
    @Synchronized
    fun keep(root: File, msg: JSONObject) {
        val app = msg.optString("app").takeIf { it in APPS } ?: return
        val s = JSONObject()
        msg.optJSONObject("seconds")?.let { m ->
            for (k in m.keys()) { val v = m.optDouble(k, 0.0); if (v > 0) s.put(k, minOf(v, SECONDS_MAX)) } }
        val u = JSONObject()
        msg.optJSONObject("uses")?.let { m ->
            for (k in m.keys()) { val v = m.optInt(k, 0); if (k.isNotEmpty() && v > 0) u.put(k.take(120), minOf(v, USES_MAX)) } }
        if (s.length() == 0 && u.length() == 0) return
        val now = System.currentTimeMillis()
        val f = file(root, Instant.ofEpochMilli(now).atZone(ZoneId.systemDefault()).toLocalDate())
        f.parentFile?.mkdirs()
        f.appendText(JSONObject().put("at", now / 1000.0).put("app", app).put("seconds", s).put("uses", u).toString() + "\n")
    }

    /** The last days' (today the first) added up: times and uses, by app, the most first. */
    @Synchronized
    fun read(root: File, days: Int): Pair<List<JSONObject>, List<JSONObject>> {
        val times = HashMap<Pair<String, String>, Double>()
        val used = HashMap<Pair<String, String>, Int>()
        val today = LocalDate.now()
        for (back in 0 until days.coerceIn(1, 366)) {
            val f = file(root, today.minusDays(back.toLong()))
            if (!f.exists()) continue
            f.forEachLine { line ->
                val d = try { JSONObject(line) } catch (_: Exception) { return@forEachLine }
                val app = d.optString("app").takeIf { it in APPS } ?: return@forEachLine
                d.optJSONObject("seconds")?.let { m -> for (k in m.keys())
                    times[app to k] = (times[app to k] ?: 0.0) + m.optDouble(k, 0.0) }
                d.optJSONObject("uses")?.let { m -> for (k in m.keys()) used[app to k] = (used[app to k] ?: 0) + m.optInt(k, 0) }
            }
        }
        return times.entries.sortedWith(compareBy({ -it.value }, { it.key.first }, { it.key.second })).map {
            JSONObject().put("app", it.key.first).put("place", it.key.second).put("seconds", Math.round(it.value))
        } to used.entries.sortedWith(compareBy({ -it.value }, { it.key.first }, { it.key.second })).map {
            JSONObject().put("app", it.key.first).put("name", it.key.second).put("count", it.value)
        }
    }
}

private val PERIODS = listOf(Wording.Sidebar.today to 1, Wording.Telemetry.last7Days to 7, Wording.Telemetry.last30Days to 30)
private val APP_NAMES = mapOf("baude" to "Baude", "record" to "Record", "phone" to "Baude")

/** A time on screen, said short (telemetryview.said). */
fun saidSeconds(seconds: Long): String = when {
    seconds < 60 -> Wording.Time.seconds(seconds = seconds)
    seconds < 3600 -> Wording.Time.minutesSeconds(minutes = seconds / 60, seconds = seconds % 60)
    else -> Wording.Time.hoursMinutes(hours = seconds / 3600, minutes = seconds % 3600 / 60)
}

/** A place's or use's words: its schema/copy message, else its name as it is (a desktop action's). */
private fun named(key: String) = Wording.byKey[key] ?: key

/** Settings → Telemetry (telemetryview.Page), Settings' fourth page: the host picked beside New session (this phone's
 * own, if it's picked), a Period, then for each app of that host's machine its time in each place and its uses of
 * each item, the most first. */
@Composable
fun TelemetryPage() {
    val c = LocalBaude.current
    val hostId = picked() ?: "phone"
    var days by remember { mutableStateOf(7) }
    var reply by remember(hostId, days) { mutableStateOf<JSONObject?>(null) }
    LaunchedEffect(hostId, days) {
        Mesh.ask(hostId, Msg.TelemetryRead(days = days).json()) { reply = it }
    }
    Column(Modifier.fillMaxSize().background(c.frame).verticalScroll(rememberScrollState())) {
        BoxedList(listOf<@Composable () -> Unit>(
            {
                ActionRow(Wording.NewSession.host, null, suffix = {
                    Text(Mesh.label(hostId), style = Type.body, color = c.dim)
                })
            },
            {
                Choice(Wording.Telemetry.period, PERIODS.map { it.second.toString() }, days.toString(),
                    { v -> PERIODS.first { it.second.toString() == v }.first }) { days = it.toInt() }
            },
        ), "") { it() }
        val r = reply ?: return@Column
        val read = if (r.optString("op") == Op.ERROR) null else Msg.TelemetryReadReply.from(r)
        val times = read?.times.orEmpty()
        val uses = read?.uses.orEmpty()
        if (read == null || times.isEmpty() && uses.isEmpty()) {
            BoxedList(listOf(if (read == null) Msg.Error.from(r).said.orEmpty() else Wording.Telemetry.nothing), "") {
                ActionRow(it, null)
            }
            return@Column
        }
        for (app in listOf("baude", "record", "phone")) {
            val name = APP_NAMES.getValue(app)
            val t = times.filter { it.app == app }
            if (t.isNotEmpty()) {
                Heading(Wording.Telemetry.screenTime(app = name))
                BoxedList(t, "") { row ->
                    ActionRow(named(row.place.orEmpty()), null, suffix = {
                        Text(saidSeconds(row.seconds?.toLong() ?: 0), style = Type.body, color = c.dim)
                    })
                }
            }
            val u = uses.filter { it.app == app }
            if (u.isNotEmpty()) {
                Heading(Wording.Telemetry.used(app = name))
                BoxedList(u, "") { row ->
                    ActionRow(named(row.name.orEmpty()), null, suffix = {
                        Text((row.count?.toLong() ?: 0).toString(), style = Type.body, color = c.dim)
                    })
                }
            }
        }
    }
}
