package dev.baude.android

import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.background
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

private val DAYS = listOf("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

/** When a routine runs, as routinespage.when_text says it. */
fun whenText(r: Msg.Routine): String {
    r.every?.let { every ->
        val m = every.toInt()
        return when {
            m == 60 -> "every hour"
            m % 60 == 0 && m >= 120 -> "every ${m / 60} hours"
            else -> "every $m minutes"
        }
    }
    val days = r.days.orEmpty().map { it.toInt() }
    val said = when (days) {
        listOf(0, 1, 2, 3, 4) -> "weekdays"
        listOf(5, 6) -> "weekends"
        (0..6).toList() -> "every day"
        else -> days.joinToString(", ") { DAYS.getOrElse(it) { "?" } }
    }
    return "$said at ${r.at.orEmpty()}"
}

/** A time as the page says it, on the host's clock (its offset): its time today, else its day too; never, none. */
fun stamp(t: Double?, offset: Int): String {
    if (t == null || t == 0.0) return "never"
    val zone = TimeZone.getTimeZone("GMT").apply { rawOffset = offset * 1000 }
    val day = SimpleDateFormat("yyyyMMdd", Locale.ENGLISH).apply { timeZone = zone }
    val at = Date((t * 1000).toLong())
    val pattern = if (day.format(at) == day.format(Date())) "HH:mm" else "EEE dd MMM HH:mm"
    return SimpleDateFormat(pattern, Locale.ENGLISH).apply { timeZone = zone }.format(at)
}

/** A Bot's routines (work it's given on a schedule, its host's clock), as Baude's Routines page: each one's when,
 * next and last run, on or paused, run now, changed or deleted; + for a new one. Read again every 30 s while it's
 * shown, and as a turn ends. */
@Composable
fun RoutinesPage(hostId: String, sid: String, here: Boolean) {
    val c = LocalBaude.current
    val list = remember(hostId, sid) { mutableStateListOf<Msg.Routine>() }
    var clock by remember { mutableStateOf(Msg.Clock()) }
    var error by remember { mutableStateOf<String?>(null) }
    var loaded by remember { mutableStateOf(false) }
    var editing by remember { mutableStateOf<Msg.Routine?>(null) }
    fun took(r: JSONObject, failed: String) {
        if (r.optString("op") == Op.ROUTINES) {
            val got = Msg.RoutinesReply.from(r)
            list.clear()
            list.addAll(got.routines.orEmpty())
            clock = got.clock ?: Msg.Clock()
            loaded = true
            error = null
        } else error = failed + Msg.Error.from(r).said.orEmpty().let { if (it.isNotBlank()) " ($it)" else "" }
    }
    fun load() = Mesh.ask(hostId, Msg.Routines(session = sid).json()) { took(it, "Couldn't load the routines.") }
    fun set(r: JSONObject, failed: String = "Couldn't save the routine.") =
        Mesh.ask(hostId, Msg.Routines(session = sid, set = r).json()) { took(it, failed) }
    LaunchedEffect(here) {
        while (here) {
            load()
            delay(30_000)
        }
    }
    DisposableEffect(hostId, sid) {
        val l: (String, JSONObject) -> Unit = { at, msg ->
            if (at == hostId && msg.optString("op") == Op.TURN_ENDED && Msg.TurnEnded.from(msg).session.orEmpty() == sid) load()
        }
        Mesh.listeners.add(l)
        onDispose { Mesh.listeners.remove(l) }
    }
    Scrolled {
        Row(Modifier.fillMaxWidth().padding(start = 10.dp, end = 10.dp, top = 8.dp, bottom = 4.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Spacer(Modifier.weight(1f))
            IconButton(Icons.Filled.Add, "New routine") { editing = Msg.Routine() }
        }
        BoxedList(list.toList(), if (loaded || error == null) "No routines yet" else "") { r ->
            val offset = clock.offset?.toInt() ?: 0
            val parts = listOfNotNull(whenText(r),
                if (r.paused == true) "paused" else "next " + stamp(r.next?.toDouble(), offset),
                r.last?.let { "last " + stamp(it.toDouble(), offset) }, r.error?.ifBlank { null })
            ActionRow(r.name.orEmpty().ifBlank { "Routine" }, parts.joinToString(" · "), suffix = {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Switch(r.paused != true, { on -> set(r.json().put("paused", !on)) },
                        colors = SwitchDefaults.colors(checkedTrackColor = c.accentBg, checkedThumbColor = c.accentFg),
                        modifier = Modifier.padding(end = 4.dp))
                    IconButton(Icons.Filled.PlayArrow, "Run now") {
                        Mesh.ask(hostId, Msg.RunRoutine(session = sid, id = r.id.orEmpty()).json()) {
                            took(it, "Couldn't run the routine.")
                        }
                    }
                    IconButton(Icons.Filled.Edit, "Change") { editing = r }
                    IconButton(Icons.Filled.Delete, "Delete") {
                        Mesh.ask(hostId, Msg.Routines(session = sid, delete = r.id.orEmpty()).json()) {
                            took(it, "Couldn't delete the routine.")
                        }
                    }
                }
            })
        }
        error?.let { Text(it, style = Type.caption, color = c.dim, modifier = Modifier.padding(start = 10.dp, end = 10.dp, top = 6.dp)) }
    }
    editing?.let { r -> RoutineForm(r, clock) { made -> editing = null; if (made != null) set(made) } }
}

/** New Routine, or Change Routine (routinespage's form): its name, its task, and when: every so many minutes or
 * hours, or at a time of day on the days picked (the host's clock). */
@Composable
private fun RoutineForm(r: Msg.Routine, clock: Msg.Clock, done: (JSONObject?) -> Unit) {
    val c = LocalBaude.current
    val new = r.id == null
    var name by remember { mutableStateOf(r.name.orEmpty()) }
    var task by remember { mutableStateOf(r.prompt.orEmpty()) }
    var atTime by remember { mutableStateOf(r.at != null) }
    val every = r.every?.toInt() ?: 60
    var hours by remember { mutableStateOf(every % 60 == 0) }
    var count by remember { mutableStateOf((if (every % 60 == 0) every / 60 else every).toString()) }
    var at by remember { mutableStateOf(r.at.orEmpty().ifBlank { "09:00" }) }
    val days = remember { mutableStateListOf<Int>().apply { addAll(r.days.orEmpty().map { it.toInt() }.ifEmpty { listOf(0, 1, 2, 3, 4) }) } }
    AlertDialog(
        onDismissRequest = { done(null) },
        title = { Text(if (new) "New Routine" else "Change Routine") },
        text = {
            Column(Modifier.heightIn(max = 440.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Field(name, { name = it }, "Name")
                Text("Task", style = Type.body, color = c.fg)
                Box(Modifier.fillMaxWidth().heightIn(min = 110.dp).border(1.dp, c.rule, RoundedCornerShape(Gap.cardRadius))
                    .padding(6.dp)) {
                    BasicTextField(task, { task = it }, textStyle = Type.body.copy(color = c.fg), cursorBrush = SolidColor(c.accentBg),
                        modifier = Modifier.fillMaxWidth().heightIn(min = 98.dp))
                }
                Picker(if (atTime) "At a time of day" else "Every", "When it runs",
                    listOf("Every" to "every", "At a time of day" to "at"), if (atTime) "at" else "every") { atTime = it == "at" }
                if (!atTime) Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Box(Modifier.width(80.dp)) {
                        NumberField(count) { count = it.filter(Char::isDigit).take(3) }
                    }
                    Picker(if (hours) "hours" else "minutes", "Minutes or hours", listOf("minutes" to "m", "hours" to "h"),
                        if (hours) "h" else "m") { hours = it == "h" }
                } else {
                    Text(clock.zone?.ifBlank { null }?.let { "Times in $it" } ?: "Times on the host's clock",
                        style = Type.caption, color = c.dim)
                    Box(Modifier.width(90.dp)) { NumberField(at) { at = it.filter { ch -> ch.isDigit() || ch == ':' }.take(5) } }
                    Row(horizontalArrangement = Arrangement.spacedBy(2.dp)) {
                        DAYS.forEachIndexed { i, d ->
                            val on = i in days
                            Text(d, style = Type.caption, color = if (on) c.accentFg else c.fg, modifier = Modifier
                                .clip(RoundedCornerShape(6.dp)).background(if (on) c.accentBg else c.fg.copy(alpha = 0.10f))
                                .clickable { if (on) days.remove(i) else days.add(i) }.padding(horizontal = 6.dp, vertical = 6.dp))
                        }
                    }
                }
            }
        },
        confirmButton = {
            Button("Save", suggested = true) {
                val made = JSONObject().put("name", name).put("prompt", task).put("paused", r.paused == true)
                if (!new) made.put("id", r.id.orEmpty())
                if (atTime) made.put("at", at).put("days", JSONArray(days.sorted()))
                else made.put("every", (count.toIntOrNull() ?: 60) * if (hours) 60 else 1)
                done(made)
            }
        },
        dismissButton = { Button("Cancel") { done(null) } },
        containerColor = c.dialog,
    )
}

@Composable
private fun NumberField(value: String, set: (String) -> Unit) {
    val c = LocalBaude.current
    Box(Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).background(c.fg.copy(alpha = 0.08f))
        .padding(horizontal = 10.dp, vertical = 9.dp)) {
        BasicTextField(value, set, singleLine = true, textStyle = Type.body.copy(color = c.fg), cursorBrush = SolidColor(c.accentBg),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number), modifier = Modifier.fillMaxWidth())
    }
}
