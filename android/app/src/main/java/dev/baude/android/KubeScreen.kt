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
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
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
import androidx.compose.runtime.mutableStateListOf
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

/** Home (kubedata.KUBE_HOME): the page the view opens on. */
private val HOME = Msg.KubePage(title = "Home", resource = "home", namespaced = true, served = true, optional = false,
    source = "summary")

/** What the host's sections' icon names (the desktop's, baude-kube-nodes-symbolic…) are drawn as here. */
fun kubeIcon(name: String?): ImageVector? = when (name?.removePrefix("baude-kube-")?.removeSuffix("-symbolic")) {
    "home" -> Adwaita.KubeHome
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
 * filtered, sorted and selected, the object's YAML, the dock's tabs (pods' logs, shells in pods). */
class KubeState(val hostId: String) {
    var contexts by mutableStateOf<List<Msg.KubeContext>>(emptyList())
    var context by mutableStateOf<String?>(null)
    /** The context shown's server: with its name, which cluster it is (one made again under its name is another). */
    var server: String? = null
    /** The context shown when it went from the kubeconfig: what the page says, till another's picked. */
    var removed by mutableStateOf<String?>(null)
    private var following: KubeFollow? = null // (the kubeconfig's contexts, followed while the page is open)
    private var noneYet = false // (the kubeconfig had no context: the first one added is shown)
    var nothing by mutableStateOf<String?>(null) // (no kubeconfig context: what the status line says)
    var sections by mutableStateOf<List<Msg.KubeSection>>(emptyList())
    var added by mutableStateOf<List<Msg.KubePage>>(emptyList()) // (custom resources opened from Definitions)
    var namespaces by mutableStateOf<List<String>>(emptyList())
    var nodes by mutableStateOf(0)
    var page by mutableStateOf(HOME)
    var tablePage by mutableStateOf<Msg.KubePage?>(null)
    var columns by mutableStateOf<List<Msg.KubeColumn>>(emptyList())
    var rows by mutableStateOf<List<Msg.KubeRow>>(emptyList())
    var metrics by mutableStateOf<Boolean?>(null)
    var summary by mutableStateOf<Msg.KubeSummary?>(null) // (Home's, as the host added it up)
    var history by mutableStateOf<List<Msg.KubeSample>>(emptyList()) // (Home's samples so far, oldest first)
    private var historyOf: String? = null // (whose they are: context and namespaces)
    var nsShown by mutableStateOf<List<String>?>(null) // (null: every namespace's)
    var filter by mutableStateOf("")
    var sortCol by mutableStateOf(0)
    var sortDesc by mutableStateOf(false)
    val colFilters = ColumnFilters("kube_hidden") // (a column's values, ticked: ColumnFilters)
    var selected by mutableStateOf<String?>(null)
    var yaml by mutableStateOf<YamlShown?>(null)
    val docks = mutableStateListOf<Any>().apply { addAll(parked.remove(hostId).orEmpty()) } // (LogsShown, ShellShown:
    // a tab each; the shells the page had before it was turned)
    var picked by mutableStateOf(docks.lastOrNull())
    var deleting by mutableStateOf<Pair<Msg.KubeRow, Boolean>?>(null)
    var note by mutableStateOf<Pair<String, Long>?>(null)
    var progress by mutableStateOf<String?>(null)
    var loading by mutableStateOf(false)
    var fault by mutableStateOf<Msg.KubeFault?>(null)
    var missing by mutableStateOf<Msg.Dep?>(null) // (kubectl isn't on the host: what the page shows instead)
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
        missing = null
        stopped = false
        kube(hostId, Msg.Kube(what = "contexts")) { r ->
            r.missing?.let { return@kube lacks(it) }
            takeContexts(r.contexts.orEmpty())
            val names = contexts.mapNotNull { it.name }
            noneYet = r.error == null && names.isEmpty()
            if (r.error != null || names.isEmpty()) {
                nothing = if (r.error != null) r.error else Wording.Kube.noContext(r.where.orEmpty())
                return@kube
            }
            if (search != null) filter = search
            if (page != null) this.page = Msg.KubePage(title = page, resource = page, namespaced = true, served = true,
                optional = false, source = "kubectl")
            switchContext(wanted(context), namespace?.let { listOf(it) })
        }
        followContexts()
    }

    /** The context to show: the one asked for, else the one shown last (the same cluster: not one made again under its
     * name), else the kubeconfig's current one, else the first. */
    private fun wanted(context: String?): String {
        val names = contexts.mapNotNull { it.name }
        val last = prefs().getString("kube_context.$hostId", null)
            ?.takeIf { l -> contexts.any { key(it) == l to prefs().getString("kube_context_server.$hostId", "").orEmpty() } }
        return context?.takeIf { it in names } ?: last ?: contexts.firstOrNull { it.current == true }?.name ?: names.first()
    }

    /** The host's kubeconfig followed while the page is open (kube_follow's contexts: sent again each time tools add or
     * delete clusters); the connection dropped, followed again in a while. */
    private fun followContexts() {
        following?.stop()
        var f: KubeFollow? = null
        f = KubeFollow(hostId, Msg.KubeFollow(req = KubeFollow.REQ, what = "contexts"),
            onContexts = { r -> if (following === f) contextsIn(r.contexts.orEmpty(), r.where.orEmpty()) },
            onEnd = { if (following === f) again(f) }, onError = { if (following === f) again(f) })
        following = f
        if (!f.start()) following = null
    }

    private fun again(f: KubeFollow?) {
        following = null
        Mesh.later(5_000) { if (following == null && f != null && !stopped) followContexts() }
    }

    private var stopped = false

    /** The host's contexts changed (one added, deleted, made again): the picker keeps up; the one shown gone, so is its
     * cluster (clusterRemoved). None shown yet, as there was none: the one wanted shown. */
    fun contextsIn(list: List<Msg.KubeContext>, where: String = "") {
        val before = context
        takeContexts(list)
        val keys = list.map { key(it) }.toSet()
        if (before != null && (before to server.orEmpty()) !in keys) clusterRemoved()
        else if (before == null && removed == null && noneYet && list.isNotEmpty()) {
            noneYet = false
            nothing = null
            switchContext(wanted(null))
        } else if (list.isEmpty() && removed == null && before == null) nothing = Wording.Kube.noContext(where)
    }

    /** The contexts as they are now; what was kept of one that's gone (its namespaces, that it was shown last) let go of. */
    private fun takeContexts(list: List<Msg.KubeContext>) {
        val keys = list.map { key(it) }.toSet()
        for (c in contexts) if (key(c) !in keys) {
            nsByContext.remove("$hostId/${c.name}/${c.server.orEmpty()}")
            if (prefs().getString("kube_context.$hostId", null) == c.name &&
                prefs().getString("kube_context_server.$hostId", "").orEmpty() == c.server.orEmpty())
                prefs().edit().remove("kube_context.$hostId").remove("kube_context_server.$hostId").apply()
        }
        contexts = list
    }

    /** The context shown went from the kubeconfig (a tool deleted its cluster, or made another under its name):
     * everything read from it let go of, its watch, logs and Home stopped, a shell in it told and ended; the page says
     * it was removed, with the contexts there are to pick from. No other is picked for you. */
    fun clusterRemoved() {
        val name = context ?: return
        gen++
        removed = name
        context = null
        server = null
        loading = false
        progress = null
        stopWatch()
        hideYaml()
        deleting = null
        for (t in docks) when (t) {
            is LogsShown -> if (t.context == name) { t.tail?.stop(); t.tail = null; t.state = Wording.Kube.removedState }
            is ShellShown -> if (t.context == name) {
                t.starting = null
                t.term?.let { term ->
                    if (term.ended == null) term.feed("\r\n\r\n${Wording.Kube.removedShell(name)}\r\n".toByteArray())
                    term.close()
                }
                t.state = Wording.Kube.removedState
            }
        }
        rows = emptyList()
        summary = null
        history = emptyList()
        historyOf = null
        tablePage = null
        fault = null
        fade = false
    }

    /** A request's or a follow's answer said the context shown isn't in the kubeconfig (it went before the follow said). */
    private fun gone(g: Int) {
        if (g == gen) clusterRemoved()
    }

    private fun key(c: Msg.KubeContext) = c.name.orEmpty() to c.server.orEmpty()

    /** Let go of everything running: the logs, the shells, the watch, and whatever's still to be heard. */
    fun stop() {
        gen++
        stopped = true
        following?.stop()
        following = null
        closeAll()
        stopWatch()
    }

    /** The page made again (the phone turned): everything let go of but the shells, kept for the page that follows. */
    fun park() {
        parked[hostId] = docks.filterIsInstance<ShellShown>()
        docks.removeAll { it is ShellShown }
        stop()
    }

    fun switchContext(name: String, spaces: List<String>? = null) {
        context = name
        server = contexts.firstOrNull { it.name == name }?.server.orEmpty()
        removed = null
        level = "table"
        sections = emptyList()
        namespaces = emptyList()
        note = null
        nsShown = spaces ?: nsByContext[nsKey()]
        nsByContext[nsKey()] = nsShown
        prefs().edit().putString("kube_context.$hostId", name).putString("kube_context_server.$hostId", server).apply()
        progress = Wording.Kube.connecting(name)
        hideYaml()
        closeAll()
        stopWatch()
        fade = true
        loading = true
        val g = ++gen
        kube(hostId, Msg.Kube(what = "connect", context = name, server = server)) { r ->
            if (g != gen) return@kube
            r.missing?.let { return@kube lacks(it) }
            if (r.removed == true) return@kube gone(g)
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

    /** kubectl isn't on the host: what's missing and how it's installed, in the table's place. */
    private fun lacks(dep: Msg.Dep) {
        gen++
        loading = false
        progress = null
        stopWatch()
        rows = emptyList()
        fade = false
        missing = dep
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
        if (p.source == "summary") return readSummary(p, ctx, g)
        kube(hostId, Msg.Kube(what = "list", context = ctx, server = server, page = p.resource, crd = p.crd)) { r ->
            if (g != gen) return@kube
            r.missing?.let { return@kube lacks(it) }
            if (r.removed == true) return@kube gone(g)
            val f = r.fault ?: r.error?.let { Msg.KubeFault(title = it, why = "", said = "") }
            if (f != null) return@kube failed(f)
            loaded(p, r.columns.orEmpty(), r.rows.orEmpty(), r.metrics, g)
        }
    }

    /** Home's totals, for the namespaces shown, as the host adds them up (kube's summary). */
    private fun readSummary(p: Msg.KubePage, ctx: String, g: Int) {
        val of = "$ctx/${nsShown?.joinToString(",")}"
        if (of != historyOf) {
            historyOf = of
            history = emptyList()
        }
        val since = history.lastOrNull()?.at // (only the samples since the newest here)
        kube(hostId, Msg.Kube(what = "summary", context = ctx, server = server, namespaces = nsShown, since = since)) { r ->
            if (g != gen) return@kube
            r.missing?.let { return@kube lacks(it) }
            if (r.removed == true) return@kube gone(g)
            val f = r.fault ?: r.error?.let { Msg.KubeFault(title = it, why = "", said = "") }
            if (f != null) return@kube failed(f)
            loading = false
            progress = null
            fault = null
            val began = r.summary?.began?.toDouble() ?: 0.0 // (a sample from before it: another cluster's, the name reused)
            val held = history.filter { (it.at?.toDouble() ?: 0.0) >= began }
            val last = held.lastOrNull()?.at?.toDouble()
            val merged = held + r.summary?.history.orEmpty().filter { h -> last == null || (h.at?.toDouble() ?: 0.0) > last }
            val edge = (merged.lastOrNull()?.at?.toDouble() ?: 0.0) - 60.0 * (r.summary?.minutes?.toDouble() ?: 0.0)
            history = merged.filter { (it.at?.toDouble() ?: 0.0) >= edge }
            summary = r.summary
            metrics = r.summary?.metrics
            tablePage = p
            rows = emptyList()
            fade = false
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
        colFilters.load(p.resource)
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
        w = KubeFollow(hostId, Msg.KubeFollow(req = KubeFollow.REQ, what = "watch", context = ctx, server = server, page = p.resource,
            crd = p.crd),
            onRows = { r ->
                if (watch === w && g == gen) {
                    if (r.metrics != null) metrics = r.metrics
                    rowsIn(r.rows.orEmpty())
                    if (!r.note.isNullOrEmpty()) setNote(r.note)
                }
            },
            onEnd = { e -> if (watch === w) { watch = null; if (e.removed == true) gone(g) } },
            onError = { if (watch === w) watch = null })
        watch = w
        if (!w.start()) watch = null
    }

    private fun stopWatch() {
        watch?.stop()
        watch = null
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

    /** Show these namespaces' objects (in the order picked; empty for none), or every namespace's (null). */
    fun showNamespaces(names: List<String>?) {
        nsShown = names?.distinct()
        context?.let { nsByContext[nsKey()] = nsShown }
        if (page.source == "summary") return load(switch = true) // (Home's totals: the host's to add up again)
        fade = true
        Mesh.later(150) { fade = false }
    }

    /** Show only ns's objects (a pod's namespace tapped, a namespace opened); null: every namespace's. */
    fun setNamespace(ns: String?) {
        if (page.namespaced == true) return showNamespaces(ns?.let { listOf(it) })
        nsShown = ns?.let { listOf(it) }
        context?.let { nsByContext[nsKey()] = nsShown }
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
        val g = gen
        kube(hostId, Msg.Kube(what = "yaml", context = ctx, server = server, page = p.resource, crd = p.crd,
            namespace = row.namespace.orEmpty(), name = row.name)) { r ->
            if (r.removed == true) return@kube gone(g)
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
        val g = gen
        kube(hostId, Msg.Kube(what = "owner", context = ctx, server = server, kind = kind, name = name,
            namespace = row.namespace.orEmpty())) { r ->
            if (r.removed == true) return@kube gone(g)
            if (ctx != context) return@kube
            goTo(r.resource.orEmpty(), r.kind ?: kind, r.name ?: name, row.namespace.orEmpty())
        }
    }

    private fun goTo(resource: String, kind: String, name: String, ns: String) {
        progress = null
        val p = allPages().firstOrNull { it.resource.orEmpty().split(".")[0] == resource && (it.optional != true || it.served == true) }
        if (p == null) return setNote(Wording.Kube.noPage(kind, name))
        nsShown?.let { if (ns.isNotEmpty() && p.namespaced == true && ns !in it) showNamespaces(it + ns) }
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
        val g = gen
        kube(hostId, Msg.Kube(what = "list", context = ctx, server = server, crd = row.name)) { r ->
            if (r.removed == true) return@kube gone(g)
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
        docks.filterIsInstance<LogsShown>().firstOrNull { it.context == ctx && it.namespace == row.namespace.orEmpty() &&
            it.pod == row.name.orEmpty() }?.let { picked = it; return }
        val pick = row.default?.takeIf { it in names } ?: cs.firstOrNull { !it.second }?.first ?: names.first()
        LogsShown(ctx, row.namespace.orEmpty(), row.name.orEmpty(), cs, pick, server.orEmpty())
            .also { docks.add(it); picked = it; tail(it) }
    }

    /** The container's log from the start: this run's followed, or with Previous its last run's. */
    fun tail(l: LogsShown) {
        l.tail?.stop()
        l.text = ""
        l.lines = 1
        l.follow = true
        l.state = if (l.previous) Wording.Kube.previousRun else Wording.Kube.following
        val f = KubeFollow(hostId, Msg.KubeFollow(req = KubeFollow.REQ, what = "logs", context = l.context, server = l.server,
            namespace = l.namespace, pod = l.pod, container = l.container, previous = l.previous),
            onText = { l.append(it) },
            onEnd = { e ->
                l.tail = null
                val code = e.code?.toInt() ?: 0
                if (e.removed == true) l.state = Wording.Kube.removedState
                else if (code != 0) l.state = Wording.Kube.kubectlExited(code) else if (!l.previous) l.state = Wording.Kube.ended
            },
            onError = { l.tail = null; l.state = Wording.Kube.kubectlError(it) })
        l.tail = f
        if (!f.start()) { l.tail = null; l.state = Wording.Kube.kubectlError(Connection.NOT_CONNECTED) }
    }

    /** A tab of the dock closed: what it ran stopped (kubectl; the shell in the pod); the one beside it picked. */
    fun close(tab: Any) {
        val at = docks.indexOf(tab)
        if (at < 0) return
        when (tab) {
            is LogsShown -> { tab.tail?.stop(); tab.tail = null }
            is ShellShown -> tab.term?.close()
        }
        docks.removeAt(at)
        if (picked === tab) picked = docks.getOrNull(minOf(at, docks.size - 1))
    }

    fun closeAll() {
        for (t in docks.toList()) close(t)
    }

    // ---- a shell in a pod (Multiplex's terminal: kubectl exec -it, run on the host by the terminal op)
    /** A shell in a pod under the table, in its default container (as its logs pick one), of its running ones (an init
     * container's has ended); the logs, or another pod's shell, closed first. */
    fun showShell(row: Msg.KubeRow) {
        val ctx = context ?: return
        val names = row.containers.orEmpty().filter { it.init != true }.mapNotNull { it.name }
        if (names.isEmpty()) return setNote(Wording.Kube.noContainers(row.name.orEmpty()))
        ShellShown(ctx, row.namespace.orEmpty(), row.name.orEmpty(), names,
            row.default?.takeIf { it in names } ?: names.first(), server.orEmpty())
            .also { docks.add(it); picked = it; startShell(it) }
    }

    /** kubectl exec -it in the container picked: bash if it has it, else sh. The one before is ended first. Only once
     * the host says its cluster's still there as it was shown (its server too: kube's check), and only on that server
     * (kubectl's --server); failed at once on a kubeconfig caught half written, run once more (kubedock's again). */
    fun startShell(s: ShellShown) {
        s.term?.close()
        s.term = null
        s.state = ""
        val token = Any().also { s.starting = it }
        kube(hostId, Msg.Kube(what = "check", context = s.context, server = s.server)) { r ->
            if (s.starting !== token || s !in docks) return@kube
            s.starting = null
            if (r.removed == true) {
                if (s.context == context && s.server == server.orEmpty()) clusterRemoved() else s.state = Wording.Kube.removedState
                return@kube
            }
            openShell(s)
        }
    }

    private fun openShell(s: ShellShown) {
        val target = listOf("--context", s.context) + (if (s.server.isNotEmpty()) listOf("--server=${s.server}") else emptyList())
        val argv = listOf("kubectl") + target + listOf("exec", "-it", "-n", s.namespace, s.pod, "-c", s.container, "--",
            "sh", "-c", "command -v bash >/dev/null 2>&1 && exec bash || exec sh")
        val began = System.currentTimeMillis()
        s.term = HostTerm(hostId, argv, "${s.pod} · ${s.namespace} · ${s.context}").also { t ->
            t.onEnd = { code ->
                if (s.term === t) {
                    if (code != 0 && !s.retried && System.currentTimeMillis() - began < 3_000 &&
                        KUBE_BROKEN.containsMatchIn(t.screen.text(all = true))) {
                        s.retried = true
                        startShell(s)
                    } else s.state = Wording.Kube.kubectlExited(code)
                }
            }
            Terms.add(t)
        }
    }


    // ---- deleting a pod: the view's only writes, each confirmed first
    fun delete(row: Msg.KubeRow, force: Boolean) {
        val ctx = context ?: return
        val name = row.name.orEmpty()
        setNote(if (force) Wording.Kube.forceDeleting(name) else Wording.Kube.deleting(name))
        val g = gen
        kube(hostId, Msg.Kube(what = "delete", context = ctx, server = server, namespace = row.namespace.orEmpty(), name = name,
            force = force)) { r ->
            if (r.removed == true) return@kube gone(g)
            if (ctx != context) return@kube
            setNote(r.said ?: r.error.orEmpty())
            load(quiet = true)
        }
    }

    /** The status line: the progress; else the note, how many are shown and the metrics. */
    fun status(shown: Int): String {
        if (missing != null) return ""
        removed?.let { return Wording.Kube.removedStatus(it) }
        nothing?.let { return it }
        progress?.let { return it }
        val ctx = context.orEmpty()
        fault?.let { return "$ctx: ${it.title}" }
        val p = tablePage ?: return ""
        val fresh0 = note?.takeIf { it.second > SystemClock.elapsedRealtime() }?.first
        if (p.source == "summary") return listOfNotNull(fresh0, if (metrics == true) Wording.Kube.metrics(KUBE_REFRESH_S)
            else Wording.Kube.noMetrics).joinToString(" · ")
        val what = p.title.orEmpty().lowercase()
        var count = if (shown != rows.size) Wording.Kube.countOf(shown, what, rows.size) else Wording.Kube.count(shown, what)
        if (nsShown != null && p.namespaced == true) count = Wording.Kube.inNamespaces(count, nsSummary())
        val fresh = note?.takeIf { it.second > SystemClock.elapsedRealtime() }?.first
        val m = if (p.resource == "pods" || p.resource == "nodes") {
            if (metrics == true) Wording.Kube.metrics(KUBE_REFRESH_S) else Wording.Kube.noMetrics
        } else null
        return listOfNotNull(fresh, count, m)
            .filter { it.isNotEmpty() }.joinToString(" · ")
    }

    /** The namespaces shown, in a few words: All namespaces; shop; shop, jobs; 3 namespaces; 0 namespaces. */
    fun nsSummary(): String = nsShown.let { n ->
        when {
            n == null -> Wording.Kube.allNamespaces
            n.size in 1..2 -> n.joinToString(", ")
            else -> Wording.Kube.nNamespaces(n.size)
        }
    }

    /** Where the namespaces shown in the context shown are kept: by its host, name and server (kube_key). */
    private fun nsKey() = "$hostId/$context/${server.orEmpty()}"

    companion object {
        private val nsByContext = HashMap<String, List<String>?>() // (memory only, as the desktop's)
        private val CLIMB = mapOf("ReplicaSet" to "replicasets", "Job" to "jobs")
        /** What kubectl says of a kubeconfig it can't read (lookupkube._KUBE_BROKEN): one caught half written. */
        private val KUBE_BROKEN = Regex("error loading config file", RegexOption.IGNORE_CASE)

        private fun prefs() = Mesh.app.getSharedPreferences("settings", Context.MODE_PRIVATE)
        /** A host's shells, kept while its page is made again. */
        private val parked = mutableMapOf<String, List<ShellShown>>()

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
fun kubeColours() = if (LocalBaude.current.dark) Tokens.dark.kube else Tokens.light.kube

/** ☰ → Kubernetes… (kubeview.KubeView): a cluster's objects as Freelens lists them, read through the host. The title bar
 * and under it the context, the namespaces and the filter; the table of the kind shown (back: the categories to pick
 * from); a row's YAML in the table's place; pods' logs and shells docked under, a tab each. Back closes the picked tab,
 * then the YAML, then goes
 * to the categories, then leaves. */
@Composable
fun KubeScreen(hostId: String, back: () -> Unit, context: String? = null, page: String? = null, search: String? = null,
               namespace: String? = null, state: KubeState? = null) {
    val c = LocalBaude.current
    val st = state ?: remember(hostId) { KubeState(hostId) }
    LaunchedEffect(st) { st.open(context, page, search, namespace) }
    LaunchedEffect(st) {
        while (true) {
            delay(KUBE_REFRESH_S * 1000L)
            st.tick()
        }
    }
    val activity = androidx.compose.ui.platform.LocalContext.current as? android.app.Activity
    DisposableEffect(st) { onDispose { if (activity?.isChangingConfigurations == true) st.park() else st.stop() } }
    val leave = { st.stop(); back() }
    val up = {
        when {
            st.level == "contexts" -> st.level = "table"
            st.picked != null -> st.close(st.picked!!)
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
        if (st.level == "categories") Categories(st)
        else if (st.level == "contexts") ContextsLevel(st)
        else {
            Controls(st)
            Box(Modifier.weight(1f).navigationBarsPadding().imePadding()) { Body(st) }
        }
    }
}

/** The context, the namespaces shown and the filter, in a row (padding 6, spacing 6). */
@Composable
private fun Controls(st: KubeState) {
    Row(Modifier.fillMaxWidth().padding(6.dp), horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalAlignment = Alignment.CenterVertically) {
        if (st.contexts.isNotEmpty()) ContextButton(st)
        Namespaces(st)
        SearchField(st.filter, { st.filter = it }, Wording.Kube.filter, Modifier.weight(1f))
    }
}

/** The context button (pickers.ContextPicker's face): the context shown, or "Choose a context", dim, when none is
 * (the one shown was removed); tapped, the contexts to pick from, in the page (ContextsLevel). */
@Composable
private fun ContextButton(st: KubeState, centred: Boolean = false) {
    val c = LocalBaude.current
    val face = st.context
    Row(Modifier.clip(RoundedCornerShape(6.dp)).then(if (centred) Modifier.background(c.raised) else Modifier)
        .clickable { st.level = "contexts" }.padding(start = 8.dp, end = 4.dp, top = 6.dp, bottom = 6.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Text(face?.let { midCut(it, 32) } ?: Wording.Kube.pickContext, style = Type.body, color = if (face != null) c.fg else c.dim,
            maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.widthIn(max = 180.dp))
        Icon(Adwaita.PanDown, Wording.Kube.contextTip, tint = c.fg, modifier = Modifier.size(16.dp.icon))
    }
}

/** The contexts to pick from, in the page (pickers.ContextPicker's list): by name (numbers by their value), the one
 * shown ticked; past CONTEXTS_SEARCHED of them, a search over them and their groups' names over them (context_groups).
 * It keeps up as tools add and delete clusters: a row is added or taken out where it is. */
@Composable
private fun ContextsLevel(st: KubeState) {
    val c = LocalBaude.current
    var query by remember { mutableStateOf("") }
    val names = st.contexts.mapNotNull { it.name }.distinct().sortedWith(contextOrder)
    val many = names.size > CONTEXTS_SEARCHED
    val groups = if (many) contextGroups(names) else emptyMap()
    val words = query.lowercase().split(Regex("\\s+")).filter { it.isNotEmpty() }
    val shown = names.filter { n -> words.all { it in n.lowercase() } }
    Column(Modifier.fillMaxSize().navigationBarsPadding().imePadding().inside(c)) {
        if (many) SearchField(query, { query = it }, Wording.Kube.findContext, Modifier.fillMaxWidth().padding(6.dp))
        LazyColumn(Modifier.fillMaxSize().padding(vertical = 4.dp)) {
            if (shown.isEmpty()) item(key = "none") {
                Text(Wording.Kube.noContextMatch, style = Type.body, color = c.dim, textAlign = TextAlign.Center,
                    modifier = Modifier.fillMaxWidth().padding(vertical = 12.dp))
            }
            shown.forEachIndexed { i, name ->
                val group = groups[name].orEmpty()
                val before = shown.getOrNull(i - 1)?.let { groups[it].orEmpty() }
                if (group.isNotEmpty() && before != group) item(key = "head:$group") { MenuHeading(group) }
                else if (group.isEmpty() && !before.isNullOrEmpty()) item(key = "rule:$name") { MenuDivider() }
                item(key = "ctx:$name") {
                    MenuLine(onClick = { if (name != st.context) st.switchContext(name) else st.level = "table" }) {
                        Text(midCut(name, 44), style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.weight(1f))
                        Icon(Adwaita.ObjectSelect, null, tint = c.accent,
                            modifier = Modifier.size(16.dp.icon).alpha(if (name == st.context) 1f else 0f))
                    }
                }
            }
        }
    }
}

/** A context picker with more than this many has a search, and its contexts grouped (pickers.CONTEXTS_SEARCHED). */
const val CONTEXTS_SEARCHED = 15

/** A context's place in a picker (pickers.context_order): by name, any case, numbers by their value. */
val contextOrder = Comparator<String> { a, b ->
    fun parts(s: String) = Regex("\\d+|\\D+").findAll(s).map { it.value }.toList()
    val x = parts(a)
    val y = parts(b)
    for (i in 0 until minOf(x.size, y.size)) {
        val p = x[i]
        val q = y[i]
        val pn = p[0].isDigit()
        val qn = q[0].isDigit()
        val r = when {
            pn && qn -> p.toBigInteger().compareTo(q.toBigInteger())
            pn -> -1
            qn -> 1
            else -> p.lowercase().compareTo(q.lowercase())
        }
        if (r != 0) return@Comparator r
    }
    x.size - y.size
}

/** Each context's group (pickers.context_groups): the start of its name up to its first - _ . : / or @, where two or
 * more share it; "" for one alone. */
fun contextGroups(names: List<String>): Map<String, String> {
    val sep = Regex("[-_.:/@]")
    val starts = names.associateWith { n -> if (sep.containsMatchIn(n)) n.split(sep, 2)[0] else "" }
    val count = starts.values.groupingBy { it }.eachCount()
    return starts.mapValues { (_, s) -> if (s.isNotEmpty() && (count[s] ?: 0) > 1) s else "" }
}

/** What the page shows when the context shown goes from the kubeconfig (build_removed): that it was removed, and the
 * context button, to pick another from those there are. Nothing else is picked for you. */
@Composable
private fun RemovedState(st: KubeState) {
    val c = LocalBaude.current
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp, Alignment.CenterVertically), horizontalAlignment = Alignment.CenterHorizontally) {
        Icon(Adwaita.NetworkServer, null, tint = c.dim, modifier = Modifier.size(32.dp.icon))
        Text(Wording.Kube.removed, style = Type.title, color = c.fg, textAlign = TextAlign.Center)
        if (st.contexts.isNotEmpty()) ContextButton(st, centred = true)
    }
}

/** The namespaces button: what's shown, and a menu of a check each, any set of them at once, or all. */
@Composable
private fun Namespaces(st: KubeState) {
    val c = LocalBaude.current
    var open by remember { mutableStateOf(false) }
    val tip = st.nsShown?.takeIf { it.size > 2 }?.joinToString(", ") ?: Wording.Kube.namespacesTip
    Box {
        Row(Modifier.clip(RoundedCornerShape(6.dp)).clickable { open = true }.padding(start = 8.dp, end = 4.dp, top = 6.dp, bottom = 6.dp),
            verticalAlignment = Alignment.CenterVertically) {
            Text(st.nsSummary(), style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.widthIn(max = 140.dp))
            Icon(Adwaita.PanDown, tip, tint = c.fg, modifier = Modifier.size(18.dp.icon))
        }
        Popover(open, { open = false }, ticks = Ticks.START) {
            MenuChecks(Wording.Kube.allNamespaces, (st.namespaces + st.nsShown.orEmpty()).distinct().sorted().map { it to it },
                st.nsShown, st::showNamespaces)
        }
    }
}

// (the phone's categories: a category's heading in the body's size, bold, its kinds' rows a little shorter than the
// desktop's 26; schema/ui's Kubernetes.page)
private val KUBE_HEAD = 30.dp
private val KUBE_ROW = 23.dp

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
                Row(Modifier.fillMaxWidth().padding(top = 9.dp).heightIn(min = KUBE_HEAD).clickable { st.fold(title, !folded) }
                    .padding(start = 6.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (icon != null) Icon(icon, null, tint = c.dim, modifier = Modifier.size(16.dp.icon))
                    Text(title, style = Type.title, color = c.dim, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                    Icon(if (folded) Adwaita.PanEnd else Adwaita.PanDown, null, tint = c.dim, modifier = Modifier.size(16.dp.icon))
                }
            }
            if (!single && title in st.folded) continue
            for (p in pages) item(key = "page:${p.resource}") {
                val on = p.resource == st.page.resource
                val ok = p.served != false
                val ctx = st.context.orEmpty()
                val press = if (ok) Modifier.clickable { st.pick(p) } else Modifier.tip(Wording.Kube.notServed(ctx, p.title.orEmpty()))
                Row(Modifier.fillMaxWidth().padding(top = 1.dp).heightIn(min = KUBE_ROW).background(if (on) c.rowSelected else c.inside)
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
 * line; under it, on the frame, the dock (pods' logs and shells, a tab each). */
@Composable
private fun Body(st: KubeState) {
    val c = LocalBaude.current
    val k = kubeColours()
    BoxWithConstraints(Modifier.fillMaxSize()) {
        val full = maxHeight
        var dockHeight by remember(st.docks.isEmpty()) { mutableStateOf(maxOf(full / 2, k.logsMin)) }
        Column(Modifier.fillMaxSize()) {
            Column(Modifier.weight(1f).fillMaxWidth().inside(c)) {
                val y = st.yaml
                if (y != null) YamlLevel(st, y)
                else {
                    val shown = remember(st.rows, st.nsShown, st.filter, st.sortCol, st.sortDesc, st.tablePage, st.columns,
                        st.colFilters.hidden, st.nodes) {
                        kubeRows(st)
                    }
                    Box(Modifier.weight(1f).fillMaxWidth().alpha(if (st.fade) 0.4f else 1f)) {
                        val lacks = st.missing
                        if (lacks != null) MissingDep(lacks, Wording.Window.kubernetes) { st.open(null, null, null, null) }
                        else if (st.removed != null) RemovedState(st)
                        else if (st.fault != null) ErrorState(st, st.fault!!)
                        else if (st.tablePage?.source == "summary") st.summary?.let { KubeHome(st, it) }
                        else if (st.tablePage != null) KubeTable(st, shown)
                    }
                    val status = st.status(shown.size)
                    if (status.isNotEmpty()) Text(status, style = Type.caption, color = c.dim, maxLines = 1,
                        overflow = TextOverflow.Ellipsis, modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp))
                }
            }
            if (st.docks.isNotEmpty()) Dock(st, dockHeight.coerceAtMost(maxOf(k.logsMin, full - 80.dp)), full) { dockHeight = it }
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
