package dev.baude.android

import android.os.Handler
import android.os.Looper
import android.os.Process
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Icon
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import org.json.JSONObject
import java.time.LocalDate
import java.util.Locale

/** What this app tells its own host (this phone's: PhoneHost, the telemetry op) of chats as it happens
 * (telemetryview.py's Recorder, the same): while that host's telemetry is on, each chat opened, coming in front (its
 * screen shown, the app in front) or leaving it, each turn sent and each turn's end (how long it took, its model and
 * effort, its tokens), by its host's id and its session's id, each with its own time. Nothing is sent anywhere else. */
object Telemetry {
    private val main = Handler(Looper.getMainLooper())
    private val queue = ArrayList<Msg.TelemetryEvent>()
    private var sending = false
    private var shown = false
    private var opened: Pair<String, String>? = null
    private var front: Pair<String, String>? = null
    private val sentAt = HashMap<Pair<String, String>, Long>()
    private var errorsDue = false
    private var counting = false
    var screen: List<String> = listOf("contacts") // (MainActivity's: the screen shown)
        set(v) {
            field = v
            PhoneErrors.shown(v)
            if (shown) PhoneCounts.front(PhoneErrors.shownScreen())
            look()
        }

    /** (any thread) This app has an error (PhoneErrors' wake): told in a moment, with any meanwhile. */
    fun errorsSoon() {
        main.post {
            if (!errorsDue) {
                errorsDue = true
                main.postDelayed({
                    errorsDue = false
                    if (!TelemetryStore.on) PhoneErrors.take()
                    else if (!sending) { sending = true; tell() }
                }, 1000)
            }
        }
    }

    private fun hostOf(id: String) = Mesh.hostIdOf(id) ?: id // (a connection's: the id of the host it's to)

    /** The chat on screen now, and the one in front: told if they've changed. */
    private fun look() {
        if (!TelemetryStore.on) {
            opened = null
            front = null
            return
        }
        val chat = screen.takeIf { it.size >= 3 && it[0] == "chat" }?.let { hostOf(it[1]) to it[2] }
        if (chat != null && chat != opened) event("chat_opened", chat)
        opened = chat
        val now = chat?.takeIf { shown }
        if (now != front) {
            front?.let { event("chat_left", it) }
            now?.let { event("chat_front", it) }
            front = now
        }
    }

    /** In front (MainActivity's onResume). */
    fun resumed() {
        shown = true
        PhoneCounts.front(PhoneErrors.shownScreen())
        if (!counting) {
            counting = true
            main.postDelayed(::tellCounts, PhoneCounts.FLUSH_MS)
        }
        look()
    }

    /** Gone to the background (onPause). */
    fun paused() {
        shown = false
        PhoneCounts.front(null)
        look()
    }

    /** Every PhoneCounts.FLUSH_MS: what this app counted since, told as a row (while telemetry's on). */
    private fun tellCounts() {
        main.postDelayed(::tellCounts, PhoneCounts.FLUSH_MS)
        val row = PhoneCounts.take() ?: return
        if (!TelemetryStore.on) return
        val msg = Msg.Telemetry(app = "phone", pid = Process.myPid(), commit = BuildConfig.COMMIT.take(12).ifEmpty { null },
            wire = WireConstants.VERSION).json().put("counters", row)
        Mesh.ask("phone", msg) { r -> if (r.optString("op") != "telemetry_kept") PhoneCounts.giveBack(row) }
    }

    /** A turn sent in a chat (Mesh.send). */
    fun sent(chat: Chat) {
        if (!TelemetryStore.on) return
        val key = hostOf(chat.hostId) to chat.sid
        sentAt[key] = System.currentTimeMillis()
        event("turn_sent", key)
    }

    /** A chat's turn's result: how long it took (from its sending here, else as it says), its model and effort, its
     * tokens. */
    fun ended(chat: Chat, m: JSONObject) {
        if (!TelemetryStore.on) return
        val key = hostOf(chat.hostId) to chat.sid
        val sent = sentAt.remove(key)
        val t = TelemetryStore.tokensOf(m)
        val listed = Mesh.session(chat.hostId, chat.sid)
        event("turn_ended", key, seconds = sent?.let { (System.currentTimeMillis() - it) / 1000.0 }
            ?: m.optDouble("duration_ms", -1.0).takeIf { it >= 0 }?.let { it / 1000 },
            turn = m.optString("uuid").ifEmpty { null }, model = listed?.model, effort = listed?.effort,
            tokens = t)
    }

    private fun event(kind: String, chat: Pair<String, String>, seconds: Double? = null, turn: String? = null,
                      model: String? = null, effort: String? = null, tokens: LongArray? = null) {
        if (chat.first.isEmpty() || chat.second.isEmpty()) return
        queue.add(Msg.TelemetryEvent(at = System.currentTimeMillis() / 1000.0, kind = kind, host = chat.first,
            session = chat.second, seconds = seconds?.let { Math.round(it * 1000) / 1000.0 }, turn = turn, model = model,
            effort = effort, tokensInput = tokens?.get(0), tokensCacheWrite = tokens?.get(1), tokensCacheRead = tokens?.get(2),
            tokensOut = tokens?.get(3)))
        if (queue.size > 1000) queue.subList(0, queue.size - 1000).clear()
        if (!sending) {
            sending = true
            main.post { tell() }
        }
    }

    private fun tell() {
        val events = ArrayList(queue)
        queue.clear()
        val errors = PhoneErrors.take()
        if (events.isEmpty() && errors.isEmpty()) {
            sending = false
            return
        }
        val msg = Msg.Telemetry(app = "phone", pid = Process.myPid(), events = events,
            commit = BuildConfig.COMMIT.take(12).ifEmpty { null }, wire = WireConstants.VERSION).json()
        if (errors.isNotEmpty()) msg.put("errors", org.json.JSONArray(errors))
        Mesh.ask("phone", msg) { r ->
            if (r.optString("op") != "telemetry_kept") { // (kept: told next time)
                queue.addAll(0, events)
                PhoneErrors.giveBack(errors)
            }
            sending = false
            if (r.optString("op") == "telemetry_kept" && queue.isNotEmpty()) {
                sending = true
                main.post { tell() }
            }
        }
    }
}

private val APP_NAMES = linkedMapOf("host" to { Wording.Telemetry.hostProcess }, "baude" to { Wording.MeshVersions.baude },
    "record" to { Wording.MeshVersions.record }, "emission" to { Wording.MeshVersions.emission },
    "multiplex" to { Wording.Deps.multiplex }, "phone" to { Wording.MeshVersions.baude })
/** Each process's app's logo (schema/icons/baude's baude-app-<logo>-symbolic), as telemetryview.APP_LOGOS. */
private val APP_LOGOS = mapOf("host" to Adwaita.AppBaude, "baude" to Adwaita.AppBaude, "record" to Adwaita.AppRecord,
    "emission" to Adwaita.AppEmission, "multiplex" to Adwaita.AppMultiplex, "phone" to Adwaita.AppBaude)
private val PROFILED = listOf("host", "baude", "record")
private const val WEEKS = 53

private fun saidSize(n: Long?): String = when {
    n == null -> "–"
    n >= 1L shl 30 -> Wording.Telemetry.sizeGb(gb = String.format(Locale.ROOT, "%.1f", n / (1L shl 30).toDouble()))
    else -> Wording.Images.sizeMb(mb = String.format(Locale.ROOT, "%.0f", n / (1L shl 20).toDouble()))
}

private fun pressureSaid(s: Msg.TelemetrySample): String? =
    s.memoryPressure?.let { Wording.byKey["telemetry.pressure_$it"] ?: it }
        ?: s.memoryStall?.let { Wording.Telemetry.stalled(percent = String.format(Locale.ROOT, "%.1f", it.toDouble())) }

private fun tokens(n: Long) = fmtTokens(n)

private fun effortSaid(e: String?) = e?.let { Wording.byKey["mode_effort.effort_$it"] ?: it } ?: "–"

/** Each day's level in the grid, 0 to 4 (telemetryview.levels). */
private fun levels(days: List<Msg.TelemetryDay>): Map<String, Int> {
    val most = days.maxOfOrNull { it.tokens?.toLong() ?: 0L } ?: 0
    return days.associate { d ->
        val t = d.tokens?.toLong() ?: 0
        d.day.orEmpty() to if (t == 0L || most == 0L) 0 else minOf(4, 1 + (4 * t / (most + 1)).toInt())
    }
}

/** A year of tokens, a square a day, a column a week (Sunday at the top), today's the last (TokenGrid). */
@Composable
private fun TokenGrid(days: List<Msg.TelemetryDay>) {
    val c = LocalBaude.current
    val level = remember(days) { levels(days) }
    val today = LocalDate.now()
    val first = today.minusDays(((WEEKS - 1) * 7 + (today.dayOfWeek.value % 7)).toLong())
    Canvas(Modifier.fillMaxWidth().padding(12.dp).aspectRatio(WEEKS / 7.2f)) { // (as tall as its squares)
        val gap = 2.dp.toPx()
        val cell = minOf((size.width - (WEEKS - 1) * gap) / WEEKS, (size.height - 6 * gap) / 7)
        val left = (size.width - (WEEKS * cell + (WEEKS - 1) * gap)) / 2
        for (i in 0 until WEEKS * 7) {
            val d = first.plusDays(i.toLong())
            if (d.isAfter(today)) break
            val l = level[d.toString()] ?: 0
            val colour = if (l == 0) c.fg.copy(alpha = 0.08f) else c.accentBg.copy(alpha = listOf(0.25f, 0.45f, 0.7f, 1f)[l - 1])
            drawRoundRect(colour, Offset(left + i / 7 * (cell + gap), i % 7 * (cell + gap)), Size(cell, cell),
                CornerRadius(cell / 5))
        }
    }
}

/** usage's tokens, each kind apart, in the order they're shown (telemetryview.KINDS): input, cache writes, cache
 * reads, output, and older turns' input with their cache in it. */
private fun kinds(input: Long?, cacheWrite: Long?, cacheRead: Long?, output: Long?, inputWithCache: Long?) = listOf(
    Wording.Meta.input to (input ?: 0L), Wording.Telemetry.cacheWrite to (cacheWrite ?: 0L),
    Wording.Telemetry.cacheRead to (cacheRead ?: 0L), Wording.Meta.output to (output ?: 0L),
    Wording.Telemetry.inputWithCache to (inputWithCache ?: 0L))

/** A row each kind of token (telemetryview.token_rows), the older turns' only where there are some. */
private fun tokenRows(k: List<Pair<String, Long>>): List<@Composable () -> Unit> =
    k.filterIndexed { i, (_, n) -> i < 4 || n > 0 }.map { (name, n) -> valueRow(name, tokens(n)) }

/** A model's row (telemetryview.model_expander): its name, its slug on a long-press, its turns after it; tapped,
 * opened to its tokens, a row each kind, as Adw.ExpanderRow is. */
@OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class)
private fun modelExpander(m: Msg.TelemetryModel, name: String, slug: String?): @Composable () -> Unit = {
    val c = LocalBaude.current
    val ctx = androidx.compose.ui.platform.LocalContext.current
    var open by remember(m.model) { mutableStateOf(false) }
    Column {
        Box(Modifier.combinedClickable(onClick = { open = !open }, onLongClick = slug?.let { { showSlug(ctx, it) } })) {
            ActionRow(name, null, suffix = {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(Wording.Telemetry.turns(turns = m.turns?.toLong() ?: 0), style = Type.body, color = c.dim, maxLines = 1)
                    Icon(Adwaita.PanDown, null, tint = c.dim, modifier = Modifier.size(16.dp.icon).rotate(if (open) 180f else 0f))
                }
            })
        }
        if (open) for (r in tokenRows(kinds(m.input?.toLong(), m.cacheWrite?.toLong(), m.cacheRead?.toLong(),
                m.output?.toLong(), m.inputWithCache?.toLong()))) {
            Box(Modifier.fillMaxWidth().height(1.dp).background(c.rule))
            r()
        }
    }
}

private const val ERRORS_SHOWN = 20
private const val COUNTS_SHOWN = 10
private const val COUNTS_DAYS = 7
private val USED_KINDS = setOf("part", "menu", "screen")
private val TIMED_KINDS = setOf("part", "menu", "op", "callback")

/** A count's name as a row says it (telemetryview.count_title): a part's entity and part, else its name. */
private fun countTitle(c: Msg.TelemetryCount): String {
    val name = c.name.orEmpty()
    if (c.kind == "part" || c.kind == "menu") {
        if ("|copy:" in name) return name.substringBefore("|copy:") + " · " + name.substringAfter("|copy:")
        if ("/" in name) return name.substringBefore("/") + " · " + name.substringAfter("/")
    }
    return name
}

private fun saidSpan(seconds: Double): String {
    val s = seconds.toLong()
    return when {
        s >= 3600 -> Wording.Time.hoursMinutes(hours = s / 3600, minutes = s % 3600 / 60)
        s >= 60 -> Wording.Time.minutesSeconds(minutes = s / 60, seconds = s % 60)
        else -> Wording.Time.seconds(seconds = s)
    }
}

/** A count (telemetryview.count_row): its process's logo, its name, how many times (and a screen's time in front;
 * slow: its time each). */
@Composable
private fun CountRow(c: Msg.TelemetryCount, slow: Boolean) {
    val k = LocalBaude.current
    val n = c.count?.toLong() ?: 0L
    val secs = c.seconds?.toDouble() ?: 0.0
    val values = listOfNotNull(
        if (slow) Wording.Telemetry.averageMs(ms = String.format(Locale.ROOT, "%.1f", 1000 * secs / maxOf(1L, n)))
        else if (c.kind == "screen" && secs > 0) saidSpan(secs) else null,
        Wording.Telemetry.errorTimes(count = n))
    ActionRow(countTitle(c), null,
        prefix = { Icon(APP_LOGOS[c.app] ?: Adwaita.AppBaude, null, tint = k.fg, modifier = Modifier.size(16.dp.icon)) },
        suffix = { Text(values.joinToString("  "), style = Type.body, color = k.dim, maxLines = 1) })
}
private const val ERRORS_DAYS = 7

private fun whereSaid(screen: String?, part: String?) = listOfNotNull(screen, part).joinToString(" · ").ifEmpty { "–" }

/** An error (telemetryview.error_row): its process's logo, its type and message, how long ago it last happened and how
 * many times; tapped, opened to where it was raised, what was on screen, the user's last actions before it and the
 * code it ran, as Adw.ExpanderRow is. */
private fun errorExpander(e: Msg.TelemetryError): @Composable () -> Unit = {
    val c = LocalBaude.current
    var open by remember(e.fingerprint, e.app) { mutableStateOf(false) }
    val title = if (!e.message.isNullOrEmpty() && e.message != e.type) "${e.type}: ${e.message}" else e.type.orEmpty()
    Column {
        ActionRow(title, null, onClick = { open = !open },
            prefix = { Icon(APP_LOGOS[e.app] ?: Adwaita.AppBaude, null, tint = c.fg, modifier = Modifier.size(16.dp.icon)) },
            suffix = {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(ago(e.last?.toLong() ?: 0L), style = Type.body, color = c.dim, maxLines = 1)
                    Text(Wording.Telemetry.errorTimes(count = e.count?.toLong() ?: 1L), style = Type.body, color = c.dim, maxLines = 1)
                    Icon(Adwaita.PanDown, null, tint = c.dim, modifier = Modifier.size(16.dp.icon).rotate(if (open) 180f else 0f))
                }
            })
        if (open) {
            val rows = ArrayList<@Composable () -> Unit>()
            e.file?.let { rows.add(valueRow(Wording.Telemetry.errorWhere, "$it:${e.line}" + (e.function?.let { f -> " $f" } ?: ""))) }
            if (e.wireOp != null) rows.add(valueRow(Wording.Telemetry.errorOp, e.wireOp + (e.code?.let { " · $it" } ?: "")))
            else e.code?.let { rows.add(valueRow(Wording.Telemetry.errorCode, it.toString())) }
            if (e.screen != null || e.part != null) rows.add(valueRow(Wording.Telemetry.errorScreen, whereSaid(e.screen, e.part)))
            for (k in e.crumbs.orEmpty().reversed()) rows.add(valueRow(Wording.byKey["telemetry.action_${k.action}"] ?: k.action.orEmpty(),
                whereSaid(k.screen, k.part ?: k.widget) + " · " + ago(k.at?.toLong() ?: 0L)))
            if (e.commit != null || e.wire != null) rows.add(valueRow(Wording.Telemetry.errorVersion,
                Wording.Telemetry.versionOf(commit = e.commit ?: "–", wire = e.wire?.toString() ?: "–")))
            if ((e.count?.toLong() ?: 1L) > 1) e.first?.let { rows.add(valueRow(Wording.Telemetry.errorFirst, ago(it.toLong()))) }
            for (r in rows) {
                Box(Modifier.fillMaxWidth().height(1.dp).background(c.rule))
                r()
            }
        }
    }
}

private fun valueRow(title: String, value: String?, slug: String? = null): @Composable () -> Unit = {
    val c = LocalBaude.current
    val ctx = androidx.compose.ui.platform.LocalContext.current
    Box(if (slug == null) Modifier else Modifier.pointerInput(slug) { detectTapGestures(onLongPress = { showSlug(ctx, slug) }) }) {
        ActionRow(title, null, suffix = { value?.let { Text(it, style = Type.body, color = c.dim, maxLines = 1) } })
    }
}

/** A process's row (telemetryview.proc_rows): its app's logo, its name (numbered where its app has several), its CPU
 * and memory after them. */
@Composable
private fun ProcessRow(p: Msg.TelemetryProc, n: Int?) {
    val c = LocalBaude.current
    val name = APP_NAMES[p.app]?.invoke() ?: p.app.orEmpty()
    ActionRow(if (n == null) name else Wording.Telemetry.processN(name = name, n = n), null,
        prefix = { Icon(APP_LOGOS[p.app] ?: Adwaita.AppBaude, null, tint = c.fg, modifier = Modifier.size(16.dp.icon)) },
        suffix = { Text(Wording.Telemetry.cpuRss(cpu = p.cpu?.let { String.format(Locale.ROOT, "%.1f", it.toDouble()) } ?: "–",
            rss = saidSize(p.rss?.toLong())), style = Type.body, color = c.dim, maxLines = 1) })
}

/** The Total row ending the processes (telemetryview.proc_rows): bold, its CPU and memory the processes' added up. */
@Composable
private fun TotalRow(cpus: List<Double>, rsses: List<Long>) {
    val c = LocalBaude.current
    ActionRow(Wording.Telemetry.total, null, bold = true,
        suffix = { Text(Wording.Telemetry.cpuRss(cpu = if (cpus.isEmpty()) "–" else String.format(Locale.ROOT, "%.1f", cpus.sum()),
            rss = saidSize(if (rsses.isEmpty()) null else rsses.sum())), style = Type.body, color = c.fg, maxLines = 1) })
}

/** Settings → Telemetry (telemetryview.Page), Settings' fourth page: the host picked beside New session (this phone's
 * own, if it's picked), Collect Telemetry, then what that host keeps: its tokens by day over a year with the most
 * used model and effort and each model's, its machine now, its processes now, and (a computer's) line profiling with
 * its top lines. */
@Composable
fun TelemetryPage() {
    val c = LocalBaude.current
    val hostId = picked() ?: "phone"
    var asked by remember { mutableStateOf(0) }
    var usage by remember(hostId, asked) { mutableStateOf<JSONObject?>(null) }
    var metrics by remember(hostId) { mutableStateOf<JSONObject?>(null) }
    var profiles by remember(hostId, asked) { mutableStateOf<JSONObject?>(null) }
    var errors by remember(hostId, asked) { mutableStateOf<JSONObject?>(null) }
    var counted by remember(hostId, asked) { mutableStateOf<JSONObject?>(null) }
    var deps by remember(hostId) { mutableStateOf<Msg.Dep?>(null) }
    fun read(from: Double, what: String, limit: Int?, then: (JSONObject) -> Unit) =
        Mesh.ask(hostId, Msg.TelemetryRead(from = from, what = listOf(what), limit = limit).json(), then)
    LaunchedEffect(hostId, asked) {
        val now = System.currentTimeMillis() / 1000.0
        read(now - (WEEKS * 7 + 7) * 86400.0, "usage", null) { usage = it }
        read(now - 3600, "profiles", 500) { profiles = it }
        while (true) { // (its errors read again every 30 seconds, as the desktop's are)
            read(System.currentTimeMillis() / 1000.0 - ERRORS_DAYS * 86400.0, "errors", ERRORS_SHOWN) { errors = it }
            read(System.currentTimeMillis() / 1000.0 - COUNTS_DAYS * 86400.0, "counted", 2000) { counted = it }
            delay(30_000)
        }
    }
    LaunchedEffect(hostId) {
        while (true) {
            val since = metrics?.let { Msg.TelemetryReadReply.from(it).metrics.orEmpty().lastOrNull()?.at?.toDouble() } ?: 0.0
            read(maxOf(since, System.currentTimeMillis() / 1000.0 - 60), "metrics", 1) { metrics = it }
            delay(5_000)
        }
    }
    val replies = listOfNotNull(usage, metrics, profiles)
    val error = replies.firstOrNull { it.optString("op") == Op.ERROR }
    val state = replies.firstOrNull { it.optString("op") == Op.TELEMETRY_READ }?.let { Msg.TelemetryReadReply.from(it).state }
    fun set(msg: Msg.TelemetrySet) = Mesh.ask(hostId, msg.json()) { asked++ }
    val profiling = state?.on == true && state.lineProfiling == true
    LaunchedEffect(hostId, asked, profiling) { // (its top lines read again as often as they're taken, while it profiles)
        while (profiling) {
            delay(30_000)
            read(System.currentTimeMillis() / 1000.0 - 3600, "profiles", 500) { profiles = it }
        }
    }
    LaunchedEffect(hostId, state?.lineProfiler) {
        if (state != null && state.lineProfiler == false && hostId != "phone")
            Mesh.ask(hostId, Msg.Deps().json()) { r -> deps = Msg.DepsReply.from(r).deps?.firstOrNull { it.dep == "line_profiler" } }
    }
    Column(Modifier.fillMaxSize().background(c.frame).verticalScroll(rememberScrollState())) {
        BoxedList(listOf<@Composable () -> Unit>(
            valueRow(Wording.NewSession.host, Mesh.label(hostId)),
            { SwitchRow(Wording.Telemetry.collect, null, state?.on == true, enabled = state != null) { set(Msg.TelemetrySet(on = it)) } },
        ), "") { it() }
        if (error != null) {
            BoxedList(listOf(Msg.Error.from(error).said.orEmpty()), "") { ActionRow(it, null) }
            return@Column
        }
        val u = usage?.let { Msg.TelemetryReadReply.from(it).usage }
        if (u != null) {
            val models = models(hostId)
            fun named(v: String?) = modelName(v, models).orEmpty()
            Heading(Wording.Telemetry.tokensByDay)
            val days = u.days.orEmpty()
            BoxedList(listOfNotNull<@Composable () -> Unit>({ TokenGrid(days) },
                u.combos.orEmpty().firstOrNull()?.let { top -> valueRow(Wording.Telemetry.mostUsed,
                    Wording.Telemetry.modelEffort(model = named(top.model), effort = effortSaid(top.effort)),
                    top.model?.let { modelSlug(it, models) }) }) + tokenRows(kinds(days.sumOf { it.input?.toLong() ?: 0L },
                days.sumOf { it.cacheWrite?.toLong() ?: 0L }, days.sumOf { it.cacheRead?.toLong() ?: 0L },
                days.sumOf { it.output?.toLong() ?: 0L }, days.sumOf { it.inputWithCache?.toLong() ?: 0L })), "") { it() }
            if (u.models.orEmpty().isNotEmpty()) {
                Heading(Wording.Telemetry.models)
                BoxedList(u.models.orEmpty(), "") { m -> modelExpander(m, named(m.model), m.model?.let { modelSlug(it, models) })() }
            }
        }
        if (state?.on == false) BoxedList(listOf(Wording.Telemetry.off), "") { ActionRow(it, null) }
        metrics?.takeIf { state?.on == true }?.let { Msg.TelemetryReadReply.from(it).metrics.orEmpty().lastOrNull() }?.let { s ->
            Heading(Wording.Telemetry.machine)
            val rows = ArrayList<@Composable () -> Unit>()
            s.load?.takeIf { it.isNotEmpty() }?.let { l ->
                rows.add(valueRow(Wording.Telemetry.load, l.joinToString(" · ") { String.format(Locale.ROOT, "%.2f", it.toDouble()) })) }
            s.memoryTotal?.let { t -> rows.add(valueRow(Wording.BotMemory.title,
                Wording.Telemetry.memoryOf(used = saidSize(s.memoryUsed?.toLong()), total = saidSize(t.toLong())))) }
            s.swapTotal?.takeIf { it.toLong() > 0 }?.let { t -> rows.add(valueRow(Wording.Telemetry.swap,
                Wording.Telemetry.memoryOf(used = saidSize(s.swapUsed?.toLong()), total = saidSize(t.toLong())))) }
            pressureSaid(s)?.let { rows.add(valueRow(Wording.Telemetry.memoryPressure, it)) }
            BoxedList(rows, "") { it() }
            val order = APP_NAMES.keys.toList()
            val procs = s.procs.orEmpty().filter { it.app in APP_NAMES }
                .sortedWith(compareBy({ order.indexOf(it.app) }, { it.pid?.toLong() ?: 0L }))
            if (procs.isNotEmpty()) {
                Heading(Wording.SidePanel.processes)
                val numbered = procs.mapIndexed { i, p ->
                    val same = procs.filter { it.app == p.app }
                    p to if (same.size > 1) procs.subList(0, i).count { it.app == p.app } + 1 else null
                }
                val cpus = procs.mapNotNull { it.cpu?.toDouble() }
                val rsses = procs.mapNotNull { it.rss?.toLong() }
                BoxedList(numbered + (null to null), "") { (p, n) ->
                    if (p == null) TotalRow(cpus, rsses) else ProcessRow(p, n)
                }
            }
        }
        counted?.takeIf { it.optString("op") == Op.TELEMETRY_READ }?.let { r ->
            val rows = Msg.TelemetryReadReply.from(r).counted.orEmpty()
            val used = rows.filter { it.kind in USED_KINDS && it.app != "host" && (it.count?.toLong() ?: 0L) > 0 }
                .sortedWith(compareBy({ -(it.count?.toLong() ?: 0L) }, { -(it.seconds?.toDouble() ?: 0.0) })).take(COUNTS_SHOWN)
            val slow = rows.filter { it.kind in TIMED_KINDS && (it.count?.toLong() ?: 0L) > 0 }
                .sortedBy { -(it.seconds?.toDouble() ?: 0.0) / (it.count?.toDouble() ?: 1.0) }.take(COUNTS_SHOWN)
            if (used.isNotEmpty()) {
                Heading(Wording.Telemetry.mostUsed)
                BoxedList(used, "") { CountRow(it, false) }
            }
            if (slow.isNotEmpty()) {
                Heading(Wording.Telemetry.slowest)
                BoxedList(slow, "") { CountRow(it, true) }
            }
        }
        errors?.takeIf { it.optString("op") == Op.TELEMETRY_READ }?.let { r ->
            val list = Msg.TelemetryReadReply.from(r).errors.orEmpty()
            Heading(Wording.Telemetry.errors)
            if (list.isEmpty()) BoxedList(listOf(Wording.Telemetry.noErrors), "") { ActionRow(it, null) }
            else BoxedList(list, "") { e -> errorExpander(e)() }
        }
        if (hostId != "phone" && state != null) Profiling(state, profiles, deps, { asked++ }) { set(it) }
    }
}

/** Line profiling of a computer's processes (telemetryview.Page.show_profiling): on or off (what each profiles is
 * Baude's own pick, or what telemetry_set named for it), each process's error, every process's top lines in the last
 * hour, each with its app's logo; line_profiler missing there, what installs it. */
@Composable
private fun Profiling(state: Msg.TelemetryState, profiles: JSONObject?, dep: Msg.Dep?, again: () -> Unit,
                      set: (Msg.TelemetrySet) -> Unit) {
    val c = LocalBaude.current
    if (state.lineProfiler != true) {
        if (dep != null && dep.present != true) Box(Modifier.fillMaxWidth().height(280.dp)) { // (as every feature without
            MissingDep(dep, Wording.Telemetry.lineProfiling) { again() }                // what it needs shows it)
        }
        return
    }
    fun logo(app: String?): @Composable () -> Unit =
        { Icon(APP_LOGOS[app] ?: Adwaita.AppBaude, null, tint = c.fg, modifier = Modifier.size(16.dp.icon)) }
    BoxedList(listOf<@Composable () -> Unit>(
        { SwitchRow(Wording.Telemetry.lineProfiling, null, state.on == true && state.lineProfiling == true,
            enabled = state.on == true) { set(Msg.TelemetrySet(lineProfiling = it)) } },
    ) + state.profiling.orEmpty().filter { it.app in PROFILED && !it.error.isNullOrEmpty() }.map { p ->
        { ActionRow(p.error.orEmpty(), null, prefix = logo(p.app)) } }, "") { it() }
    val lines = HashMap<Triple<String, String, Long>, Triple<Msg.TelemetryLine, Long, Double>>()
    profiles?.let { Msg.TelemetryReadReply.from(it).profiles }.orEmpty().filter { it.app in PROFILED }.forEach { p ->
        for (x in p.lines.orEmpty()) {
            val k = Triple(p.app.orEmpty(), x.file.orEmpty(), x.line?.toLong() ?: 0)
            val got = lines[k]
            lines[k] = Triple(x, (got?.second ?: 0) + (x.hits?.toLong() ?: 0), (got?.third ?: 0.0) + (x.seconds?.toDouble() ?: 0.0))
        }
    }
    val top = lines.entries.sortedByDescending { it.value.third }.take(10)
    if (top.isEmpty()) return
    Heading(Wording.Telemetry.topLines)
    BoxedList(top, "") { (k, v) ->
        val (x, _, seconds) = v
        Row(Modifier.fillMaxWidth().heightIn(min = 50.dp).padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            logo(k.first)()
            Text("${x.file.orEmpty().substringAfterLast('/')}:${x.line}  ${x.code ?: x.function.orEmpty()}", style = Type.mono,
                color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            Text(Wording.Time.seconds(seconds = String.format(Locale.ROOT, "%.3f", seconds)), style = Type.body,
                color = c.dim, maxLines = 1) // (a phone's width: its time alone, the hits left to the desktop's)
        }
    }
}
