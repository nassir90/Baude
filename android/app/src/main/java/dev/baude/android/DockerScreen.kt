package dev.baude.android

import android.content.Context
import android.os.SystemClock
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import org.json.JSONObject

/** A docker request to a host, its reply on the main thread (an error one if it isn't there). */
fun docker(hostId: String, msg: Msg.Docker, then: (Msg.DockerReply) -> Unit) {
    Mesh.ask(hostId, msg.json()) { then(Msg.DockerReply.from(it)) }
}

/** A container's or a Compose project's log followed, on a connection of its own to the host (as KubeFollow's):
 * stop() lets go of it, which stops the docker the host runs for it. */
class DockerFollow(private val hostId: String, private val msg: Msg.DockerFollow, private val onText: (String) -> Unit,
                   private val onEnd: (Msg.DockerEnd) -> Unit, private val onError: (String) -> Unit,
                   private val onAgain: () -> Unit = {}) {
    private var conn: Connection? = null
    @Volatile private var stopped = false
    private var sent = false

    /** A reconnect asks again, and the log's tail comes again: onAgain lets the screen drop what it has first. */
    fun start(): Boolean {
        conn = Mesh.watch(hostId, ::heard) { c ->
            if (stopped || c.state != "up") return@watch
            if (sent) Mesh.post { if (!stopped) onAgain() }
            sent = true
            c.send(msg.json())
        }
        return conn != null
    }

    fun stop() {
        stopped = true
        conn?.stop()
        conn = null
    }

    private fun heard(c: Connection, m: JSONObject) {
        if (m.optInt("req", -1) != KubeFollow.REQ) return
        when (m.optString("op")) {
            Op.DOCKER_TEXT -> Msg.DockerText.from(m).text?.let { t -> Mesh.post { if (!stopped) onText(t) } }
            Op.DOCKER_END -> Msg.DockerEnd.from(m).let { e -> Mesh.post { if (!stopped) onEnd(e) } }
            Op.ERROR -> Msg.Error.from(m).said.orEmpty().let { e -> Mesh.post { if (!stopped) onError(e) } }
        }
    }
}

/** What the host's categories' icon names are drawn as here (the desktop's). */
fun dockerIcon(name: String?): ImageVector? = when (name) {
    "package-x-generic-symbolic" -> Adwaita.PackageXGeneric
    "view-grid-symbolic" -> Adwaita.ViewGrid
    else -> kubeIcon(name)
}

/** A container's or a Compose project's log in the dock: its text so far and what it's doing. */
class DockerLogs(val context: String, val page: String, val row: Msg.DockerRow) {
    var follow by mutableStateOf(true)
    var text by mutableStateOf("")
    var state by mutableStateOf("")
    var tail: DockerFollow? = null
}

/** A shell in a container in the dock: its terminal, and how it ended. */
class DockerShell(val context: String, val row: Msg.DockerRow) {
    var term by mutableStateOf<HostTerm?>(null)
    var state by mutableStateOf("")
}

/** The state of the Docker view (dockerview.DockerView's): what the host says of its Docker, the page shown, what's
 * filtered, sorted and selected, the object inspected, the dock's tabs. All of it asked of the host. */
class DockerState(val hostId: String) {
    var contexts by mutableStateOf<List<Msg.DockerContext>>(emptyList())
    var context by mutableStateOf<String?>(null)
    var nothing by mutableStateOf<String?>(null)
    var sections by mutableStateOf<List<Msg.DockerSection>>(emptyList())
    var page by mutableStateOf(Msg.DockerPage(title = "Running", key = "running", what = "running containers"))
    var tablePage by mutableStateOf<Msg.DockerPage?>(null)
    var columns by mutableStateOf<List<Msg.DockerColumn>>(emptyList())
    var rows by mutableStateOf<List<Msg.DockerRow>>(emptyList())
    var filter by mutableStateOf("")
    var sortCol by mutableStateOf(0)
    var sortDesc by mutableStateOf(false)
    var selected by mutableStateOf<Msg.DockerRow?>(null)
    var inspect by mutableStateOf<Pair<String, String>?>(null) // (its text and file, or null while it's read)
    var format by mutableStateOf(prefs().getString("docker_inspect", "json") ?: "json")
    val docks = mutableStateListOf<Any>()
    var picked by mutableStateOf<Any?>(null)
    var removing by mutableStateOf<Msg.DockerRow?>(null)
    var note by mutableStateOf<Pair<String, Long>?>(null)
    var progress by mutableStateOf<String?>(null)
    var loading by mutableStateOf(false)
    var fault by mutableStateOf<Msg.DockerFault?>(null)
    var missing by mutableStateOf<Msg.Dep?>(null)
    var fade by mutableStateOf(false)
    var level by mutableStateOf("table") // (table, or categories)
    private var gen = 0

    fun open() {
        missing = null
        docker(hostId, Msg.Docker(what = "contexts")) { r ->
            r.missing?.let { return@docker lacks(it) }
            sections = r.sections.orEmpty()
            contexts = r.contexts.orEmpty()
            val names = contexts.mapNotNull { it.name }
            if (r.error != null || names.isEmpty()) {
                nothing = r.error ?: Wording.Docker.noContext
                return@docker
            }
            val last = prefs().getString("docker_context.$hostId", null)
            switchContext(last?.takeIf { it in names } ?: contexts.firstOrNull { it.current == true }?.name ?: names.first())
        }
    }

    fun stop() {
        gen++
        for (t in docks.toList()) close(t)
    }

    fun switchContext(name: String) {
        context = name
        prefs().edit().putString("docker_context.$hostId", name).apply()
        hide()
        for (t in docks.toList()) close(t)
        load(switch = true)
    }

    private fun lacks(dep: Msg.Dep) {
        gen++
        loading = false
        progress = null
        rows = emptyList()
        fade = false
        missing = dep
    }

    fun load(quiet: Boolean = false, switch: Boolean = false) {
        val ctx = context ?: return
        if (switch) fade = true
        val p = page
        val g = ++gen
        loading = true
        if (!quiet) progress = Wording.Kube.reading(p.what.orEmpty(), ctx)
        docker(hostId, Msg.Docker(what = "list", context = ctx, page = p.key)) { r ->
            if (g != gen) return@docker
            loading = false
            progress = null
            fade = false
            r.missing?.let { return@docker lacks(it) }
            val f = r.fault ?: r.error?.let { Msg.DockerFault(title = it, why = "", said = "") }
            if (f != null) {
                fault = f
                rows = emptyList()
                return@docker
            }
            fault = null
            val cols = r.columns.orEmpty()
            if (tablePage?.key != p.key || columns.map { it.title } != cols.map { it.title }) {
                sortCol = cols.indexOfFirst { it.kind == "name" }.coerceAtLeast(0)
                sortDesc = false
            }
            tablePage = p
            columns = cols
            rows = r.rows.orEmpty()
            selected = selected?.let { s -> rows.firstOrNull { it.id == s.id } }
        }
    }

    fun tick() {
        if (!loading && context != null && fault == null && missing == null) load(quiet = true)
    }

    fun pick(p: Msg.DockerPage) {
        level = "table"
        if (p.key == page.key) return
        page = p
        hide()
        load(switch = true)
    }

    fun setNote(text: String) {
        note = text to SystemClock.elapsedRealtime() + 30_000
    }

    // ---- an object, inspected
    fun select(row: Msg.DockerRow) {
        selected = row
        readInspect()
    }

    fun readInspect() {
        val row = selected ?: return
        val ctx = context ?: return
        val p = tablePage ?: return
        inspect = null
        val fmt = format
        docker(hostId, Msg.Docker(what = "inspect", context = ctx, page = p.key, id = row.id, format = fmt)) { r ->
            if (selected?.id != row.id || fmt != format) return@docker
            inspect = (r.text ?: "# ${r.error.orEmpty()}\n") to (r.file ?: "inspect.$fmt")
        }
    }

    fun pickFormat(f: String) {
        format = f
        prefs().edit().putString("docker_inspect", f).apply()
        readInspect()
    }

    fun hide() {
        selected = null
        inspect = null
    }

    // ---- logs and shells
    fun showLogs(row: Msg.DockerRow) {
        val ctx = context ?: return
        val p = tablePage ?: return
        docks.filterIsInstance<DockerLogs>().firstOrNull { it.context == ctx && it.row.id == row.id }?.let { picked = it; return }
        val l = DockerLogs(ctx, p.key.orEmpty(), row)
        docks.add(l)
        picked = l
        l.state = Wording.Kube.following
        val f = DockerFollow(hostId, Msg.DockerFollow(req = KubeFollow.REQ, what = "logs", context = ctx, page = l.page, id = row.id),
            onText = { l.text = (l.text + it.replace("\r\n", "\n")).takeLast(2_000_000) },
            onEnd = { e ->
                l.tail = null
                val code = e.code?.toInt() ?: 0
                l.state = if (code != 0 && !e.said.isNullOrEmpty()) Wording.Docker.error(e.said)
                    else if (code != 0) Wording.Docker.exited(code) else Wording.Multiplex.ended
            },
            onError = { l.tail = null; l.state = Wording.Docker.error(it) },
            onAgain = { l.text = "" })
        l.tail = f
        if (!f.start()) { l.tail = null; l.state = Wording.Docker.error(Connection.NOT_CONNECTED) }
    }

    fun showShell(row: Msg.DockerRow) {
        val ctx = context ?: return
        val argv = row.shell.orEmpty().ifEmpty { return }
        val s = DockerShell(ctx, row)
        docks.add(s)
        picked = s
        s.term = HostTerm(hostId, argv, "${row.name} · $ctx").also { t ->
            t.onEnd = { code -> if (s.term === t) s.state = Wording.Docker.exited(code) }
            Terms.add(t)
        }
    }

    fun close(tab: Any) {
        val at = docks.indexOf(tab)
        if (at < 0) return
        when (tab) {
            is DockerLogs -> { tab.tail?.stop(); tab.tail = null }
            is DockerShell -> tab.term?.close()
        }
        docks.removeAt(at)
        if (picked === tab) picked = docks.getOrNull(minOf(at, docks.size - 1))
    }

    // ---- acting
    fun act(row: Msg.DockerRow, what: String) {
        if (what == "remove") { removing = row; return }
        run(row, what)
    }

    fun run(row: Msg.DockerRow, what: String) {
        val ctx = context ?: return
        val p = tablePage ?: return
        val name = row.name.orEmpty()
        setNote(when (what) {
            "start" -> Wording.Docker.starting(name)
            "stop" -> Wording.Docker.stopping(name)
            "restart" -> Wording.Docker.restarting(name)
            else -> Wording.Docker.removing(name)
        })
        docker(hostId, Msg.Docker(what = what, context = ctx, page = p.key, id = row.id)) { r ->
            if (ctx != context) return@docker
            setNote(r.said ?: r.error.orEmpty())
            load(quiet = true)
        }
    }

    fun status(shown: Int): String {
        if (missing != null) return ""
        nothing?.let { return it }
        progress?.let { return it }
        val ctx = context.orEmpty()
        fault?.let { return "$ctx: ${it.title}" }
        val p = tablePage ?: return ""
        val what = p.what.orEmpty()
        val count = if (shown != rows.size) Wording.Kube.countOf(shown, what, rows.size) else Wording.Kube.count(shown, what)
        val fresh = note?.takeIf { it.second > SystemClock.elapsedRealtime() }?.first
        return listOfNotNull(fresh, count, ctx).filter { it.isNotEmpty() }.joinToString(" · ")
    }

    companion object {
        private fun prefs() = Mesh.app.getSharedPreferences("settings", Context.MODE_PRIVATE)
    }
}

/** The rows to show: those with the filter's text in a cell, in the order sorted by (kubeOrder's). */
fun dockerRows(st: DockerState): List<Msg.DockerRow> {
    val q = st.filter.trim().lowercase()
    val kept = st.rows.filter { r -> q.isEmpty() || r.cells.orEmpty().any { q in it.lowercase() } }
    val i = st.sortCol
    val sign = if (st.sortDesc) -1 else 1
    return kept.sortedWith { a, b ->
        val x = a.keys?.opt(i)
        val y = b.keys?.opt(i)
        val nx = x == null || x == JSONObject.NULL || x == ""
        val ny = y == null || y == JSONObject.NULL || y == ""
        if (nx || ny) (if (nx) 1 else 0) - (if (ny) 1 else 0) else sign * kubeOrder(x, y)
    }
}

/** ☰ → Docker (dockerview.DockerView): the host's Docker, laid out as the Kubernetes page is. The title bar, then the
 * context and the filter; the table of the page shown (back: the categories to pick from); a row's inspect in the
 * table's place; logs and shells docked under, a tab each. Back closes the picked tab, then the inspect, then goes to
 * the categories, then leaves. */
@Composable
fun DockerScreen(hostId: String, back: () -> Unit) {
    val c = LocalBaude.current
    val st = remember(hostId) { DockerState(hostId) }
    LaunchedEffect(st) { st.open() }
    LaunchedEffect(st) {
        while (true) {
            delay(KUBE_REFRESH_S * 1000L)
            st.tick()
        }
    }
    DisposableEffect(st) { onDispose { st.stop() } }
    val leave = { st.stop(); back() }
    val up = {
        when {
            st.picked != null -> st.close(st.picked!!)
            st.selected != null -> st.hide()
            st.level == "table" -> st.level = "categories"
            else -> leave()
        }
    }
    BackHandler(onBack = up)
    st.removing?.let { RemoveDocker(st, it) }
    Column(Modifier.fillMaxSize().background(c.frame)) {
        Bar(Wording.Window.docker, start = { IconButton(Adwaita.GoPrevious, Wording.Browser.back, onClick = up) }, end = {
            if (st.loading && st.progress != null) Box(Modifier.padding(horizontal = 9.dp)) { Spinner() }
            IconButton(Adwaita.ViewRefresh, Wording.Common.readAgain) { st.load() }
            IconButton(Adwaita.WindowClose, Wording.Images.close, onClick = leave)
        })
        if (st.level == "categories") DockerCategories(st) else {
            Row(Modifier.fillMaxWidth().padding(6.dp), horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalAlignment = Alignment.CenterVertically) {
                val names = st.contexts.mapNotNull { it.name }
                if (names.isNotEmpty()) Picker(midCut(st.context.orEmpty(), 32), Wording.Docker.contextTip, names.map { it to it },
                    st.context) { if (it != st.context) st.switchContext(it) }
                SearchField(st.filter, { st.filter = it }, Wording.Kube.filter, Modifier.weight(1f))
            }
            Box(Modifier.weight(1f).navigationBarsPadding().imePadding()) { DockerBody(st) }
        }
    }
}

/** The categories as a page: a category with several pages a heading over them, each page a row, the one shown marked. */
@Composable
private fun DockerCategories(st: DockerState) {
    val c = LocalBaude.current
    LazyColumn(Modifier.fillMaxSize().navigationBarsPadding().inside(c).padding(vertical = 4.dp)) {
        for (section in st.sections) {
            val title = section.title.orEmpty()
            val pages = section.pages.orEmpty()
            val single = pages.size == 1 && pages[0].title == title
            val icon = dockerIcon(section.icon)
            if (!single) item(key = "head:$title") {
                Row(Modifier.fillMaxWidth().padding(top = 9.dp).heightIn(min = 30.dp).padding(start = 6.dp, end = 4.dp),
                    verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (icon != null) Icon(icon, null, tint = c.dim, modifier = Modifier.size(16.dp.icon))
                    Text(title, style = Type.title, color = c.dim, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
            for (p in pages) item(key = "page:${p.key}") {
                val on = p.key == st.page.key
                Row(Modifier.fillMaxWidth().padding(top = 1.dp).heightIn(min = 23.dp).background(if (on) c.rowSelected else c.inside)
                    .clickable { st.pick(p) }.padding(start = if (single) 6.dp else 6.dp + 16.dp.icon + 8.dp, end = 4.dp),
                    verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (single && icon != null) Icon(icon, null, tint = c.fg, modifier = Modifier.size(16.dp.icon))
                    Text(p.title.orEmpty(), style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
        }
    }
}

/** Inside the frame: the table (or why Docker can't be reached, or what's missing, or a row's inspect), its status
 * line; under it the dock. */
@Composable
private fun DockerBody(st: DockerState) {
    val c = LocalBaude.current
    val k = kubeColours()
    BoxWithConstraints(Modifier.fillMaxSize()) {
        val full = maxHeight
        Column(Modifier.fillMaxSize()) {
            Column(Modifier.weight(1f).fillMaxWidth().inside(c)) {
                val sel = st.selected
                if (sel != null) InspectLevel(st, sel)
                else {
                    val shown = remember(st.rows, st.filter, st.sortCol, st.sortDesc, st.columns) { dockerRows(st) }
                    Box(Modifier.weight(1f).fillMaxWidth().alpha(if (st.fade) 0.4f else 1f)) {
                        val lacks = st.missing
                        val f = st.fault
                        if (lacks != null) MissingDep(lacks, Wording.Window.docker) { st.open() }
                        else if (f != null) DockerError(st, f) else if (st.tablePage != null) DockerTable(st, shown)
                    }
                    val status = st.status(shown.size)
                    if (status.isNotEmpty()) Text(status, style = Type.caption, color = c.dim, maxLines = 1,
                        overflow = TextOverflow.Ellipsis, modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp))
                }
            }
            if (st.docks.isNotEmpty()) DockerDock(st, maxOf(full / 2, k.logsMin))
        }
    }
}

/** A row's inspect in the table's place: its name, JSON or YAML, and a × to hide it. */
@Composable
private fun InspectLevel(st: DockerState, row: Msg.DockerRow) {
    val c = LocalBaude.current
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.padding(start = 10.dp, end = 6.dp, top = 6.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(midCut(row.name.orEmpty(), 40), style = Type.title, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f))
            Picker(st.format.uppercase(), Wording.Docker.formatTip, listOf("JSON" to "json", "YAML" to "yaml"), st.format) {
                if (it != st.format) st.pickFormat(it)
            }
            IconButton(Adwaita.WindowClose, Wording.Kube.hide) { st.hide() }
        }
        val shown = st.inspect
        key(row.id, shown?.second) {
            CodePane(shown?.first.orEmpty(), shown?.second ?: "inspect.${st.format}", Modifier.weight(1f), numbered = true,
                dim = shown == null)
        }
    }
}

/** Why Docker can't be reached: why, the command that starts it to copy, Try again, and all docker wrote, folded. */
@Composable
private fun DockerError(st: DockerState, f: Msg.DockerFault) {
    val c = LocalBaude.current
    val clip = LocalClipboardManager.current
    var more by remember(f) { mutableStateOf(false) }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp, Alignment.CenterVertically), horizontalAlignment = Alignment.CenterHorizontally) {
        Icon(Adwaita.DialogWarning, null, tint = c.dim, modifier = Modifier.size(48.dp.icon))
        Text(f.title.orEmpty(), style = Type.title, color = c.fg, textAlign = TextAlign.Center)
        if (!f.why.isNullOrBlank()) SelectionContainer { Text(f.why, style = Type.body, color = c.dim, textAlign = TextAlign.Center) }
        if (!f.fix.isNullOrBlank()) Row(Modifier.clip(RoundedCornerShape(Gap.codeRadius)).background(c.raised)
            .padding(start = 10.dp, top = 2.dp, bottom = 2.dp, end = 2.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            SelectionContainer(Modifier.weight(1f, fill = false)) { Text(f.fix, style = Type.mono, color = c.fg) }
            IconButton(Adwaita.EditCopy, Wording.Common.copy) {
                clip.setText(AnnotatedString(f.fix))
                toast(Wording.Kube.copiedTryAgain)
            }
        }
        Button(Wording.Common.tryAgain) { st.load(switch = true) }
        val said = f.said.orEmpty()
        if (said.isNotBlank() && said.trim() != f.why) {
            Row(Modifier.clip(RoundedCornerShape(6.dp)).clickable { more = !more }.padding(4.dp), verticalAlignment = Alignment.CenterVertically) {
                Icon(if (more) Adwaita.PanDown else Adwaita.PanEnd, null, tint = c.fg, modifier = Modifier.size(16.dp.icon))
                Text(Wording.Docker.output, style = Type.body, color = c.fg)
            }
            if (more) SelectionContainer(Modifier.heightIn(max = 220.dp).verticalScroll(rememberScrollState())) {
                Text(said, style = Type.mono, color = c.fg)
            }
        }
    }
}

/** The table (KubeTable's look): a header to sort by, a row to inspect; a row ends with its log, a shell (a running
 * container's) and ⋮ (what can be done to it as it is). The columns scroll sideways together. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun DockerTable(st: DockerState, rows: List<Msg.DockerRow>) {
    val c = LocalBaude.current
    val k = kubeColours()
    val cols = st.columns
    val measure = rememberTextMeasurer()
    val density = LocalDensity.current
    val widths = remember(rows, cols) {
        cols.indices.map { i ->
            var w = with(density) { measure.measure(cols[i].title.orEmpty(), Type.bold, softWrap = false).size.width.toDp() } + 12.dp
            for (r in rows) w = maxOf(w, with(density) {
                measure.measure(r.cells.orEmpty().getOrElse(i) { "" }, Type.body, softWrap = false).size.width.toDp()
            } + 12.dp)
            if (cols[i].kind == "name") maxOf(w, k.nameWidth.coerceAtMost(160.dp)) else minOf(w, 260.dp)
        }
    }
    val buttons = 16.dp.icon * 3 + 30.dp
    val total = widths.fold(0.dp) { a, b -> a + b } + buttons
    val scroll = rememberScrollState()
    Column {
        Row(Modifier.horizontalScroll(scroll).width(total).background(c.raised)) {
            for (i in cols.indices) {
                Row(Modifier.width(widths[i]).clickable { if (st.sortCol == i) st.sortDesc = !st.sortDesc else { st.sortCol = i; st.sortDesc = false } }
                    .padding(horizontal = 6.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(cols[i].title.orEmpty(), style = Type.bold, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f, fill = false))
                    if (st.sortCol == i) Icon(if (st.sortDesc) Adwaita.PanDown else Adwaita.GoUp, null, tint = c.fg,
                        modifier = Modifier.size(16.dp.icon))
                }
            }
            Box(Modifier.width(buttons))
        }
        LazyColumn(Modifier.horizontalScroll(scroll).width(total)) {
            items(rows, key = { it.id.orEmpty() }) { row ->
                val on = row.id == st.selected?.id
                Row(Modifier.width(total).background(if (on) c.rowSelected else Color.Transparent)
                    .combinedClickable(onClick = { st.select(row) }).padding(vertical = k.rowPad),
                    verticalAlignment = Alignment.CenterVertically) {
                    for (i in cols.indices) {
                        val tone = when (row.tones.orEmpty().getOrElse(i) { "" }) {
                            "ok" -> c.success
                            "warn" -> c.warning
                            "bad" -> c.error
                            else -> c.fg
                        }
                        val tip = row.tips.orEmpty().getOrElse(i) { "" }
                        Box(Modifier.width(widths[i]).combinedClickable(onClick = { st.select(row) },
                            onLongClick = if (tip.isNotEmpty()) ({ toast(tip) }) else null).padding(horizontal = 6.dp)) {
                            Text(row.cells.orEmpty().getOrElse(i) { "" }, style = Type.body, color = tone, maxLines = 1,
                                overflow = TextOverflow.Ellipsis)
                        }
                    }
                    Box(Modifier.width(buttons)) { RowButtons(st, row) }
                }
            }
        }
    }
}

/** A row's last cell: its log, a shell in it (a running container's, on a host that runs terminals), and ⋮. */
@Composable
private fun RowButtons(st: DockerState, row: Msg.DockerRow) {
    val c = LocalBaude.current
    var open by remember { mutableStateOf(false) }
    @Composable
    fun button(icon: ImageVector, label: String, shown: Boolean, onClick: () -> Unit) {
        Box((if (shown) Modifier.clickable(onClick = onClick) else Modifier.alpha(0f)).padding(horizontal = 4.dp, vertical = 2.dp)) {
            Icon(icon, label, tint = c.fg, modifier = Modifier.size(16.dp.icon))
        }
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        button(Adwaita.Logs, Wording.Kube.logs, row.logs == true) { st.showLogs(row) }
        button(Adwaita.UtilitiesTerminal, Wording.Kube.shell, !row.shell.isNullOrEmpty() && takes(st.hostId, "terminal")) { st.showShell(row) }
        Box {
            button(Adwaita.ViewMore, Wording.Common.removeEllipsis, !row.actions.isNullOrEmpty()) { open = true }
            Popover(open, { open = false }) {
                for (a in row.actions.orEmpty()) MenuChoice(when (a) {
                    "start" -> Wording.NewSession.start
                    "stop" -> Wording.Common.stop
                    "restart" -> Wording.SharedSettings.restart
                    else -> Wording.Common.removeEllipsis
                }, false) { open = false; st.act(row, a) }
            }
        }
    }
}

/** The dock under the table: a strip of tabs (each a log or a shell: its icon, "<name> · <context>", ×), the picked
 * one under it. */
@Composable
private fun DockerDock(st: DockerState, height: androidx.compose.ui.unit.Dp) {
    val c = LocalBaude.current
    Column(Modifier.fillMaxWidth().height(height)) {
        LazyRow(Modifier.fillMaxWidth(), contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = Gap.inside),
            verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(2.dp)) {
            items(st.docks.toList(), key = { System.identityHashCode(it) }) { t ->
                val (icon, row, ctx) = when (t) {
                    is DockerLogs -> Triple(Adwaita.Logs, t.row, t.context)
                    is DockerShell -> Triple(Adwaita.UtilitiesTerminal, t.row, t.context)
                    else -> return@items
                }
                Row(Modifier.clip(RoundedCornerShape(topStart = Gap.codeRadius, topEnd = Gap.codeRadius))
                    .background(if (st.picked === t) c.inside else Color.Transparent).clickable { st.picked = t }
                    .padding(start = 8.dp, end = 2.dp, top = 2.dp, bottom = 2.dp), verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Icon(icon, null, tint = c.fg, modifier = Modifier.size(16.dp.icon))
                    Text(midCut("${row.name} · $ctx", 36), style = Type.bold, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    IconButton(Adwaita.WindowClose, Wording.Common.close) { st.close(t) }
                }
            }
        }
        Column(Modifier.weight(1f).fillMaxWidth().inside(c)) {
            when (val p = st.picked) {
                is DockerLogs -> {
                    Row(Modifier.fillMaxWidth().heightIn(min = 34.dp).padding(start = 10.dp, end = 6.dp),
                        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text(p.state, style = Type.caption, color = c.dim, maxLines = 1, overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.weight(1f))
                        IconButton(Adwaita.GoBottom, Wording.Kube.followTip, background = if (p.follow) c.fg.copy(alpha = 0.10f) else null) {
                            p.follow = !p.follow
                        }
                    }
                    key(p) {
                        CodePane(p.text, "pod.log", Modifier.weight(1f).fillMaxWidth(), numbered = true, follow = p.follow,
                            onFollow = { if (it != p.follow) p.follow = it })
                    }
                }
                is DockerShell -> {
                    val said = p.term?.said ?: p.state
                    if (said.isNotEmpty()) Text(said, style = Type.caption, color = c.dim, maxLines = 1,
                        modifier = Modifier.padding(start = 10.dp, top = 6.dp, bottom = 4.dp))
                    p.term?.let { t -> key(t) { TerminalPane(t, Modifier.weight(1f).fillMaxWidth()) } }
                }
            }
        }
    }
}

/** Ask before removing it: what it is, its name, the context, and what goes with it. Nothing's removed unless you say
 * Remove. */
@Composable
private fun RemoveDocker(st: DockerState, row: Msg.DockerRow) {
    val c = LocalBaude.current
    val name = row.name.orEmpty()
    val page = st.tablePage?.key
    val heading = when (page) {
        "images" -> Wording.Docker.removeImageHeading(name)
        "volumes" -> Wording.Docker.removeVolumeHeading(name)
        "networks" -> Wording.Docker.removeNetworkHeading(name)
        "compose" -> Wording.Docker.removeComposeHeading(name)
        else -> Wording.Docker.removeContainerHeading(name)
    }
    val body = listOf(Wording.Docker.onContext(st.context.orEmpty()), when (page) {
        "images" -> Wording.Docker.removeImageBody
        "volumes" -> Wording.Docker.removeVolumeBody
        "networks" -> Wording.Docker.removeNetworkBody
        "compose" -> Wording.Docker.removeComposeBody
        else -> if (row.running == true) Wording.Docker.removeRunningBody else Wording.Docker.removeContainerBody
    })
    val done = { st.removing = null }
    AlertDialog(
        onDismissRequest = done,
        title = { Text(heading) },
        text = { Column(verticalArrangement = Arrangement.spacedBy(12.dp)) { for (p in body) Text(p, style = Type.body, color = c.fg) } },
        confirmButton = { Button(Wording.Common.remove, destructive = true) { done(); st.run(row, "remove") } },
        dismissButton = { Button(Wording.Common.cancel, onClick = done) },
        containerColor = c.dialog,
    )
}
