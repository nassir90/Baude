package dev.baude.android

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
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
import androidx.compose.material3.Icon
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
import androidx.compose.ui.graphics.compositeOver
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

private val DAYS = listOf(Wording.Tasks.mon, Wording.Tasks.tue, Wording.Tasks.wed, Wording.Tasks.thu,
    Wording.Tasks.fri, Wording.Tasks.sat, Wording.Tasks.sun)

/** A run's states that are still going. */
private fun going(status: String?) = status == "running" || status == "asking"

/** What fires a trigger, as its row's title says it: 'weekdays at 09:00', 'every 2 hours', 'push to main in ~/site'. */
fun triggerText(t: Msg.TaskTrigger): String {
    if (t.kind == "git_push") return Wording.Tasks.pushTo(branch = t.branch.orEmpty(), repo = t.repo.orEmpty())
    t.every?.let { every ->
        val m = every.toInt()
        return when {
            m == 60 -> Wording.Tasks.everyHour
            m % 60 == 0 && m >= 120 -> Wording.Tasks.everyHours(n = m / 60)
            else -> Wording.Tasks.everyMinutes(n = m)
        }
    }
    val days = t.days.orEmpty().map { it.toInt() }.sorted()
    val said = when (days) {
        listOf(0, 1, 2, 3, 4) -> Wording.Tasks.weekdays
        listOf(5, 6) -> Wording.Tasks.weekends
        (0..6).toList() -> Wording.Tasks.everyDay
        else -> days.joinToString(", ") { DAYS.getOrElse(it) { "?" } }
    }
    return Wording.Tasks.daysAt(days = said, time = t.at.orEmpty())
}

/** A time on the host's clock (its offset): HH:MM today, else 'Mon 03 Nov 14:00'. */
fun hostWhen(t: Double?, offset: Int, short: Boolean = false): String {
    if (t == null || t == 0.0) return Wording.Tasks.never
    val zone = TimeZone.getTimeZone("GMT").apply { rawOffset = offset * 1000 }
    val day = SimpleDateFormat("yyyyMMdd", Locale.ENGLISH).apply { timeZone = zone }
    val at = Date((t * 1000).toLong())
    val pattern = if (day.format(at) == day.format(Date())) "HH:mm" else if (short) "dd MMM HH:mm" else "EEE dd MMM HH:mm"
    return SimpleDateFormat(pattern, Locale.ENGLISH).apply { timeZone = zone }.format(at)
}

/** How long a run took: '12 s', '3 min 4 s'. */
fun tookText(seconds: Double): String {
    val s = Math.round(seconds).toInt()
    return if (s < 60) Wording.Tasks.seconds(n = s) else Wording.Tasks.minutesTook(m = s / 60, s = s % 60)
}

private fun typeWord(type: String?) = when (type) {
    "number" -> Wording.Tasks.number
    "bool" -> Wording.Tasks.yesNo
    "choice" -> Wording.Tasks.choice
    else -> Wording.Tasks.text
}

private fun kindWord(kind: String?) = when (kind) {
    "schedule" -> Wording.Tasks.schedule
    "git_push" -> Wording.Tasks.gitPush
    else -> Wording.Tasks.byHand
}

private fun kindIcon(kind: String?) = when (kind) {
    "schedule" -> Adwaita.Alarm
    "git_push" -> Adwaita.MediaPlaylistShuffle
    else -> Adwaita.MediaPlaybackStart
}

/** A trigger's payload's keys (what fills an input). */
private fun payloadKeys(kind: String) = if (kind == "git_push") listOf("repo", "branch", "before", "after", "commit", "message", "author")
    else listOf("time")

/** What the Tasks page shows (the list, the task open, the run open, the dialog up), kept while the chat's open.
 * live false: nothing's asked of a host (a holder filled by hand, as the screenshot rig's fixtures are). */
class TasksState(val live: Boolean = true) {
    val tasks = mutableStateListOf<Msg.Task>()
    var clock by mutableStateOf(Msg.Clock())
    var loaded by mutableStateOf(false)
    var error by mutableStateOf<String?>(null)
    var reason by mutableStateOf<String?>(null)
    /** list, task or run. */
    var page by mutableStateOf("list")
    /** The task open: as listed till it's read; its latest runs, newest first. */
    var task by mutableStateOf<Msg.Task?>(null)
    val runs = mutableStateListOf<Msg.TaskRun>()
    /** The run open, its log, its Bot's name. */
    var run by mutableStateOf<Msg.TaskRun?>(null)
    val log = mutableStateListOf<Msg.TaskLogLine>()
    var bot by mutableStateOf("")
    /** The dialogs: the task dialog's, the run form's, the trigger dialog's, the inputs dialog's. */
    var editing by mutableStateOf<Msg.Task?>(null)
    var running by mutableStateOf<Msg.Task?>(null)
    var adding by mutableStateOf<Msg.Task?>(null)
    var forming by mutableStateOf<Msg.Task?>(null)
    /** The task whose Delete waits to be confirmed. */
    var deleting by mutableStateOf<Msg.Task?>(null)
}

/** A task as it's sent to be saved: the whole of it but its script (left as it is: the Bot's to change). */
private fun whole(t: Msg.Task, form: List<Msg.TaskField>? = t.form,
                  triggers: List<Msg.TaskTrigger>? = t.triggers): JSONObject {
    val o = JSONObject().put("name", t.name.orEmpty()).put("runtime", t.runtime ?: "python")
        .put("form", JSONArray(form.orEmpty().map { it.json() }))
        .put("triggers", JSONArray(triggers.orEmpty().map { it.json() }))
        .put("fallback", t.fallback ?: true).put("paused", t.paused == true)
    t.id?.let { o.put("id", it) }
    t.timeoutS?.let { o.put("timeout_s", it) }
    return o
}

/** A run's state as an icon (its word for a description): a tick done, a stop sign failed, a spinner going, a square
 * stopped, an arrow skipped; nothing for none. */
@Composable
private fun RunState(status: String?) {
    val c = LocalBaude.current
    val word = Wording.Tasks.status(status = status.orEmpty())
    val mod = Modifier.size(16.dp.icon)
    when (status) {
        "done" -> Icon(Adwaita.ObjectSelect, word, tint = c.success, modifier = mod)
        "failed" -> Icon(Adwaita.ProcessStop, word, tint = c.destructive, modifier = mod)
        "running", "asking" -> Box(mod.semantics { contentDescription = word }, contentAlignment = Alignment.Center) { Spinner() }
        "stopped" -> Icon(Adwaita.MediaPlaybackStop, word, tint = c.dim, modifier = mod)
        "skipped" -> Icon(Adwaita.GoNext, word, tint = c.dim, modifier = mod)
    }
}

/** A group's heading on a task's page, what's at its right after it. */
@Composable
private fun GroupHeading(text: String, end: (@Composable () -> Unit)? = null) {
    val c = LocalBaude.current
    Row(Modifier.fillMaxWidth().padding(start = 14.dp, end = 10.dp, top = 10.dp).heightIn(min = 34.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(text, style = Type.folder, color = c.dim, modifier = Modifier.weight(1f))
        end?.invoke()
    }
}

/** A page's header row: back (its tip), its title in bold, the buttons after it. */
@Composable
private fun PageHeader(back: String, title: String, onBack: () -> Unit, end: @Composable () -> Unit) {
    val c = LocalBaude.current
    Row(Modifier.fillMaxWidth().padding(horizontal = 6.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        IconButton(Adwaita.GoPrevious, back, onClick = onBack)
        Text(title, style = Type.bold, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
        end()
    }
}

/** A Bot's tasks (scripts its host runs, by hand or as their triggers fire), as Baude's Tasks page: the list, a task's
 * page, a run's log, and their dialogs. The list's read again every 30 s while it's shown (5 while a run's going) and
 * as a turn of the Bot ends; a task's page every 5 s while a run's going; a run's every second while it's going.
 * state: what it shows (the screenshot rig's, filled by hand). */
@Composable
fun TasksPage(hostId: String, sid: String, here: Boolean, state: TasksState? = null) {
    val c = LocalBaude.current
    val st = state ?: remember(hostId, sid) { TasksState() }
    fun ask(msg: JSONObject, doing: String, then: (JSONObject) -> Unit = {}) {
        if (!st.live) return
        Mesh.ask(hostId, msg) { r ->
            when (r.optString("op")) {
                Op.TASKS -> {
                    val got = Msg.TasksReply.from(r)
                    st.tasks.clear()
                    st.tasks.addAll(got.tasks.orEmpty())
                    st.clock = got.clock ?: st.clock
                    st.loaded = true
                    st.task?.id?.let { id -> got.tasks?.firstOrNull { it.id == id }?.let { st.task = it } } // (as listed now)
                }
                Op.TASK -> {
                    val got = Msg.TaskReply.from(r)
                    if (got.task?.id == st.task?.id) {
                        st.task = got.task
                        st.runs.clear()
                        st.runs.addAll(got.runs.orEmpty())
                    }
                    st.clock = got.clock ?: st.clock
                }
                Op.TASK_RUN -> {
                    val got = Msg.TaskRunReply.from(r)
                    if (got.run?.id == st.run?.id) {
                        st.run = got.run
                        st.log.clear()
                        st.log.addAll(got.log.orEmpty())
                        st.bot = got.bot.orEmpty()
                    }
                }
                Op.TASK_STARTED -> {}
                else -> {
                    st.error = Wording.Tasks.couldnt(doing = doing)
                    st.reason = Msg.Error.from(r).said?.ifBlank { null }
                    return@ask
                }
            }
            st.error = null
            st.reason = null
            then(r)
        }
    }
    fun load() = ask(Msg.Tasks(session = sid).json(), "load")
    fun readTask() { st.task?.id?.let { ask(Msg.TaskGet(session = sid, id = it).json(), "load") } }
    fun readRun() { st.run?.id?.let { ask(Msg.TaskRunGet(session = sid, run = it).json(), "load") } }
    fun openTask(t: Msg.Task) {
        if (st.task?.id != t.id) st.runs.clear()
        st.task = t
        st.page = "task"
        readTask()
    }
    fun openRun(r: Msg.TaskRun) {
        if (st.run?.id != r.id) { st.log.clear(); st.bot = "" }
        st.run = r
        st.page = "run"
        readRun()
    }
    fun save(task: JSONObject) = ask(Msg.Tasks(session = sid, set = task).json(), "save") { if (st.page == "task") readTask() }
    fun runIt(t: Msg.Task, inputs: JSONObject = JSONObject(), dry: Boolean = false) =
        ask(Msg.RunTask(session = sid, id = t.id.orEmpty(), inputs = inputs, dry = dry).json(), "run") {
            load()
            if (st.page == "task") readTask()
        }
    fun runOrAsk(t: Msg.Task) = if (t.form.orEmpty().isNotEmpty()) st.running = t else runIt(t)
    LaunchedEffect(here) {
        while (here && st.live) {
            load()
            delay(if (st.tasks.any { going(it.last?.status) }) 5_000 else 30_000)
        }
    }
    LaunchedEffect(here, st.page, st.task?.id) {
        while (here && st.live && st.page == "task") {
            delay(5_000)
            if (st.runs.any { going(it.status) } || going(st.task?.last?.status)) readTask()
        }
    }
    LaunchedEffect(here, st.page, st.run?.id) {
        while (here && st.live && st.page == "run") {
            delay(1_000)
            if (going(st.run?.status)) readRun()
        }
    }
    DisposableEffect(hostId, sid) {
        val l: (String, JSONObject) -> Unit = { at, msg ->
            if (at == hostId && msg.optString("op") == Op.TURN_ENDED && Msg.TurnEnded.from(msg).session.orEmpty() == sid) {
                load()
                if (st.page == "task") readTask()
            }
        }
        Mesh.listeners.add(l)
        onDispose { Mesh.listeners.remove(l) }
    }
    androidx.activity.compose.BackHandler(here && st.page != "list") { st.page = if (st.page == "run") "task" else "list" }
    val offset = st.clock.offset?.toInt() ?: 0
    Column(Modifier.fillMaxSize()) {
        Box(Modifier.weight(1f)) {
            when (st.page) {
                "task" -> st.task?.let { t ->
                    TaskView(t, st.runs.toList(), offset, back = { st.page = "list"; load() }, run = { runOrAsk(t) },
                        change = { st.editing = t }, delete = { st.deleting = t }, addTrigger = { st.adding = t },
                        removeTrigger = { tr -> save(whole(t, triggers = t.triggers.orEmpty().filter { it.id != tr.id })) },
                        editForm = { st.forming = t }, openRun = { openRun(it) })
                }
                "run" -> st.run?.let { r ->
                    RunView(r, st.log.toList(), st.bot.ifBlank { Wording.Sidebar.bot }, st.task?.name ?: r.name.orEmpty(), offset,
                        back = { st.page = "task"; readTask() },
                        stop = { ask(Msg.TaskStop(session = sid, run = r.id.orEmpty()).json(), "stop") })
                }
                else -> Scrolled {
                    BoxedList(st.tasks.toList(), if (st.loaded || st.error == null) Wording.Tasks.noTasks else "") { t ->
                        ActionRow(t.name.orEmpty().ifBlank { Wording.Tasks.task }, null, onClick = { openTask(t) },
                            prefix = t.last?.let { { RunState(it.status) } }, suffix = {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Switch(t.paused != true, { on -> save(whole(t).put("paused", !on)) },
                                        colors = SwitchDefaults.colors(checkedTrackColor = c.accentBg, checkedThumbColor = c.accentFg),
                                        modifier = Modifier.padding(end = 4.dp).semantics { contentDescription = Wording.Tasks.turnOff })
                                    IconButton(Adwaita.MediaPlaybackStart, Wording.Tasks.run) { runOrAsk(t) }
                                }
                            })
                    }
                }
            }
        }
        st.error?.let { e ->
            Text(e, style = Type.caption, color = c.dim, modifier = Modifier.fillMaxWidth().padding(10.dp)
                .then(st.reason?.let { Modifier.tip(it) } ?: Modifier))
        }
    }
    st.editing?.let { t ->
        TaskDialog(t) { made -> st.editing = null; if (made != null) save(made) }
    }
    st.running?.let { t ->
        RunForm(t) { inputs, dry -> st.running = null; if (inputs != null) runIt(t, inputs, dry) }
    }
    st.adding?.let { t ->
        TriggerDialog(t, st.clock) { made ->
            st.adding = null
            if (made != null) save(whole(t, triggers = t.triggers.orEmpty() + made))
        }
    }
    st.forming?.let { t ->
        FormDialog(t) { made -> st.forming = null; if (made != null) save(whole(t, form = made)) }
    }
    st.deleting?.let { t ->
        AlertDialog(
            onDismissRequest = { st.deleting = null },
            title = { Text(Wording.Tasks.deleteHeading(name = t.name.orEmpty().ifBlank { Wording.Tasks.task })) },
            confirmButton = {
                Button(Wording.Common.delete, destructive = true) {
                    st.deleting = null
                    ask(Msg.Tasks(session = sid, delete = t.id.orEmpty()).json(), "delete")
                    st.page = "list"
                }
            },
            dismissButton = { Button(Wording.Common.cancel) { st.deleting = null } },
            containerColor = c.dialog,
        )
    }
}

/** A task's page: its header (back, its name, Run, Change, Delete), then its triggers, inputs and runs, each a compact
 * list. */
@Composable
private fun TaskView(t: Msg.Task, runs: List<Msg.TaskRun>, offset: Int, back: () -> Unit, run: () -> Unit, change: () -> Unit,
                     delete: () -> Unit, addTrigger: () -> Unit, removeTrigger: (Msg.TaskTrigger) -> Unit, editForm: () -> Unit,
                     openRun: (Msg.TaskRun) -> Unit) {
    val c = LocalBaude.current
    Column(Modifier.fillMaxSize()) {
        PageHeader(Wording.Tasks.title, t.name.orEmpty().ifBlank { Wording.Tasks.task }, back) {
            IconButton(Adwaita.MediaPlaybackStart, Wording.Tasks.run, onClick = run)
            IconButton(Adwaita.DocumentEdit, Wording.Tasks.change, onClick = change)
            IconButton(Adwaita.UserTrash, Wording.Common.delete, onClick = delete)
        }
        Scrolled {
            GroupHeading(Wording.Tasks.triggers) { IconButton(Adwaita.ListAdd, Wording.Tasks.addTrigger, onClick = addTrigger) }
            BoxedList(t.triggers.orEmpty(), Wording.Tasks.byHand) { tr ->
                ActionRow(triggerText(tr), null, compact = true, prefix = {
                    Icon(kindIcon(tr.kind), kindWord(tr.kind), tint = c.fg, modifier = Modifier.size(16.dp.icon))
                }, suffix = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        tr.error?.ifBlank { null }?.let { e ->
                            Box(Modifier.size(Gap.barButton).tip(e), contentAlignment = Alignment.Center) {
                                Icon(Adwaita.DialogWarning, null, tint = c.warning, modifier = Modifier.size(16.dp.icon))
                            }
                        }
                        IconButton(Adwaita.ListRemove, Wording.Tasks.removeTrigger) { removeTrigger(tr) }
                    }
                })
            }
            GroupHeading(Wording.SignalCard.inputs) { IconButton(Adwaita.DocumentEdit, Wording.Tasks.change, onClick = editForm) }
            BoxedList(t.form.orEmpty(), Wording.SignalCard.noInputs) { f ->
                ActionRow(f.label?.ifBlank { null } ?: f.name.orEmpty(), null, compact = true, suffix = {
                    Text(typeWord(f.type), style = Type.body, color = c.dim)
                })
            }
            GroupHeading(Wording.Tasks.runs)
            BoxedList(runs, Wording.Tasks.noRuns) { r ->
                val title = listOfNotNull(hostWhen(r.started?.toDouble(), r.offset?.toInt() ?: offset, short = true),
                    if (r.dry == true) Wording.Tasks.dryTag else null).joinToString(" · ")
                ActionRow(title, null, onClick = { openRun(r) }, compact = true, prefix = { RunState(r.status) }, suffix = {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Icon(kindIcon(r.kind), kindWord(r.kind), tint = c.dim, modifier = Modifier.size(16.dp.icon))
                        r.version?.let { Text(it, style = Type.mono, color = c.dim) }
                    }
                })
            }
            Spacer(Modifier.padding(bottom = 6.dp))
        }
    }
}

/** A run's page: its header (back, '<task> · <when>', Stop while it's going), one line of its facts (why it failed in
 * place of its state, if it did), and its log: the script's output and its errors each in code blocks of their own
 * (errors in the error colour), its asks of the Bot and the Bot's answers as cards, the rest as plain lines. */
@Composable
private fun RunView(r: Msg.TaskRun, log: List<Msg.TaskLogLine>, bot: String, task: String, offset: Int, back: () -> Unit,
                    stop: () -> Unit) {
    val c = LocalBaude.current
    Column(Modifier.fillMaxSize()) {
        PageHeader(task, "$task · ${hostWhen(r.started?.toDouble(), r.offset?.toInt() ?: offset)}", back) {
            if (going(r.status)) IconButton(Adwaita.MediaPlaybackStop, Wording.Common.stop, onClick = stop)
        }
        Text(listOfNotNull(r.error?.ifBlank { null } ?: Wording.Tasks.status(status = r.status.orEmpty()), kindWord(r.kind),
            r.seconds?.let { tookText(it.toDouble()) }, if (r.dry == true) Wording.Tasks.dummyAnswers else null, r.version)
            .joinToString(" · "),
            style = Type.caption, color = c.dim, modifier = Modifier.padding(start = 10.dp, end = 10.dp, bottom = 6.dp))
        // (consecutive output lines together as one block, consecutive errors as another)
        val parts = mutableListOf<List<Msg.TaskLogLine>>()
        for (l in log) {
            val prev = parts.lastOrNull()
            val block = l.kind == "out" || l.kind == "err"
            if (block && prev != null && prev.first().kind == l.kind) parts[parts.size - 1] = prev + l
            else parts.add(listOf(l))
        }
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 10.dp, vertical = 4.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)) {
            for (p in parts) {
                val l = p.first()
                when (l.kind) {
                    "out", "err" -> LogBlock(p)
                    "ask", "answer" -> Column(Modifier.fillMaxWidth().card(c), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text(when {
                            l.kind == "answer" -> Wording.Tasks.botAnswered(bot = bot)
                            l.error == true -> Wording.Tasks.handedBot(bot = bot)
                            else -> Wording.Tasks.askedBot(bot = bot)
                        }, style = Type.bold, color = c.fg)
                        val values = l.values?.takeIf { it.length() > 0 }
                        Text(l.text.orEmpty().ifBlank { values?.toString(1).orEmpty() }, style = Type.body, color = c.fg)
                    }
                    else -> Text(l.text.orEmpty(), style = Type.body, color = c.fg)
                }
            }
            Spacer(Modifier.padding(bottom = 6.dp))
        }
    }
}

/** A run's output, or its errors, a code block's panel (errors in the error colour). */
@Composable
private fun LogBlock(lines: List<Msg.TaskLogLine>) {
    val c = LocalBaude.current
    Box(Modifier.fillMaxWidth().clip(RoundedCornerShape(Gap.codeRadius)).background(c.raised.compositeOver(c.inside))
        .horizontalScroll(rememberScrollState()).padding(horizontal = 8.dp, vertical = 6.dp)) {
        Column {
            for (l in lines) Text(l.text.orEmpty(), style = Type.mono, color = if (l.kind == "err") c.error else c.fg, softWrap = false)
        }
    }
}

/** A label over a control, in a dialog. */
@Composable
private fun Label(text: String) {
    Text(text, style = Type.body, color = LocalBaude.current.fg)
}

/** Change Task: its name, and whether a failure's handed to the Bot. */
@Composable
private fun TaskDialog(t: Msg.Task, done: (JSONObject?) -> Unit) {
    val c = LocalBaude.current
    var name by remember { mutableStateOf(t.name.orEmpty()) }
    var fallback by remember { mutableStateOf(t.fallback ?: true) }
    AlertDialog(
        onDismissRequest = { done(null) },
        title = { Text(Wording.Tasks.changeHeading) },
        text = {
            Column(Modifier.heightIn(max = 520.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Field(name, { name = it }, Wording.Common.name)
                Row(Modifier.fillMaxWidth().clickable { fallback = !fallback }, verticalAlignment = Alignment.CenterVertically) {
                    Text(Wording.Tasks.fallback, style = Type.body, color = c.fg, modifier = Modifier.weight(1f))
                    Switch(fallback, { fallback = it }, colors = SwitchDefaults.colors(checkedTrackColor = c.accentBg, checkedThumbColor = c.accentFg))
                }
            }
        },
        confirmButton = {
            Button(Wording.Common.save, suggested = true) { done(whole(t).put("name", name).put("fallback", fallback)) }
        },
        dismissButton = { Button(Wording.Common.cancel) { done(null) } },
        containerColor = c.dialog,
    )
}

/** A field's default as the run form fills it. */
private fun defaultText(f: Msg.TaskField): String = when (val d = f.default) {
    null, JSONObject.NULL -> ""
    is Number -> if (d.toDouble() == Math.floor(d.toDouble())) d.toLong().toString() else d.toString()
    else -> d.toString()
}

/** Run <task>: a control for each field of its form, filled with its default; Cancel, Dry run, Run. */
@Composable
private fun RunForm(t: Msg.Task, done: (JSONObject?, Boolean) -> Unit) {
    val c = LocalBaude.current
    val fields = t.form.orEmpty()
    val values = remember { mutableStateListOf<String>().apply { addAll(fields.map { f ->
        if (f.type == "bool") (f.default == true).toString() else if (f.type == "choice") defaultText(f).ifBlank { f.choices.orEmpty().firstOrNull().orEmpty() }
        else defaultText(f) }) } }
    fun filled() = JSONObject().apply {
        fields.forEachIndexed { i, f ->
            val v = values[i]
            put(f.name.orEmpty(), when (f.type) {
                "bool" -> v == "true"
                "number" -> v.toLongOrNull() ?: v.toDoubleOrNull() ?: JSONObject.NULL
                else -> v
            })
        }
    }
    AlertDialog(
        onDismissRequest = { done(null, false) },
        title = { Text(Wording.Tasks.runHeading(name = t.name.orEmpty())) },
        text = {
            Column(Modifier.heightIn(max = 520.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                fields.forEachIndexed { i, f ->
                    val label = f.label?.ifBlank { null } ?: f.name.orEmpty()
                    when (f.type) {
                        "bool" -> Row(Modifier.fillMaxWidth().clickable { values[i] = (values[i] != "true").toString() },
                            verticalAlignment = Alignment.CenterVertically) {
                            Text(label, style = Type.body, color = c.fg, modifier = Modifier.weight(1f))
                            Switch(values[i] == "true", { values[i] = it.toString() },
                                colors = SwitchDefaults.colors(checkedTrackColor = c.accentBg, checkedThumbColor = c.accentFg))
                        }
                        "choice" -> Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Label(label)
                            Picker(values[i], label, f.choices.orEmpty().map { it to it }, values[i]) { values[i] = it }
                        }
                        "number" -> { Label(label); NumberField(values[i]) { values[i] = it.filter { ch -> ch.isDigit() || ch == '.' || ch == '-' } } }
                        else -> { Label(label); Field(values[i], { values[i] = it }, "") }
                    }
                }
                // (stacked, as the desktop's alert stacks them when they don't fit side by side)
                Column(Modifier.fillMaxWidth().padding(top = 8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(Wording.Tasks.run, suggested = true, modifier = Modifier.fillMaxWidth()) { done(filled(), false) }
                    Button(Wording.Tasks.dryRun, modifier = Modifier.fillMaxWidth()) { done(filled(), true) }
                    Button(Wording.Common.cancel, modifier = Modifier.fillMaxWidth()) { done(null, false) }
                }
            }
        },
        confirmButton = {},
        containerColor = c.dialog,
    )
}

/** Add Trigger: a schedule (every so many minutes or hours, or at a time of day on the days picked) or a git push (a
 * repo's folder on the host and its branch), and which of its payload's keys fills each field of the form. */
@Composable
private fun TriggerDialog(t: Msg.Task, clock: Msg.Clock, done: (Msg.TaskTrigger?) -> Unit) {
    val c = LocalBaude.current
    var kind by remember { mutableStateOf("schedule") }
    var atTime by remember { mutableStateOf(true) }
    var hours by remember { mutableStateOf(true) }
    var count by remember { mutableStateOf("1") }
    var at by remember { mutableStateOf("09:00") }
    val days = remember { mutableStateListOf(0, 1, 2, 3, 4) }
    var repo by remember { mutableStateOf("") }
    var branch by remember { mutableStateOf("main") }
    val fields = t.form.orEmpty().mapNotNull { it.name }
    val fills = remember { mutableStateOf(mapOf<String, String>()) } // (a field: the key picked; else its own name's, if any)
    val keys = payloadKeys(kind)
    fun from(field: String) = fills.value[field]?.takeIf { it == "" || it in keys } ?: field.takeIf { it in keys } ?: ""
    AlertDialog(
        onDismissRequest = { done(null) },
        title = { Text(Wording.Tasks.triggerHeading) },
        text = {
            Column(Modifier.heightIn(max = 520.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Picker(if (kind == "git_push") Wording.Tasks.gitPush else Wording.Tasks.schedule, Wording.Tasks.triggerHeading,
                    listOf(Wording.Tasks.schedule to "schedule", Wording.Tasks.gitPush to "git_push"), kind) { kind = it }
                if (kind == "schedule") {
                    Picker(if (atTime) Wording.Tasks.atATime else Wording.Tasks.every, Wording.Tasks.schedule,
                        listOf(Wording.Tasks.every to "every", Wording.Tasks.atATime to "at"), if (atTime) "at" else "every") { atTime = it == "at" }
                    if (!atTime) Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Box(Modifier.width(80.dp)) { NumberField(count) { count = it.filter(Char::isDigit).take(4) } }
                        Picker(if (hours) Wording.Tasks.hours else Wording.Tasks.minutes, Wording.Tasks.every,
                            listOf(Wording.Tasks.minutes to "m", Wording.Tasks.hours to "h"), if (hours) "h" else "m") { hours = it == "h" }
                    } else {
                        Text(clock.zone?.ifBlank { null }?.let { Wording.Tasks.timesIn(zone = it) } ?: Wording.Tasks.hostClock,
                            style = Type.caption, color = c.dim)
                        Box(Modifier.width(90.dp)) { NumberField(at, "HH:MM") { at = it.filter { ch -> ch.isDigit() || ch == ':' }.take(5) } }
                        Row(horizontalArrangement = Arrangement.spacedBy(2.dp)) {
                            DAYS.forEachIndexed { i, d ->
                                val on = i in days
                                Text(d, style = Type.caption, color = if (on) c.accentFg else c.fg, modifier = Modifier
                                    .clip(RoundedCornerShape(6.dp)).background(if (on) c.accentBg else c.fg.copy(alpha = 0.10f))
                                    .clickable { if (on) days.remove(i) else days.add(i) }.padding(horizontal = 6.dp, vertical = 6.dp))
                            }
                        }
                    }
                } else {
                    Label(Wording.Tasks.repo)
                    Field(repo, { repo = it }, "")
                    Label(Wording.Lookup.branch)
                    Field(branch, { branch = it }, "")
                }
                if (fields.isNotEmpty()) {
                    Text(Wording.Tasks.fills, style = Type.folder, color = c.dim, modifier = Modifier.padding(top = 4.dp))
                    for (f in fields) Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(Wording.Tasks.fillsField(field = f), style = Type.body, color = c.fg, modifier = Modifier.weight(1f))
                        val picked = from(f)
                        Picker(picked.ifBlank { Wording.Tasks.nothing }, Wording.Tasks.fillsField(field = f),
                            listOf(Wording.Tasks.nothing to "") + keys.map { it to it }, picked) { fills.value = fills.value + (f to it) }
                    }
                }
            }
        },
        confirmButton = {
            Button(Wording.Common.save, suggested = true) {
                val map = fields.associateWith { from(it) }.filterValues { it.isNotEmpty() }
                done(if (kind == "git_push") Msg.TaskTrigger(kind = kind, repo = repo, branch = branch.ifBlank { "main" }, fills = map)
                    else if (atTime) Msg.TaskTrigger(kind = kind, at = at, days = days.sorted(), fills = map)
                    else Msg.TaskTrigger(kind = kind, fills = map, every = (count.toIntOrNull() ?: 1).let { n -> // (as the host takes it)
                        if (hours) n.coerceIn(1, 744) * 60 else n.coerceIn(5, 44640) }))
            }
        },
        dismissButton = { Button(Wording.Common.cancel) { done(null) } },
        containerColor = c.dialog,
    )
}

/** One input in the inputs dialog, as it's being written. */
private class FieldDraft(name: String, type: String, choices: String, default: String, val label: String?) {
    var name by mutableStateOf(name)
    var type by mutableStateOf(type)
    var choices by mutableStateOf(choices)
    var default by mutableStateOf(default)
}

/** Change Inputs: a row for each input (its name, its type, a choice's choices, its default, remove), and Add input. */
@Composable
private fun FormDialog(t: Msg.Task, done: (List<Msg.TaskField>?) -> Unit) {
    val c = LocalBaude.current
    val drafts = remember { mutableStateListOf<FieldDraft>().apply { addAll(t.form.orEmpty().map { f ->
        FieldDraft(f.name.orEmpty(), f.type ?: "text", f.choices.orEmpty().joinToString(", "), defaultText(f), f.label) }) } }
    val types = listOf("text", "number", "bool", "choice").map { typeWord(it) to it }
    AlertDialog(
        onDismissRequest = { done(null) },
        title = { Text(Wording.Tasks.inputsHeading) },
        text = {
            Column(Modifier.heightIn(max = 520.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                for (d in drafts.toList()) Column(Modifier.fillMaxWidth().card(c), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        Box(Modifier.weight(1f)) { Field(d.name, { d.name = it.filter { ch -> ch.isLetterOrDigit() || ch == '_' } }, Wording.Common.name) }
                        Picker(typeWord(d.type), typeWord(d.type), types, d.type) { d.type = it }
                        IconButton(Adwaita.ListRemove, Wording.Tasks.removeInput) { drafts.remove(d) }
                    }
                    if (d.type == "choice") Field(d.choices, { d.choices = it }, Wording.Tasks.choices)
                    if (d.type == "bool") Row(Modifier.fillMaxWidth().clickable { d.default = (d.default != "true").toString() },
                        verticalAlignment = Alignment.CenterVertically) {
                        Spacer(Modifier.weight(1f))
                        Switch(d.default == "true", { d.default = it.toString() },
                            colors = SwitchDefaults.colors(checkedTrackColor = c.accentBg, checkedThumbColor = c.accentFg))
                    } else Field(d.default, { d.default = it }, "")
                }
                Button(Wording.Tasks.addInput) { drafts.add(FieldDraft("", "text", "", "", null)) }
            }
        },
        confirmButton = {
            Button(Wording.Common.save, suggested = true) {
                done(drafts.filter { it.name.isNotBlank() }.map { d ->
                    val choices = d.choices.split(',').map { it.trim() }.filter { it.isNotEmpty() }
                    Msg.TaskField(name = d.name, label = d.label, type = d.type, choices = if (d.type == "choice") choices else null,
                        default = when (d.type) {
                            "bool" -> d.default == "true"
                            "number" -> d.default.toLongOrNull() ?: d.default.toDoubleOrNull()
                            else -> d.default.ifBlank { null }
                        })
                })
            }
        },
        dismissButton = { Button(Wording.Common.cancel) { done(null) } },
        containerColor = c.dialog,
    )
}

@Composable
private fun NumberField(value: String, hint: String = "", set: (String) -> Unit) {
    val c = LocalBaude.current
    Box(Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).background(c.fg.copy(alpha = 0.08f))
        .padding(horizontal = 10.dp, vertical = 9.dp)) {
        if (value.isEmpty()) Text(hint, style = Type.body, color = c.dim)
        BasicTextField(value, set, singleLine = true, textStyle = Type.body.copy(color = c.fg), cursorBrush = SolidColor(c.accentBg),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number), modifier = Modifier.fillMaxWidth())
    }
}
