package dev.baude.android

import android.os.Handler
import android.os.Looper
import android.os.Process
import androidx.compose.foundation.Canvas
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
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
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Paint
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.scale
import androidx.compose.ui.text.input.ImeAction
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
    var screen: List<String> = listOf("contacts") // (MainActivity's: the screen shown)
        set(v) {
            field = v
            look()
        }

    private fun hostOf(id: String) = if (id == "phone") PhoneHost.id else id

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
        look()
    }

    /** Gone to the background (onPause). */
    fun paused() {
        shown = false
        look()
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
        val u = m.optJSONObject("usage") ?: JSONObject()
        val listed = Mesh.session(chat.hostId, chat.sid)
        event("turn_ended", key, seconds = sent?.let { (System.currentTimeMillis() - it) / 1000.0 }
            ?: m.optDouble("duration_ms", -1.0).takeIf { it >= 0 }?.let { it / 1000 },
            turn = m.optString("uuid").ifEmpty { null }, model = listed?.model, effort = listed?.effort,
            tokensIn = u.optLong("input_tokens") + u.optLong("cache_read_input_tokens") + u.optLong("cache_creation_input_tokens"),
            tokensOut = u.optLong("output_tokens"))
    }

    private fun event(kind: String, chat: Pair<String, String>, seconds: Double? = null, turn: String? = null,
                      model: String? = null, effort: String? = null, tokensIn: Long? = null, tokensOut: Long? = null) {
        if (chat.first.isEmpty() || chat.second.isEmpty()) return
        queue.add(Msg.TelemetryEvent(at = System.currentTimeMillis() / 1000.0, kind = kind, host = chat.first,
            session = chat.second, seconds = seconds?.let { Math.round(it * 1000) / 1000.0 }, turn = turn, model = model,
            effort = effort, tokensIn = tokensIn, tokensOut = tokensOut))
        if (queue.size > 1000) queue.subList(0, queue.size - 1000).clear()
        if (!sending) {
            sending = true
            main.post { tell() }
        }
    }

    private fun tell() {
        val events = ArrayList(queue)
        queue.clear()
        if (events.isEmpty()) {
            sending = false
            return
        }
        val msg = Msg.Telemetry(app = "phone", pid = Process.myPid(), events = events).json()
        Mesh.ask("phone", msg) { r ->
            if (r.optString("op") != "telemetry_kept") queue.addAll(0, events) // (kept: told next time)
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
/** Each process's app's logo (AppLogos: schema/icons/apps), as telemetryview.APP_LOGOS. */
private val APP_LOGOS = mapOf("host" to "baude", "baude" to "baude", "record" to "record", "emission" to "emission-16",
    "multiplex" to "multiplex-16", "phone" to "baude")
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

private fun tokens(n: Long) = fmtTokens(n.coerceAtMost(Int.MAX_VALUE.toLong()).toInt())

private fun effortSaid(e: String?) = e?.let { Wording.byKey["mode_effort.effort_$it"] ?: it } ?: "–"

/** Each day's level in the grid, 0 to 4 (telemetryview.levels). */
private fun levels(days: List<Msg.TelemetryDay>): Map<String, Int> {
    val most = days.maxOfOrNull { it.tokens?.toLong() ?: 0 } ?: 0
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

private fun valueRow(title: String, value: String?, slug: String? = null): @Composable () -> Unit = {
    val c = LocalBaude.current
    val ctx = androidx.compose.ui.platform.LocalContext.current
    Box(if (slug == null) Modifier else Modifier.pointerInput(slug) { detectTapGestures(onLongPress = { showSlug(ctx, slug) }) }) {
        ActionRow(title, null, suffix = { value?.let { Text(it, style = Type.body, color = c.dim, maxLines = 1) } })
    }
}

private val logoPaths = HashMap<String, androidx.compose.ui.graphics.Path>()
private fun logoPath(d: String) = logoPaths.getOrPut(d) { androidx.compose.ui.graphics.vector.PathParser().parsePathString(d).toPath() }

/** An app's logo (AppLogos), size square: Baude's and Record's tile in the accent picked, as telemetryview.logo. */
@Composable
private fun AppLogo(name: String, size: androidx.compose.ui.unit.Dp) {
    val logo = AppLogos.all[name] ?: return
    val accent = Settings.ACCENTS[Settings.accent.value]?.takeIf { name == "baude" || name == "record" }
    fun colour(argb: Long) = if (accent != null && argb == logo.tile) accent else androidx.compose.ui.graphics.Color(argb)
    Canvas(Modifier.size(size)) {
        scale(this.size.width / 128f, Offset.Zero) {
            fun draw(sh: AppLogos.Shape, clear: Boolean = false) {
                val mode = if (clear) BlendMode.Clear else BlendMode.SrcOver
                sh.fill?.let { drawPath(logoPath(sh.d), colour(it), sh.alpha, blendMode = mode) }
                sh.stroke?.let { drawPath(logoPath(sh.d), colour(it), sh.alpha, Stroke(sh.width,
                    cap = when (sh.cap) { "round" -> StrokeCap.Round; "square" -> StrokeCap.Square; else -> StrokeCap.Butt },
                    join = when (sh.join) { "round" -> StrokeJoin.Round; "bevel" -> StrokeJoin.Bevel; else -> StrokeJoin.Miter }),
                    blendMode = mode) }
            }
            for (sh in logo.shapes) {
                if (sh.cut.isEmpty()) { draw(sh); continue }
                drawContext.canvas.saveLayer(Rect(Offset.Zero, Size(128f, 128f)), Paint()) // (its cuts show what's under it)
                draw(sh)
                sh.cut.forEach { draw(it, clear = true) }
                drawContext.canvas.restore()
            }
        }
    }
}

/** A process's row (telemetryview.proc_rows): its app's logo, its name (numbered where its app has several), its CPU
 * and memory after them. */
@Composable
private fun ProcessRow(p: Msg.TelemetryProc, n: Int?) {
    val c = LocalBaude.current
    val name = APP_NAMES[p.app]?.invoke() ?: p.app.orEmpty()
    ActionRow(if (n == null) name else Wording.Telemetry.processN(name = name, n = n), null,
        prefix = { AppLogo(APP_LOGOS[p.app] ?: "baude", 16.dp.icon) },
        suffix = { Text(Wording.Telemetry.cpuRss(cpu = p.cpu?.let { String.format(Locale.ROOT, "%.1f", it.toDouble()) } ?: "–",
            rss = saidSize(p.rss?.toLong())), style = Type.body, color = c.dim, maxLines = 1) })
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
    var deps by remember(hostId) { mutableStateOf<Msg.Dep?>(null) }
    var app by remember(hostId) { mutableStateOf("host") }
    fun read(from: Double, what: String, limit: Int?, then: (JSONObject) -> Unit) =
        Mesh.ask(hostId, Msg.TelemetryRead(from = from, what = listOf(what), limit = limit).json(), then)
    LaunchedEffect(hostId, asked) {
        val now = System.currentTimeMillis() / 1000.0
        read(now - (WEEKS * 7 + 7) * 86400.0, "usage", null) { usage = it }
        read(now - 3600, "profiles", 500) { profiles = it }
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
    val profiling = state?.on == true && state.profiling.orEmpty().any { it.functions.orEmpty().isNotEmpty() }
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
            BoxedList(listOfNotNull<@Composable () -> Unit>({ TokenGrid(u.days.orEmpty()) },
                u.combos.orEmpty().firstOrNull()?.let { top -> valueRow(Wording.Telemetry.mostUsed,
                    Wording.Telemetry.modelEffort(model = named(top.model), effort = effortSaid(top.effort)),
                    top.model?.let { modelSlug(it, models) }) }), "") { it() }
            if (u.models.orEmpty().isNotEmpty()) {
                Heading(Wording.Telemetry.models)
                BoxedList(u.models.orEmpty(), "") { m ->
                    valueRow(named(m.model), Wording.Telemetry.turnsTokens(turns = m.turns?.toLong() ?: 0,
                        tokens = tokens(m.tokens?.toLong() ?: 0)), m.model?.let { modelSlug(it, models) })()
                }
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
                BoxedList(numbered, "") { (p, n) -> ProcessRow(p, n) }
            }
        }
        if (hostId != "phone" && state != null) Profiling(state, profiles, deps, app, { app = it }, { asked++ }) { set(it) }
    }
}

/** Line profiling of a computer's processes (telemetryview.Page.show_profiling): on or off, which process, what it
 * profiles, its top lines in the last hour; line_profiler missing there, what installs it. */
@Composable
private fun Profiling(state: Msg.TelemetryState, profiles: JSONObject?, dep: Msg.Dep?, app: String, pick: (String) -> Unit,
                      again: () -> Unit, set: (Msg.TelemetrySet) -> Unit) {
    val c = LocalBaude.current
    if (state.lineProfiler != true) {
        if (dep != null && dep.present != true) Box(Modifier.fillMaxWidth().height(280.dp)) { // (as every feature without
            MissingDep(dep, Wording.Telemetry.lineProfiling) { again() }                // what it needs shows it)
        }
        return
    }
    val mine = state.profiling.orEmpty().firstOrNull { it.app == app }
    val functions = mine?.functions.orEmpty()
    var text by remember(app, functions) { mutableStateOf(functions.joinToString(", ")) }
    fun named() = text.split(",").map { it.trim() }.filter { it.isNotEmpty() }
    BoxedList(listOfNotNull<@Composable () -> Unit>(
        { SwitchRow(Wording.Telemetry.lineProfiling, null, state.on == true && functions.isNotEmpty(),
            enabled = state.on == true && (functions.isNotEmpty() || named().isNotEmpty())) {
            set(Msg.TelemetrySet(app = app, functions = if (it) named() else emptyList())) } },
        { Choice(Wording.Telemetry.process, PROFILED, app, { APP_NAMES.getValue(it)() }) { pick(it) } },
        {
            Column(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp)) {
                Text(Wording.Telemetry.functions, style = Type.caption, color = c.dim)
                BasicTextField(text, { text = it }, singleLine = true, textStyle = Type.body.copy(color = c.fg),
                    cursorBrush = SolidColor(c.accentBg), keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                    keyboardActions = KeyboardActions(onDone = { set(Msg.TelemetrySet(app = app, functions = named())) }),
                    modifier = Modifier.fillMaxWidth())
            }
        },
        mine?.error?.let { e -> { ActionRow(e, null) } },
    ), "") { it() }
    val lines = HashMap<Pair<String, Long>, Triple<Msg.TelemetryLine, Long, Double>>()
    profiles?.let { Msg.TelemetryReadReply.from(it).profiles }.orEmpty().filter { it.app == app }.forEach { p ->
        for (x in p.lines.orEmpty()) {
            val k = x.file.orEmpty() to (x.line?.toLong() ?: 0)
            val got = lines[k]
            lines[k] = Triple(x, (got?.second ?: 0) + (x.hits?.toLong() ?: 0), (got?.third ?: 0.0) + (x.seconds?.toDouble() ?: 0.0))
        }
    }
    val top = lines.values.sortedByDescending { it.third }.take(10)
    if (top.isEmpty()) return
    Heading(Wording.Telemetry.topLines)
    BoxedList(top, "") { (x, hits, seconds) ->
        Row(Modifier.fillMaxWidth().heightIn(min = 50.dp).padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("${x.file.orEmpty().substringAfterLast('/')}:${x.line}  ${x.code ?: x.function.orEmpty()}", style = Type.mono,
                color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            Text(Wording.Time.seconds(seconds = String.format(Locale.ROOT, "%.3f", seconds)), style = Type.body,
                color = c.dim, maxLines = 1) // (a phone's width: its time alone, the hits left to the desktop's)
        }
    }
}
