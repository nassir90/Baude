package dev.baude.android

import android.content.Context
import android.os.SystemClock
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
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
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay

/** How often a view that isn't watched reads its list again, and the pods' CPU and memory (kubedata.KUBE_REFRESH_S). */
const val KUBE_REFRESH_S = 10

private val PODS = Msg.KubePage(title = "Pods", resource = "pods", namespaced = true, served = true, optional = false,
    source = "kubectl")

/** What the host's sections' icon names (the desktop's, baude-kube-nodes-symbolic…) are drawn as here. */
fun kubeIcon(name: String?): ImageVector? = when (name?.removePrefix("baude-kube-")?.removeSuffix("-symbolic")) {
    "nodes" -> Adwaita.KubeNodes
    "workloads" -> Adwaita.KubeWorkloads
    "helm" -> Adwaita.KubeHelm
    "config" -> Adwaita.KubeConfig
    "network" -> Adwaita.KubeNetwork
    "storage" -> Adwaita.KubeStorage
    "namespaces" -> Adwaita.KubeNamespaces
    "events" -> Adwaita.KubeEvents
    "users" -> Adwaita.KubeUsers
    "crds" -> Adwaita.KubeCrds
    "scaling" -> Adwaita.KubeScaling
    "certificates" -> Adwaita.KubeCertificates
    "monitoring" -> Adwaita.KubeMonitoring
    else -> null
}

/** A text cut in the middle with … past n characters. */
fun midCut(s: String, n: Int): String = if (s.length > n) s.take((n - 1) / 2) + "…" + s.takeLast(n / 2) else s

/** An object's YAML as shown: whose it is, and the text (greyed while it's read again). */
class YamlShown(val uid: String, val title: String, val version: String, val text: String = "", val file: String = "object.yaml",
                val refs: List<Msg.KubeRef> = emptyList(), val loading: Boolean = true)

/** The state of the Kubernetes view (kubeview.KubeView's): the cluster read through a host, the page shown, what's
 * filtered, sorted and selected, the object's YAML, the pod's logs. */
class KubeState(val hostId: String) {
    var contexts by mutableStateOf<List<Msg.KubeContext>>(emptyList())
    var context by mutableStateOf<String?>(null)
    var nothing by mutableStateOf<String?>(null) // (no kubeconfig context: what the status line says)
    var sections by mutableStateOf<List<Msg.KubeSection>>(emptyList())
    var added by mutableStateOf<List<Msg.KubePage>>(emptyList()) // (custom resources opened from Definitions)
    var namespaces by mutableStateOf<List<String>>(emptyList())
    var nodes by mutableStateOf(0)
    var page by mutableStateOf(PODS)
    var tablePage by mutableStateOf<Msg.KubePage?>(null)
    var columns by mutableStateOf<List<Msg.KubeColumn>>(emptyList())
    var rows by mutableStateOf<List<Msg.KubeRow>>(emptyList())
    var metrics by mutableStateOf<Boolean?>(null)
    var nsShown by mutableStateOf<List<String>>(emptyList())
    var filter by mutableStateOf("")
    var sortCol by mutableStateOf(0)
    var sortDesc by mutableStateOf(false)
    var selected by mutableStateOf<String?>(null)
    var yaml by mutableStateOf<YamlShown?>(null)
    var logs by mutableStateOf<LogsShown?>(null)
    var deleting by mutableStateOf<Pair<Msg.KubeRow, Boolean>?>(null)
    var note by mutableStateOf<Pair<String, Long>?>(null)
    var progress by mutableStateOf<String?>(null)
    var loading by mutableStateOf(false)
    var fault by mutableStateOf<Msg.KubeFault?>(null)
    var live by mutableStateOf(false)
    var fade by mutableStateOf(false)
    var level by mutableStateOf("table") // (table, or categories)
    var folded by mutableStateOf(prefs().getStringSet("kube_folded", emptySet()).orEmpty().toSet())
    var wrap by mutableStateOf(prefs().getBoolean("kube_log_wrap", false))
    private var gen = 0
    private var watch: KubeFollow? = null
    private var pending: Pair<String, String>? = null // (name, namespace) to select once the page is in

    val connected get() = sections.isNotEmpty()

    /** Every kind the sidebar lists, in its category (custom resources opened from Definitions under it). */
    fun pagesOf(section: Msg.KubeSection): List<Msg.KubePage> = section.pages.orEmpty().flatMap { p ->
        if (p.resource == "customresourcedefinitions") listOf(p) + added else listOf(p)
    }

    fun allPages(): List<Msg.KubePage> = sections.flatMap { pagesOf(it) }

    fun served(p: Msg.KubePage) = allPages().firstOrNull { it.resource == p.resource }?.served != false

    fun open(context: String?, page: String?, search: String?, namespace: String?) {
        kube(hostId, Msg.Kube(what = "contexts")) { r ->
            contexts = r.contexts.orEmpty()
            val names = contexts.mapNotNull { it.name }
            if (r.error != null || names.isEmpty()) {
                nothing = if (r.error != null) r.error else Wording.Kube.noContext(r.where.orEmpty())
                return@kube
            }
            val last = prefs().getString("kube_context.$hostId", null)
            val want = context?.takeIf { it in names } ?: last?.takeIf { it in names }
                ?: contexts.firstOrNull { it.current == true }?.name ?: names.first()
            if (search != null) filter = search
            if (page != null) this.page = Msg.KubePage(title = page, resource = page, namespaced = true, served = true,
                optional = false, source = "kubectl")
            switchContext(want, namespace?.let { listOf(it) })
        }
    }

    /** Let go of everything running: the logs, the watch, and whatever's still to be heard. */
    fun stop() {
        gen++
        stopLogs()
        stopWatch()
    }

    fun switchContext(name: String, spaces: List<String>? = null) {
        context = name
        sections = emptyList()
        namespaces = emptyList()
        note = null
        nsShown = spaces ?: nsByContext["$hostId/$name"] ?: emptyList()
        nsByContext["$hostId/$name"] = nsShown
        prefs().edit().putString("kube_context.$hostId", name).apply()
        progress = Wording.Kube.connecting(name)
        hideYaml()
        closeLogs()
        stopWatch()
        fade = true
        loading = true
        val g = ++gen
        kube(hostId, Msg.Kube(what = "connect", context = name)) { r ->
            if (g != gen) return@kube
            val f = r.fault ?: r.error?.let { Msg.KubeFault(title = it, why = "", said = "") }
            if (f != null) return@kube failed(f)
            sections = r.sections.orEmpty()
            namespaces = r.namespaces.orEmpty().filter { it.isNotEmpty() }
            nodes = r.nodes?.toInt() ?: 0
            if (!served(page)) page = PODS
            else allPages().firstOrNull { it.resource == page.resource }?.let { page = it }
            load(switch = true)
        }
    }

    private fun failed(f: Msg.KubeFault) {
        loading = false
        progress = null
        stopWatch()
        fault = f
        rows = emptyList()
        fade = false
    }

    /** Read the page's objects (and, for pods and nodes, their CPU and memory); switch: it's another kind, so the
     * table fades until they're in. */
    fun load(quiet: Boolean = false, switch: Boolean = false) {
        val ctx = context ?: return
        stopWatch()
        if (switch) fade = true
        val p = page
        val g = ++gen
        loading = true
        if (!quiet) progress = Wording.Kube.reading(p.title.orEmpty().lowercase(), ctx)
        kube(hostId, Msg.Kube(what = "list", context = ctx, page = p.resource, crd = p.crd)) { r ->
            if (g != gen) return@kube
            val f = r.fault ?: r.error?.let { Msg.KubeFault(title = it, why = "", said = "") }
            if (f != null) return@kube failed(f)
            loaded(p, r.columns.orEmpty(), r.rows.orEmpty(), r.metrics, g)
        }
    }

    private fun loaded(p: Msg.KubePage, cols: List<Msg.KubeColumn>, list: List<Msg.KubeRow>, got: Boolean?, g: Int) {
        loading = false
        progress = null
        fault = null
        if (tablePage?.resource != p.resource || columns.map { it.title } != cols.map { it.title }) {
            sortCol = (if (p.resource == "events") cols.indexOfFirst { it.title == "Last Seen" } else
                cols.indexOfFirst { it.kind == "name" }).coerceAtLeast(0)
            sortDesc = false
        }
        tablePage = p
        columns = cols
        metrics = got
        rowsIn(list)
        fade = false
        if (p.source == "kubectl") startWatch(p, g)
    }

    /** The rows replaced: the one selected selected again (or the one an owner link asked for), its YAML read again if
     * the object has changed. */
    private fun rowsIn(list: List<Msg.KubeRow>) {
        rows = list
        val want = pending
        pending = null
        if (want != null) {
            val row = list.firstOrNull { it.name == want.first && it.namespace.orEmpty() == want.second }
            if (row != null) selectRow(row)
            else setNote(Wording.Kube.ownerMissing(want.first, tablePage?.title.orEmpty().lowercase(), context.orEmpty()))
            return
        }
        val y = yaml ?: return
        val row = list.firstOrNull { it.uid == y.uid } ?: return
        if (row.version != y.version) readYaml(row, true)
    }

    private fun startWatch(p: Msg.KubePage, g: Int) {
        val ctx = context ?: return
        var w: KubeFollow? = null
        w = KubeFollow(hostId, Msg.KubeFollow(req = KubeFollow.REQ, what = "watch", context = ctx, page = p.resource, crd = p.crd),
            onRows = { r ->
                if (watch === w && g == gen) {
                    if (r.metrics != null) metrics = r.metrics
                    rowsIn(r.rows.orEmpty())
                    if (!r.note.isNullOrEmpty()) { setNote(r.note); live = false } else live = true
                }
            },
            onEnd = { if (watch === w) { live = false; watch = null } },
            onError = { if (watch === w) { live = false; watch = null } },
            onUp = { up -> if (watch === w) live = up })
        watch = w
        if (!w.start()) watch = null
    }

    private fun stopWatch() {
        watch?.stop()
        watch = null
        live = false
    }

    /** The list read again if it isn't watched (and the pods' CPU and memory with it). */
    fun tick() {
        if (!loading && context != null && watch == null && !connectedFailed()) load(quiet = true)
    }

    private fun connectedFailed() = fault != null || !connected

    fun tryAgain() {
        val ctx = context ?: return
        if (!connected) switchContext(ctx, nsShown) else load(switch = true)
    }

    fun setNote(text: String, seconds: Long = 30) {
        note = text to SystemClock.elapsedRealtime() + seconds * 1000
        progress = null
    }

    // ---- kinds, namespaces
    fun pick(p: Msg.KubePage) {
        level = "table"
        if (p.resource == page.resource) return
        page = p
        hideYaml()
        if (context != null) load(switch = true)
    }

    fun fold(title: String, on: Boolean) {
        folded = if (on) folded + title else folded - title
        prefs().edit().putStringSet("kube_folded", folded).apply()
    }

    fun showNamespaces(names: List<String>) {
        nsShown = names.distinct()
        context?.let { nsByContext["$hostId/$it"] = nsShown }
        fade = true
        Mesh.later(150) { fade = false }
    }

    /** Show only ns's objects (a pod's namespace tapped, a namespace opened); null: every namespace's. */
    fun setNamespace(ns: String?) {
        if (page.namespaced == true) return showNamespaces(listOfNotNull(ns))
        nsShown = listOfNotNull(ns)
        context?.let { nsByContext["$hostId/$it"] = nsShown }
        page = allPages().firstOrNull { it.resource == "pods" } ?: PODS
        hideYaml()
        load(switch = true)
    }

    // ---- an object
    fun selectRow(row: Msg.KubeRow) {
        selected = row.uid
        readYaml(row, false)
    }

    private fun readYaml(row: Msg.KubeRow, again: Boolean) {
        val p = tablePage ?: return
        val ctx = context ?: return
        val kind = p.title.orEmpty().let { if (it.endsWith("s")) it.dropLast(1) else it }
        val title = "$kind ${row.name}" + if (!row.namespace.isNullOrEmpty()) " · ${row.namespace}" else ""
        val old = yaml?.takeIf { again && it.uid == row.uid }
        yaml = YamlShown(row.uid.orEmpty(), title, row.version.orEmpty(), old?.text.orEmpty(), old?.file ?: "object.yaml",
            old?.refs.orEmpty(), true)
        val uid = row.uid.orEmpty()
        kube(hostId, Msg.Kube(what = "yaml", context = ctx, page = p.resource, crd = p.crd, namespace = row.namespace.orEmpty(),
            name = row.name)) { r ->
            val y = yaml ?: return@kube
            if (y.uid != uid) return@kube
            yaml = YamlShown(uid, title, row.version.orEmpty(), r.text ?: r.error.orEmpty(), r.file ?: "object.yaml",
                r.refs.orEmpty(), false)
        }
    }

    fun hideYaml() {
        yaml = null
        selected = null
    }

    /** A name in an object's YAML, tapped: that object (its page, it selected); a namespace, its objects. */
    fun openRef(ref: Msg.KubeRef) {
        val kind = ref.kind.orEmpty()
        if (kind == "Namespace") return setNamespace(ref.name)
        goTo(CLIMB[kind] ?: resourceOf(kind), kind, ref.name.orEmpty(), ref.namespace.orEmpty())
    }

    /** What controls a pod (its Controlled By, tapped): the Deployment of a ReplicaSet, the CronJob of a Job, when it
     * has one; its page, with it selected. */
    fun goToOwner(row: Msg.KubeRow) {
        val ctx = context ?: return
        val kind = row.ownerKind.orEmpty()
        val name = row.ownerName.orEmpty()
        progress = Wording.Kube.finding(kind, name)
        kube(hostId, Msg.Kube(what = "owner", context = ctx, kind = kind, name = name, namespace = row.namespace.orEmpty())) { r ->
            if (ctx != context) return@kube
            goTo(r.resource.orEmpty(), r.kind ?: kind, r.name ?: name, row.namespace.orEmpty())
        }
    }

    private fun goTo(resource: String, kind: String, name: String, ns: String) {
        progress = null
        val p = allPages().firstOrNull { it.resource.orEmpty().split(".")[0] == resource && (it.optional != true || it.served == true) }
        if (p == null) return setNote(Wording.Kube.noPage(kind, name))
        if (ns.isNotEmpty() && p.namespaced == true && nsShown.isNotEmpty() && ns !in nsShown) showNamespaces(nsShown + ns)
        filter = ""
        pending = name to (if (p.namespaced == true) ns else "")
        page = p
        hideYaml()
        level = "table"
        load(switch = true)
    }

    /** A row tapped twice: a namespace's pods; a definition's objects as a page of their own. */
    fun activate(row: Msg.KubeRow) {
        val p = tablePage ?: return
        val ctx = context ?: return
        if (p.resource == "namespaces") return setNamespace(row.name)
        if (p.resource != "customresourcedefinitions") return
        kube(hostId, Msg.Kube(what = "list", context = ctx, crd = row.name)) { r ->
            val made = r.page ?: return@kube
            if (allPages().none { it.resource == made.resource }) added = added + made
            page = allPages().first { it.resource == made.resource }
            hideYaml()
            level = "table"
            load(switch = true)
        }
    }

    // ---- a pod's logs
    fun showLogs(row: Msg.KubeRow) {
        val ctx = context ?: return
        val cs = row.containers.orEmpty().map { it.name.orEmpty() to (it.init == true) }
        if (cs.isEmpty()) return setNote(Wording.Kube.noContainers(row.name.orEmpty()))
        val names = cs.map { it.first }
        val pick = row.default?.takeIf { it in names } ?: cs.firstOrNull { !it.second }?.first ?: names.first()
        stopLogs()
        logs = LogsShown(ctx, row.namespace.orEmpty(), row.name.orEmpty(), cs, pick).also { tail(it) }
    }

    /** The container's log from the start: this run's followed, or with Previous its last run's. */
    fun tail(l: LogsShown) {
        l.tail?.stop()
        l.text = ""
        l.lines = 1
        l.follow = true
        l.state = if (l.previous) Wording.Kube.previousRun else Wording.Kube.following
        val f = KubeFollow(hostId, Msg.KubeFollow(req = KubeFollow.REQ, what = "logs", context = l.context, namespace = l.namespace,
            pod = l.pod, container = l.container, previous = l.previous),
            onText = { l.append(it) },
            onEnd = { e ->
                l.tail = null
                val code = e.code?.toInt() ?: 0
                if (code != 0) l.state = Wording.Kube.kubectlExited(code) else if (!l.previous) l.state = Wording.Kube.ended
            },
            onError = { l.tail = null; l.state = Wording.Kube.kubectlError(it) })
        l.tail = f
        if (!f.start()) { l.tail = null; l.state = Wording.Kube.kubectlError(Connection.NOT_CONNECTED) }
    }

    private fun stopLogs() {
        logs?.tail?.stop()
        logs?.tail = null
    }

    fun closeLogs() {
        stopLogs()
        logs = null
    }

    // ---- deleting a pod: the view's only writes, each confirmed first
    fun delete(row: Msg.KubeRow, force: Boolean) {
        val ctx = context ?: return
        val name = row.name.orEmpty()
        setNote(if (force) Wording.Kube.forceDeleting(name) else Wording.Kube.deleting(name))
        kube(hostId, Msg.Kube(what = "delete", context = ctx, namespace = row.namespace.orEmpty(), name = name, force = force)) { r ->
            if (ctx != context) return@kube
            setNote(r.said ?: r.error.orEmpty())
            load(quiet = true)
        }
    }

    /** The status line: the progress; else the note, how many are shown, the context, whether it's live, the metrics and
     * that it's read-only. */
    fun status(shown: Int): String {
        nothing?.let { return it }
        progress?.let { return it }
        val ctx = context.orEmpty()
        fault?.let { return "$ctx: ${it.title}" }
        val p = tablePage ?: return ""
        val what = p.title.orEmpty().lowercase()
        var count = if (shown != rows.size) Wording.Kube.countOf(shown, what, rows.size) else Wording.Kube.count(shown, what)
        if (nsShown.isNotEmpty() && p.namespaced == true) count = Wording.Kube.inNamespaces(count, nsSummary())
        val fresh = note?.takeIf { it.second > SystemClock.elapsedRealtime() }?.first
        val m = if (p.resource == "pods" || p.resource == "nodes") {
            if (metrics == true) Wording.Kube.metrics(KUBE_REFRESH_S) else Wording.Kube.noMetrics
        } else null
        return listOfNotNull(fresh, count, ctx, if (live) Wording.Kube.live else null, m, Wording.Kube.readOnly)
            .filter { it.isNotEmpty() }.joinToString(" · ")
    }

    /** The namespaces shown, in a few words: All namespaces; shop; shop, jobs; 3 namespaces. */
    fun nsSummary(): String = when {
        nsShown.isEmpty() -> Wording.Kube.allNamespaces
        nsShown.size <= 2 -> nsShown.joinToString(", ")
        else -> Wording.Kube.nNamespaces(nsShown.size)
    }

    companion object {
        private val nsByContext = HashMap<String, List<String>>() // (memory only, as the desktop's)
        private val CLIMB = mapOf("ReplicaSet" to "replicasets", "Job" to "jobs")

        private fun prefs() = Mesh.app.getSharedPreferences("settings", Context.MODE_PRIVATE)

        /** The resource kubectl calls a kind by: Ingress, ingresses (kubedata.kube_resource). */
        fun resourceOf(kind: String): String {
            val k = kind.lowercase()
            return when {
                k.endsWith("endpoints") -> k
                k.endsWith("s") || k.endsWith("x") || k.endsWith("ch") || k.endsWith("sh") -> k + "es"
                k.endsWith("y") && k.length > 1 && k[k.length - 2] !in "aeiou" -> k.dropLast(1) + "ies"
                else -> k + "s"
            }
        }
    }
}

/** The kind colours as the theme has them: ok, starting, bad. */
@Composable
fun kubeColours() = if (LocalBaude.current.window == DarkBaude.window) Tokens.dark.kube else Tokens.light.kube

/** ☰ → Kubernetes… (kubeview.KubeView): a cluster's objects as Freelens lists them, read through the host. The title bar
 * and under it the context, the namespaces and the filter; the table of the kind shown (back: the categories to pick
 * from); a row's YAML in the table's place; a pod's logs docked under. Back closes the logs, then the YAML, then goes
 * to the categories, then leaves. */
@Composable
fun KubeScreen(hostId: String, back: () -> Unit, context: String? = null, page: String? = null, search: String? = null,
               namespace: String? = null) {
    val c = LocalBaude.current
    val st = remember(hostId) { KubeState(hostId) }
    LaunchedEffect(st) { st.open(context, page, search, namespace) }
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
            st.logs != null -> st.closeLogs()
            st.yaml != null -> st.hideYaml()
            st.level == "table" -> st.level = "categories"
            else -> leave()
        }
    }
    BackHandler(onBack = up)
    st.deleting?.let { (row, force) -> DeletePod(st, row, force) }
    Column(Modifier.fillMaxSize().background(c.frame)) {
        Bar(Wording.Window.kubernetes, start = { IconButton(Adwaita.GoPrevious, Wording.Browser.back, onClick = up) }, end = {
            if (st.loading && st.progress != null) Box(Modifier.padding(horizontal = 9.dp)) { Spinner() }
            IconButton(Adwaita.ViewRefresh, Wording.Common.readAgain) { st.load() }
            IconButton(Adwaita.WindowClose, Wording.Images.close, onClick = leave)
        })
        if (st.level == "categories") Categories(st) else {
            Controls(st)
            Box(Modifier.weight(1f).navigationBarsPadding().inside(c)) { Body(st) }
        }
    }
}

/** The context, the namespaces shown and the filter, in a row (padding 6, spacing 6). */
@Composable
private fun Controls(st: KubeState) {
    Row(Modifier.fillMaxWidth().padding(6.dp), horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalAlignment = Alignment.CenterVertically) {
        val names = st.contexts.mapNotNull { it.name }
        if (names.isNotEmpty()) Picker(midCut(st.context.orEmpty(), 32), Wording.Kube.contextTip, names.map { it to it }, st.context) {
            if (it != st.context) st.switchContext(it)
        }
        Namespaces(st)
        SearchField(st.filter, { st.filter = it }, Wording.Kube.filter, Modifier.weight(1f))
    }
}

/** The namespaces button: what's shown, and a menu of a check each, any set of them at once, or all. */
@Composable
private fun Namespaces(st: KubeState) {
    val c = LocalBaude.current
    var open by remember { mutableStateOf(false) }
    val tip = if (st.nsShown.size > 2) st.nsShown.joinToString(", ") else Wording.Kube.namespacesTip
    Box {
        Row(Modifier.clip(RoundedCornerShape(6.dp)).clickable { open = true }.padding(start = 8.dp, end = 4.dp, top = 6.dp, bottom = 6.dp),
            verticalAlignment = Alignment.CenterVertically) {
            Text(st.nsSummary(), style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.widthIn(max = 140.dp))
            Icon(Adwaita.PanDown, tip, tint = c.fg, modifier = Modifier.size(18.dp.icon))
        }
        Popover(open, { open = false }) {
            MenuChoice(Wording.Kube.allNamespaces, st.nsShown.isEmpty(), leading = true) { st.showNamespaces(emptyList()) }
            Box(Modifier.fillMaxWidth().padding(vertical = 4.dp).height(1.dp).background(c.rule))
            Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState())) {
                for (ns in (st.namespaces + st.nsShown).distinct().sorted()) {
                    MenuChoice(ns, ns in st.nsShown, leading = true) {
                        st.showNamespaces(st.nsShown.filter { it != ns } + if (ns in st.nsShown) emptyList() else listOf(ns))
                    }
                }
            }
        }
    }
}

/** The categories (Freelens' sidebar) as a page: a category with several kinds is a heading that folds them; each kind a
 * row, the one shown marked, those the cluster doesn't serve dimmed (hold: why). */
@Composable
private fun Categories(st: KubeState) {
    val c = LocalBaude.current
    LazyColumn(Modifier.fillMaxSize().navigationBarsPadding().inside(c).padding(vertical = 4.dp)) {
        for (section in st.sections) {
            val title = section.title.orEmpty()
            val pages = st.pagesOf(section).filter { p ->
                (p.optional != true || p.served == true) && (p.resource != "nodes" || st.nodes > 1 || st.page.resource == "nodes")
            }
            if (pages.isEmpty()) continue
            val single = section.pages.orEmpty().size == 1 && section.pages!![0].title == title
            val icon = kubeIcon(section.icon)
            if (!single) item(key = "head:$title") {
                val folded = title in st.folded
                Row(Modifier.fillMaxWidth().padding(top = 9.dp).heightIn(min = 26.dp).clickable { st.fold(title, !folded) }
                    .padding(start = 6.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (icon != null) Icon(icon, null, tint = c.dim, modifier = Modifier.size(16.dp.icon))
                    Text(title, style = Type.folder, color = c.dim, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                    Icon(if (folded) Adwaita.PanEnd else Adwaita.PanDown, null, tint = c.dim, modifier = Modifier.size(16.dp.icon))
                }
            }
            if (!single && title in st.folded) continue
            for (p in pages) item(key = "page:${p.resource}") {
                val on = p.resource == st.page.resource
                val ok = p.served != false
                val ctx = st.context.orEmpty()
                val press = if (ok) Modifier.clickable { st.pick(p) } else Modifier.tip(Wording.Kube.notServed(ctx, p.title.orEmpty()))
                Row(Modifier.fillMaxWidth().padding(top = 1.dp).heightIn(min = 26.dp).background(if (on) c.rowSelected else c.inside)
                    .then(press).alpha(if (ok) 1f else 0.4f)
                    .padding(start = if (single) 6.dp else 6.dp + 16.dp.icon + 8.dp, end = 4.dp),
                    verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (single && icon != null) Icon(icon, null, tint = c.fg, modifier = Modifier.size(16.dp.icon))
                    Text(p.title.orEmpty(), style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
        }
    }
}

/** What's inside the frame at the table level: the table (or why it can't be read, or an object's YAML), its status
 * line, and the pod's logs docked under. */
@Composable
private fun Body(st: KubeState) {
    val c = LocalBaude.current
    val k = kubeColours()
    BoxWithConstraints(Modifier.fillMaxSize()) {
        val full = maxHeight
        var logsHeight by remember(st.logs?.pod) { mutableStateOf(maxOf(full / 2, k.logsMin)) }
        Column(Modifier.fillMaxSize()) {
            Column(Modifier.weight(1f).fillMaxWidth()) {
                val y = st.yaml
                if (y != null) YamlLevel(st, y)
                else {
                    val shown = remember(st.rows, st.nsShown, st.filter, st.sortCol, st.sortDesc, st.tablePage, st.columns) {
                        kubeRows(st)
                    }
                    Box(Modifier.weight(1f).fillMaxWidth().alpha(if (st.fade) 0.4f else 1f)) {
                        if (st.fault != null) ErrorState(st, st.fault!!) else if (st.tablePage != null) KubeTable(st, shown)
                    }
                    val status = st.status(shown.size)
                    if (status.isNotEmpty()) Text(status, style = Type.caption, color = c.dim, maxLines = 1,
                        overflow = TextOverflow.Ellipsis, modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp))
                }
            }
            st.logs?.let { l ->
                LogsPane(st, l, logsHeight, full) { logsHeight = it }
            }
        }
    }
}

/** What the cluster can't be read for (build_error): why, the command that fixes it to copy, Try again, and all kubectl
 * wrote, folded away. */
@Composable
private fun ErrorState(st: KubeState, f: Msg.KubeFault) {
    val c = LocalBaude.current
    val clip = LocalClipboardManager.current
    var more by remember(f) { mutableStateOf(false) }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp), verticalArrangement = Arrangement.spacedBy(10.dp, Alignment.CenterVertically),
        horizontalAlignment = Alignment.CenterHorizontally) {
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
        Button(Wording.Common.tryAgain) { st.tryAgain() }
        val said = f.said.orEmpty()
        if (said.isNotBlank() && said.trim() != f.why) {
            Row(Modifier.clip(RoundedCornerShape(6.dp)).clickable { more = !more }.padding(4.dp), verticalAlignment = Alignment.CenterVertically) {
                Icon(if (more) Adwaita.PanDown else Adwaita.PanEnd, null, tint = c.fg, modifier = Modifier.size(16.dp.icon))
                Text(Wording.Kube.kubectlOutput, style = Type.body, color = c.fg)
            }
            if (more) SelectionContainer(Modifier.heightIn(max = 220.dp).verticalScroll(rememberScrollState())) {
                Text(said, style = Type.mono, color = c.fg)
            }
        }
    }
}
