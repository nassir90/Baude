package dev.baude.android

import android.content.Context
import androidx.compose.foundation.background
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.border
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.expandVertically
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.DpOffset
import kotlinx.coroutines.launch
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import java.util.Locale

/** How the home screen lists (Baude's sidebar settings, kept on this phone): grouped by folder or by date, the
 * sections folded, the groups shown (none picked: every one, unless noGroups: none), the host shown (none kept: picked()); the sessions
 * deleted from the list (hidden, as Baude's unhide_remote brings them back); and the groups
 * as this phone has them (Baude's group_names and chat_groups): those made here, and each chat moved to one. */
object HomePrefs {
    val by = mutableStateOf("folder")
    val collapsed = mutableStateListOf<String>()
    val groups = mutableStateListOf<String>()
    val noGroups = mutableStateOf(false) // (every group unchecked: none listed)
    val host = mutableStateOf("") // (the host kept: its connection id; "" or "all" from before: none)
    val hostName = mutableStateOf("") // (its name as it was last said, for when it's no longer listed)
    val hidden = androidx.compose.runtime.mutableStateMapOf<String, JSONObject>() // ("host/session": {updated, dead, at} when it was deleted)
    val made = mutableStateListOf<String>()
    val chatGroups = androidx.compose.runtime.mutableStateMapOf<String, String>() // ("host/session": its group here)
    private var ctx: Context? = null

    /** The groups picked among names, none when they're every one (all shown, as Baude's picked() and filtered()). */
    fun pickedOf(names: List<String>): List<String>? = groups.filter { it in names }.let { p -> if (p.toSet().containsAll(names)) null else p.ifEmpty { null } }

    /** groups cleared if they name every group (and No group): all of them, so groups made later are shown too. */
    fun allIfEvery() {
        if (groups.isNotEmpty() && groups.toSet().containsAll(Groups.names() + "")) groups.clear()
    }

    fun load(c: Context) {
        if (ctx != null) return
        ctx = c.applicationContext
        val p = c.getSharedPreferences("home", Context.MODE_PRIVATE)
        by.value = p.getString("by", "folder").let { if (it == "date") "updated" else it } ?: "folder" // ("date", from
        // before: by when they were last used)
        collapsed.addAll(p.getStringSet("collapsed", setOf("archived")).orEmpty())
        groups.addAll(p.getStringSet("groups", emptySet()).orEmpty())
        noGroups.value = groups.isEmpty() && p.getBoolean("no_groups", false)
        host.value = p.getString("host", "") ?: ""
        hostName.value = p.getString("host_name", "") ?: ""
        val gone = JSONObject(p.getString("hidden", "{}") ?: "{}")
        for (k in gone.keys()) gone.optJSONObject(k)?.let { hidden[k] = it }
        made.addAll(p.getStringSet("made", emptySet()).orEmpty())
        val moved = org.json.JSONObject(p.getString("chat_groups", "{}") ?: "{}")
        for (k in moved.keys()) chatGroups[k] = moved.optString(k)
    }

    fun save() {
        ctx?.getSharedPreferences("home", Context.MODE_PRIVATE)?.edit()?.putString("by", by.value)
            ?.putStringSet("collapsed", collapsed.toSet())?.putStringSet("groups", groups.toSet())?.putBoolean("no_groups", noGroups.value)
            ?.putString("host", host.value)?.putString("host_name", hostName.value)
            ?.putString("hidden", JSONObject(hidden.toMap()).toString())?.putStringSet("made", made.toSet())
            ?.putString("chat_groups", org.json.JSONObject(chatGroups.toMap()).toString())?.apply()
    }

    /** The host picked, by its connection id, and its name. */
    fun pick(id: String) {
        host.value = id
        hostName.value = Mesh.label(id)
        save()
    }

    /** A session deleted from the list (Baude's hide): kept off it till it's used since, or its host's back. */
    fun hide(hostId: String, s: Msg.Session, dead: Boolean) {
        hidden["$hostId/${s.id.orEmpty()}"] = JSONObject().put("updated", s.updated ?: 0).put("dead", dead)
            .put("at", System.currentTimeMillis())
        save()
    }

    /** Sessions shown again (Baude's unhide_remote): one used since it was hidden, at a turn, or whose host is back. */
    fun unhide() {
        var changed = false
        for (k in hidden.keys.toList()) {
            val h = k.substringBefore("/")
            val s = Mesh.sessions[h]?.firstOrNull { it.id == k.substringAfter("/") } ?: continue
            val o = hidden[k] ?: continue
            if ((s.updated ?: 0).toDouble() > o.optDouble("updated", 0.0) + 1 || s.state in listOf("working", "waiting") ||
                o.optBoolean("dead") && Mesh.conn(h)?.state == "up") {
                hidden.remove(k)
                changed = true
            }
        }
        if (changed) save()
    }

    /** The folder a new session on a host starts in, as last given (Baude's remote_folders). */
    fun folder(c: Context, hostId: String): String? =
        c.getSharedPreferences("home", Context.MODE_PRIVATE).getString("folder:$hostId", null)

    fun setFolder(c: Context, hostId: String, folder: String) {
        c.getSharedPreferences("home", Context.MODE_PRIVATE).edit().putString("folder:$hostId", folder).apply()
    }
}

/** A row of the home screen: a session on a host, nested depth deep under the one that forked or started it. */
data class Item(val hostId: String, val s: Msg.Session, val depth: Int) {
    val id: String get() = s.id.orEmpty()
    val bot: Msg.BotListing? get() = s.bot
}

/** A section: a folder, a day, the Bots (no header) or the archived ones. */
data class Section(val key: String, val label: String, val hostId: String?, val folder: String?, val rows: List<Item>,
                   val full: String = label)

/** The group a session's in, as Baude's chat_group has it from a host: Desktop for a Claude Desktop chat; else its
 * group, unless it's the host's own. */
fun groupOf(hostId: String, s: Msg.Session): String {
    HomePrefs.chatGroups["$hostId/${s.id.orEmpty()}"]?.let { return it } // (moved here: this phone's say)
    if (s.desktop == true) return Wording.Sidebar.desktopGroup
    val g = s.group.orEmpty()
    return if (g == Mesh.conn(hostId)?.info?.let { it.group.orEmpty() }) "" else g
}

/** The host whose chats are listed and a new session's made on: the one kept, else your first (Baude's shown_host),
 * none with no host. */
fun picked(): String? = HomePrefs.host.value.takeIf { it != "" && it != "all" } ?: Mesh.hosts.firstOrNull()?.id

/** The host's deactivated (Deactivate): not reached, its chats as last seen. */
fun off(id: String): Boolean = id in Mesh.deactivated

/** The host's gone: it has no connection that's up or being made (and isn't switched off), and hasn't for a while
 * (Mesh.reach: one only away a moment is listed as it was). */
fun dead(id: String): Boolean = !off(id) && Mesh.conn(id)?.state.let { it != "up" && it != "connecting" } && Mesh.reach(id) == "gone"

/** What's said over a host's chats while it isn't reached (Baude's host_state), or null while it is. */
fun hostState(id: String?): String? {
    if (id == null) return null
    val host = Mesh.label(id)
    if (off(id)) return Wording.Hosts.deactivated(host = host)
    val conn = Mesh.conn(id)
    if (Mesh.hosts.none { it.id == id } && id !in Mesh.sessions.keys && conn == null)
        return Wording.Hosts.looking(host = HomePrefs.hostName.value.ifBlank { id })
    return when (conn?.state) {
        "up" -> null
        "connecting" -> Wording.Hosts.looking(host = host)
        else -> conn?.why?.let { Wording.Hosts.downBecause(host = host, why = it) }
            ?: if (Mesh.key.value == null) Wording.Hosts.downBecause(host = host, why = Wording.PhoneKeys.noKeyYet)
            else Wording.Hosts.down(host = host)
    }
}

/** The group a new session on host h is put in (Baude's new_group): the one group picked, else the one the host's
 * chats listed are all in, else none. */
fun newGroup(h: String): String {
    if (HomePrefs.groups.size == 1) return HomePrefs.groups[0]
    return sections("").flatMap { it.rows }.filter { it.depth == 0 && it.hostId == h }.map { groupOf(it.hostId, it.s) }
        .distinct().singleOrNull().orEmpty()
}

private fun said(s: Msg.Session) = (s.said ?: s.updated)?.toDouble() ?: 0.0

/** A folder as the sidebar says it: home as ~. */
fun shortPath(hostId: String, cwd: String): String {
    val home = Mesh.conn(hostId)?.info?.home.orEmpty()
    return if (home.isNotEmpty() && (cwd == home || cwd.startsWith("$home/"))) "~" + cwd.removePrefix(home) else cwd
}

/** The chats ("host/id") an agent on another host started (spawned_by: a session of another host, none of their own
 * host's; archived, hidden here and those of a gone host too), and those such a chat started in turn there: each is
 * listed only under the agent that started it, never among its own host's chats (Baude's started_elsewhere;
 * schema/ui/Sidebar.region.json). One whose agent is nowhere (deleted, say) is its host's. */
fun startedElsewhere(): Set<String> {
    val byKey = HashMap<String, Msg.Session>() // ("host/id" and "host/cli_id": the session)
    val hostsOf = HashMap<String, MutableSet<String>>() // (an id: the hosts that have a session by it)
    for ((h, list) in Mesh.sessions) for (s in list) for (id in listOfNotNull(s.id?.ifBlank { null }, s.cliId?.ifBlank { null })) {
        byKey["$h/$id"] = s
        hostsOf.getOrPut(id) { HashSet() }.add(h)
    }
    fun far(h: String, s: Msg.Session, seen: Set<String>): Boolean {
        val p = s.spawnedBy?.ifBlank { null } ?: return false
        val up = byKey["$h/$p"] ?: return hostsOf[p].orEmpty().any { it != h }
        return up !== s && up.id.orEmpty() !in seen && far(h, up, seen + s.id.orEmpty()) // (a loop of them: its host's own)
    }
    return Mesh.sessions.flatMap { (h, list) -> list.filter { far(h, it, emptySet()) }.map { "$h/${it.id.orEmpty()}" } }.toSet()
}

/** The sections, as Baude's sidebar makes them (groups()): the Bots first (by folder), then the Desktop group's chats
 * (Claude Desktop's), then each folder by its newest chat, or each day; a chat nested under the one that forked or started it; archived ones last. query: what's
 * searched for (title, folder, host, Bot's name). */
fun sections(query: String): List<Section> {
    val q = query.trim().lowercase()
    HomePrefs.unhide()
    val pick = picked()
    val shown = Mesh.sessions.keys.filter { it == pick }.sorted()
    val live = ArrayList<Item>()
    val archived = ArrayList<Item>()
    val groups = HomePrefs.groups.toSet()
    // (as Baude's sidebar lists them: each host's sessions that pass the host picked and the groups shown; a chat an
    // agent started, on another host or in another group, pulled in to go under it if that's listed (one an agent on
    // another host started: only so); then the search)
    val everywhere = Mesh.sessions.flatMap { (h, list) -> list.filter { it.draft != true && "$h/${it.id.orEmpty()}" !in HomePrefs.hidden }.map { h to it } }
    fun key(h: String, s: Msg.Session) = "$h/${s.id.orEmpty()}"
    val byKey = HashMap<String, Pair<String, Msg.Session>>() // ("host/id" and "host/cli_id": the session)
    val byId = HashMap<String, Pair<String, Msg.Session>>()  // (id or cli_id alone: an agent on another host)
    for ((h, s) in everywhere) for (id in listOfNotNull(s.id.orEmpty(), s.cliId?.ifBlank { null })) {
        byKey["$h/$id"] = h to s
        byId.putIfAbsent(id, h to s)
    }
    fun agent(h: String, s: Msg.Session) = s.spawnedBy?.ifBlank { null }?.let { p -> byKey["$h/$p"] ?: byId[p] }?.takeIf { it.second !== s }
    val far = startedElsewhere() // (only ever under the agent on another host that started it)
    val listedKeys = everywhere.filter { (h, s) -> h in shown && (if (groups.isEmpty()) !HomePrefs.noGroups.value else groupOf(h, s) in groups) && key(h, s) !in far }
        .map { (h, s) -> key(h, s) }.toMutableSet()
    val pulled = HashSet<String>()
    while (true) {
        val more = everywhere.filter { (h, s) -> key(h, s) !in listedKeys && agent(h, s)?.let { key(it.first, it.second) } in listedKeys }
            .map { (h, s) -> key(h, s) }
        if (more.isEmpty()) break
        listedKeys.addAll(more)
        pulled.addAll(more)
    }
    val found = everywhere.filter { (h, s) -> key(h, s) in listedKeys && (q.isEmpty() || listOf(s.title?.ifBlank { null }.orEmpty(),
        s.cwd.orEmpty(), Mesh.label(h), s.bot?.name.orEmpty()).any { it.lowercase().contains(q) }) }
    val kept = found.map { (h, s) -> key(h, s) }.toMutableSet()
    while (true) { // (one pulled in whose agent isn't listed after all (not found by the search, say): nor is it)
        val lost = found.filter { (h, s) -> key(h, s) in kept && key(h, s) in pulled && agent(h, s)?.let { key(it.first, it.second) } !in kept }
        if (lost.isEmpty()) break
        lost.forEach { (h, s) -> kept.remove(key(h, s)) }
    }
    // (nested under the one it forked from, on its host, or the agent that started it, wherever that is, if that's
    // listed; in its own place otherwise)
    fun parent(h: String, s: Msg.Session): Pair<String, Msg.Session>? {
        s.forkFrom?.ifBlank { null }?.let { f -> byKey["$h/$f"]?.takeIf { it.second !== s && key(it.first, it.second) in kept }?.let { return it } }
        return agent(h, s)?.takeIf { key(it.first, it.second) in kept }
    }
    val children = HashMap<String, MutableList<Pair<String, Msg.Session>>>()
    val top = ArrayList<Pair<String, Msg.Session>>()
    for ((h, s) in found) {
        if (key(h, s) !in kept) continue
        val p = parent(h, s)
        if (p != null) children.getOrPut(key(p.first, p.second)) { ArrayList() }.add(h to s) else top.add(h to s)
    }
    fun flat(h: String, s: Msg.Session, depth: Int, into: MutableList<Item>, seen: MutableSet<String>) {
        if (!seen.add(key(h, s))) return // (a loop of parents: each once)
        into.add(Item(h, s, depth))
        for ((ch, c) in children[key(h, s)].orEmpty().sortedByDescending { said(it.second) }) flat(ch, c, depth + 1, into, seen)
    }
    val seen = HashSet<String>()
    for ((h, s) in top.sortedByDescending { said(it.second) }) flat(h, s, 0, if (s.archived == true && metaMark(h, s.id.orEmpty()) == null) archived else live, seen)
    for ((h, s) in found) if (key(h, s) in kept && key(h, s) !in seen) // (in a loop of parents: in its own place)
        flat(h, s, 0, if (s.archived == true && metaMark(h, s.id.orEmpty()) == null) archived else live, seen)
    val out = ArrayList<Section>()
    fun runs(items: List<Item>): List<List<Item>> { // (each top-level row with its subtree)
        val r = ArrayList<MutableList<Item>>()
        for (i in items) if (i.depth == 0 || r.isEmpty()) r.add(mutableListOf(i)) else r.last().add(i)
        return r
    }
    // (the Desktop group's chats, Claude Desktop's: apart, first after the Bots, by folder or by day alike)
    val (desktop, rest) = runs(live).partition { groupOf(it[0].hostId, it[0].s) == Wording.Sidebar.desktopGroup }
    val desktopSection = if (desktop.isEmpty()) null
        else Section("desktop", Wording.Sidebar.desktopGroup, null, null, desktop.sortedByDescending { said(it[0].s) }.flatten())
    desktopSection?.let { out.add(it) }
    if (HomePrefs.by.value != "folder") {
        val today = LocalDate.now()
        // (Created: by the day the top one of each was made, the newest made first; Updated: by when it was last used)
        fun day(run: List<Item>) = if (HomePrefs.by.value == "created") run[0].s.created?.toDouble() ?: said(run[0].s) else said(run[0].s)
        val days = rest.sortedByDescending { day(it) }.groupBy { run ->
            val d = Instant.ofEpochMilli((day(run) * 1000).toLong()).atZone(ZoneId.systemDefault()).toLocalDate()
            ChronoUnit.DAYS.between(d, today).coerceAtLeast(0).let { if (it >= 7) 7L else it }
        }
        for ((n, group) in days.toSortedMap()) {
            val label = when (n) {
                0L -> Wording.Sidebar.today
                1L -> Wording.Sidebar.yesterday
                7L -> Wording.Sidebar.older
                else -> today.minusDays(n).format(DateTimeFormatter.ofPattern("MMM d", Locale.ENGLISH))
            }
            out.add(Section(if (n == 7L) "date:older" else "date:$n", label, null, null, group.flatten()))
        }
    } else {
        val (bots, chats) = rest.partition { it[0].bot != null }
        if (bots.isNotEmpty()) out.add(0, Section("bots", Wording.Common.bots, null, null, bots.sortedByDescending { said(it[0].s) }.flatten()))
        val folders = chats.groupBy { it[0].hostId to it[0].s.cwd.orEmpty() }
        for ((key, group) in folders.entries.sortedByDescending { e -> e.value.maxOf { said(it[0].s) } }) {
            val (hostId, cwd) = key
            val name = if (cwd.isEmpty()) Wording.Sidebar.unknownFolder else shortPath(hostId, cwd).trimEnd('/').substringAfterLast('/')
                .ifBlank { "/" }
            out.add(Section("remote:$hostId:$cwd", Wording.Sidebar.folderOnHost(folder = name, host = Mesh.label(hostId)),
                hostId, cwd, group.flatten(),
                full = Wording.Sidebar.folderOnHost(folder = shortPath(hostId, cwd), host = Mesh.label(hostId))))
        }
    }
    if (archived.isNotEmpty()) out.add(Section("archived", Wording.Sidebar.archived, null, null, archived))
    return out
}

/** The home screen: Baude's whole sidebar. New session (on the host picked beside it), search, the sessions of every
 * host (or one) grouped as Baude groups them: Bots first, each folder, archived folded; ⋮ to group by day and pick
 * groups. */
@Composable
fun Home(open: (String, String) -> Unit, feedback: () -> Unit, phone: () -> Unit = {},
         settings: () -> Unit = {}, mesh: () -> Unit = {}, changelog: () -> Unit = {}, fixes: () -> Unit = {},
         tool: (String, String) -> Unit = { _, _ -> }, subscriptions: () -> Unit = {}, note: @Composable () -> Unit = {}) {
    val c = LocalBaude.current
    val ctx = androidx.compose.ui.platform.LocalContext.current
    LaunchedEffect(Unit) { HomePrefs.load(ctx) }
    Mesh.tick.value // (a host's state changed: drawn again)
    var query by rememberSaveable { mutableStateOf("") }
    var choosing by remember { mutableStateOf<String?>(null) } // (Open folder…'s: the host it's a folder of)
    var adding by remember { mutableStateOf(false) }
    val snack = remember { SnackbarHostState() }
    // New session, a folder's + and Open folder…: a new chat on host h (none: the one picked), a draft till it's written
    // in, opened at once (Baude's new_chat, start_in_folder): in folder, kept as the host's last; else in the one last
    // given there
    fun newSession(h: String? = picked(), folder: String? = null) {
        val at = h ?: return
        hostState(at)?.let { toast(it); return }
        val group = newGroup(at)
        val cwd = folder?.trim()?.ifBlank { "~" } ?: HomePrefs.folder(ctx, at) ?: "~"
        val msg = Msg.Start(prompt = "", cwd = cwd, mode = "auto", group = group).json().put("draft", true)
        Mesh.ask(at, msg) { r ->
            val id = Msg.Started.from(r).session?.id
            if (r.optString("op") == Op.STARTED && id != null) {
                if (folder != null) HomePrefs.setFolder(ctx, at, cwd)
                Groups.putIn(group)
                open(at, id)
            } else toast(Wording.NewSession.hostRefused(host = Mesh.label(at),
                why = Msg.Error.from(r).said.orEmpty().ifBlank { Wording.BotBrowser.hostSaidNo }))
        }
    }
    Box(Modifier.fillMaxSize().background(c.frame)) {
    Column(Modifier.fillMaxSize()) {
        Bar("Baude", left = true, end = {
            IconButton(Adwaita.Phone, Wording.ThisPhone.title, onClick = phone) // (its own agents: PhoneScreen)
            HomeMenu(settings, changelog, mesh, feedback, fixes, tool, subscriptions)
        })
        note()
        if (Mesh.hosts.isEmpty()) GreyBanner("Add a host to see its sessions.", onClick = { adding = true })
        // the top: New session and the host it's on, Open folder…, then the search and ⋮ (Baude's sidebar's top box)
        Column(Modifier.padding(start = 8.dp, end = 8.dp, top = 8.dp, bottom = 4.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(2.dp)) {
                SidebarAction(Adwaita.DocumentNew, Wording.Sidebar.newSession, Modifier.weight(1f)) { newSession() }
                HostPick()
            }
            SidebarAction(Adwaita.FolderOpen, Wording.Sidebar.openFolder, Modifier.fillMaxWidth()) {
                val h = picked()
                val said = hostState(h)
                if (said != null) toast(said) else choosing = h
            }
            Row(Modifier.padding(top = 6.dp), verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(2.dp)) {
                SearchField(query, { query = it }, Wording.Sidebar.search, Modifier.weight(1f))
                ViewMenu(snack)
            }
        }
        val said = hostState(picked())
        val away = picked()?.let { !off(it) && Mesh.reach(it) == "away" } == true && said != null
        AnimatedVisibility(said != null, enter = expandVertically()) { if (away) Away() else HostBanner(said.orEmpty()) }
        val all = sections(query)
        val update = Update.state.value != ""
        if (all.isEmpty()) {
            Spacer(Modifier.weight(1f))
        } else LazyColumn(Modifier.fillMaxWidth().weight(1f).then(if (update) Modifier else Modifier.navigationBarsPadding())
            .padding(horizontal = Gap.inside)) {
            for (sec in all) {
                val folded = query.isBlank() && sec.key in HomePrefs.collapsed
                if (sec.key != "bots") item(key = "h:" + sec.key) {
                    Header(sec, folded, plus = sec.hostId?.takeIf { Mesh.conn(it)?.state == "up" }?.let { h ->
                        { newSession(h, sec.folder) } }) {
                        if (sec.key in HomePrefs.collapsed) HomePrefs.collapsed.remove(sec.key) else HomePrefs.collapsed.add(sec.key)
                        HomePrefs.save()
                    }
                }
                if (folded) continue
                val rows = sec.rows
                val through = through(rows.map { it.depth })
                val isBot = { item: Item -> item.bot != null && item.depth == 0 && !dead(item.hostId) }
                var top: Item? = null
                val underBot = rows.map { if (it.depth == 0) top = it; top?.let(isBot) == true }
                itemsIndexed(rows, key = { _, it -> sec.key + "/" + it.hostId + "/" + it.id }) { i, item ->
                    val kids = rows.getOrNull(i + 1)?.let { it.depth > item.depth } == true
                    if (isBot(item)) BotRow(item, kids) { open(item.hostId, item.id) }
                    else SessionRow(item, through[i], underBot[i]) { open(item.hostId, item.id) }
                }
            }
        }
        if (update) UpdateBar()
    }
    // (each host keeping a restart of its daemon: a snackbar, as Baude toasts it)
    val restarts = Mesh.known().mapNotNull { (id, _, up) -> Mesh.conn(id)?.info?.restart?.takeIf { up }?.let { id to it } }
    RestartSnackbar(restarts, Modifier.align(Alignment.BottomCenter).navigationBarsPadding(), open, tool)
    SnackbarHost(snack, Modifier.align(Alignment.BottomCenter).navigationBarsPadding())
    }
    choosing?.let { h -> OpenFolder(h, done = { choosing = null }) { choosing = null; newSession(h, it) } }
    if (adding) AddHost(done = { adding = false })
}

/** Open folder… (Baude's ask_host_folder): the folder a new chat on host h starts in, a path there typed or browsed,
 * from the one last given there (else the host's home). Under it, the folders the host lists (list_dir) as it's typed:
 * those in the folder typed, else those of its parent that its last part begins (.. first, for the one above); one
 * tapped is put in the path. Open: picked. */
@Composable
private fun OpenFolder(h: String, done: () -> Unit, picked: (String) -> Unit) {
    val c = LocalBaude.current
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val home = Mesh.conn(h)?.info?.home?.ifBlank { null } ?: "/"
    val start = HomePrefs.folder(ctx, h) ?: home
    var field by remember { mutableStateOf(androidx.compose.ui.text.input.TextFieldValue(start,
        androidx.compose.ui.text.TextRange(start.length))) }
    var listed by remember { mutableStateOf<Pair<String, List<String>>?>(null) } // (the folder listed, its folders shown)
    fun full(t: String) = t.trim().ifBlank { "~" }.let { if (it == "~" || it.startsWith("~/")) home.trimEnd('/') + it.drop(1) else it }
    fun up(p: String) = p.trimEnd('/').substringBeforeLast('/').ifEmpty { "/" }
    val typed = full(field.text)
    LaunchedEffect(typed) {
        if (!typed.startsWith("/")) { listed = null; return@LaunchedEffect }
        fun shown(folder: String, r: JSONObject, part: String) = folder to Msg.Dir.from(r).entries.orEmpty()
            .mapNotNull { e -> e.name?.takeIf { e.dir == true } }
            .filter { it.lowercase().startsWith(part.lowercase()) && (!it.startsWith(".") || part.startsWith(".")) }
        Mesh.ask(h, Msg.ListDir(path = typed).json()) { r ->
            if (typed != full(field.text)) return@ask // (typed on since: that one's answer's wanted)
            if (r.optString("op") == Op.DIR && Msg.Dir.from(r).error.isNullOrBlank()) listed = shown(typed, r, "")
            else { // (not a folder: its parent's, that its last part begins)
                val parent = up(typed)
                val part = typed.trimEnd('/').substringAfterLast('/')
                Mesh.ask(h, Msg.ListDir(path = parent).json()) { p ->
                    if (typed != full(field.text)) return@ask
                    listed = if (p.optString("op") == Op.DIR && Msg.Dir.from(p).error.isNullOrBlank()) shown(parent, p, part)
                        else parent to emptyList()
                }
            }
        }
    }
    fun put(path: String) { field = androidx.compose.ui.text.input.TextFieldValue(path, androidx.compose.ui.text.TextRange(path.length)) }
    fun open() = picked(if (typed.startsWith("/")) java.io.File(typed).normalize().path.ifEmpty { "/" } else typed)
    AlertDialog(
        onDismissRequest = done,
        title = { Text(Wording.Sidebar.openFolderOn(host = Mesh.label(h))) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                val focusing = remember { androidx.compose.ui.focus.FocusRequester() }
                LaunchedEffect(Unit) { focusing.requestFocus() }
                Box(Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).background(c.fg.copy(alpha = 0.08f))
                    .padding(horizontal = 10.dp, vertical = 9.dp)) {
                    if (field.text.isEmpty()) Text("~", style = Type.body, color = c.dim)
                    BasicTextField(field, { field = it }, singleLine = true, textStyle = Type.body.copy(color = c.fg),
                        cursorBrush = SolidColor(c.accentBg),
                        keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(imeAction = androidx.compose.ui.text.input.ImeAction.Done),
                        keyboardActions = androidx.compose.foundation.text.KeyboardActions(onDone = { open() }),
                        modifier = Modifier.fillMaxWidth().focusRequester(focusing))
                }
                val (folder, names) = listed ?: ("" to emptyList())
                val rows = (if (folder.trimEnd('/').isNotEmpty()) listOf(".." to up(folder)) else emptyList()) +
                    names.map { it to folder.trimEnd('/') + "/" + it }
                LazyColumn(Modifier.fillMaxWidth().heightIn(min = 240.dp, max = 240.dp).clip(RoundedCornerShape(8.dp))
                    .border(1.dp, c.rule, RoundedCornerShape(8.dp))) {
                    items(rows, key = { it.second + "\u0000" + it.first }) { (name, path) ->
                        Row(Modifier.fillMaxWidth().clickable { put(path) }.heightIn(min = 36.dp).padding(horizontal = 10.dp),
                            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Icon(if (name == "..") Adwaita.GoUp else Adwaita.Folder, null, tint = c.fg, modifier = Modifier.size(16.dp.icon))
                            Text(name, style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.MiddleEllipsis,
                                modifier = Modifier.weight(1f))
                        }
                    }
                }
            }
        },
        confirmButton = { Button(Wording.Common.open, suggested = true) { open() } },
        dismissButton = { Button(Wording.Common.cancel, onClick = done) },
        containerColor = LocalBaude.current.dialog,
    )
}

/** What's said over the chats of a host that isn't reached (Baude's chatparts.HostState): a placeholder in the rule's
 * outline. */
@Composable
private fun HostBanner(text: String) {
    val c = LocalBaude.current
    Column(Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 6.dp).clip(RoundedCornerShape(12.dp)).background(c.card)
        .border(1.dp, c.rule, RoundedCornerShape(12.dp))) {
        Text(text, style = Type.body, color = c.dim, textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth().padding(12.dp))
    }
}

/** At the foot, under a rule from the chats (Baude's UpdateBar): Update, while a newer build's ready or waiting to be
 * downloaded (Update.kt), how much of it's come while it's downloading; tapped, installed. A newer build that can't be
 * installed over this one: why, instead. */
@Composable
private fun UpdateBar() {
    val c = LocalBaude.current
    val ctx = androidx.compose.ui.platform.LocalContext.current
    Column(Modifier.fillMaxWidth().navigationBarsPadding()) {
        HorizontalDivider(color = c.rule)
        if (Update.state.value == "downloading")
            androidx.compose.material3.LinearProgressIndicator({ Update.progress.value }, Modifier.fillMaxWidth(),
                color = c.accentBg, trackColor = c.rule)
        if (Update.state.value == "wrong") // (a newer build that can't be installed over this one: why)
            Text(Update.why.value, style = Type.caption, color = c.dim,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 11.dp))
        else Text(Wording.UpdateBar.update, style = Type.body, color = c.fg, modifier = Modifier.padding(horizontal = 6.dp, vertical = 4.dp)
            .clip(RoundedCornerShape(6.dp)).clickable { Update.install(ctx) }.padding(horizontal = 10.dp, vertical = 7.dp))
    }
}

/** Which hosts' pending restarts have had their snackbar (Baude's meshui.RestartToasts): each once, till it's gone;
 * held, those a See Thread holds (said till it's dismissed, which lets go of that pause); showing, the one shown. */
internal object RestartSnack {
    val toasted = mutableStateListOf<String>()
    val held = mutableStateListOf<String>()
    var showing by mutableStateOf<String?>(null)
}

/** Each host's pending daemon restart (restarts; named when there's more than one), a snackbar at a time (Baude's
 * toast): what it waits for, kept up as it changes, with Details: what it waits for (RestartBlockers; See Thread on
 * one: its chat, open(host, session), or its terminal, tool("terminal", host)). Each is shown once and goes as a
 * snackbar does (RESTART_SNACK_MS, or dismissed); the restart goes on, as it was. A See Thread holds the restart: the
 * snackbar back, "Daemon restart paused", till it's dismissed (the pause let go of) or Details is tapped again. */
@Composable
private fun RestartSnackbar(restarts: List<Pair<String, Msg.RestartState>>, modifier: Modifier,
                            open: (String, String) -> Unit, tool: (String, String) -> Unit) {
    val c = LocalBaude.current
    val s = RestartSnack
    var blockers by remember { mutableStateOf<String?>(null) } // (the host whose restart's waits are shown)
    val ids = restarts.map { it.first }
    LaunchedEffect(ids, s.showing) { // (gone ones forgotten; none shown: a held one, else one not shown yet)
        s.toasted.retainAll(ids)
        s.held.retainAll(ids)
        if (s.showing != null && s.showing !in ids) s.showing = null
        if (s.showing == null) s.showing = s.held.firstOrNull() ?: ids.firstOrNull { it !in s.toasted }?.also { s.toasted.add(it) }
    }
    val id = s.showing
    val r = restarts.firstOrNull { it.first == id }?.second
    LaunchedEffect(id, id in s.held) {
        if (id != null && id !in s.held) {
            kotlinx.coroutines.delay(RESTART_SNACK_MS)
            if (s.showing == id) s.showing = null
        }
    }
    if (id != null && r != null) {
        val said = if (r.paused == true) Wording.Hosts.restartPaused else Wording.Hosts.restartWaiting(n = r.blockers.orEmpty().size)
        androidx.compose.material3.Snackbar(modifier.padding(12.dp),
            action = {
                androidx.compose.material3.TextButton({ s.showing = null; s.held.remove(id); blockers = id }) {
                    Text(Wording.Hosts.restartDetails)
                }
            },
            dismissAction = {
                IconButton(Adwaita.WindowClose, Wording.Common.close, tint = androidx.compose.material3.SnackbarDefaults.dismissActionContentColor) {
                    s.showing = null
                    if (s.held.remove(id) && Mesh.conn(id)?.info?.restart != null) // (a See Thread's pause: let go of)
                        Mesh.conn(id)?.request(Msg.Restart(`do` = "free").json()) {}
                }
            }) {
            Text(if (restarts.size > 1) Wording.Hosts.restartOn(host = Mesh.label(id), said = said) else said)
        }
    }
    blockers?.let { h ->
        RestartBlockers(h, done = { blockers = null }) { b -> // (See Thread: the restart held while it's looked at)
            if (h !in s.held) s.held.add(h)
            s.showing = h
            when (b.kind) {
                "terminal" -> { Terms.wanted[h] = b.terminal.orEmpty(); tool("terminal", h) }
                "task" -> { TaskLinks.pending = listOf(h, b.session.orEmpty(), b.task.orEmpty(), b.run.orEmpty()); open(h, b.session.orEmpty()) }
                else -> open(b.host?.ifBlank { null } ?: h, b.session.orEmpty())
            }
        }
    }
}

/** How long a pending restart's snackbar stays (Baude's meshui.RESTART_TOAST_S). */
private const val RESTART_SNACK_MS = 10_000L

/** What a host's pending restart waits for (Baude's meshui.restart_dialog): each chat running a command, each task's
 * run, each agent run there for a chat elsewhere, each terminal there someone's attached to; the restart held while
 * it's shown (restart pause: every app says it's paused), and again once its connection's back. Restart: it restarts
 * once nothing's running; Force Restart: now, what's running stopped. Closed, or gone from the screen otherwise, the
 * pause is let go of (restart free, only while it's still pending: never a restart of its own). See Thread on a row:
 * see(it), the restart held meanwhile (its snackbar says it's paused; Details, this again). */
@Composable
fun RestartBlockers(hostId: String, done: () -> Unit, see: (Msg.RestartBlocker) -> Unit = {}) {
    val c = LocalBaude.current
    Mesh.tick.value
    val conn = Mesh.conn(hostId)
    val up = conn?.state == "up"
    var held by remember { mutableStateOf(true) } // (false once it's answered, or left to look at a row)
    LaunchedEffect(hostId, up) { if (up) conn?.request(Msg.Restart(`do` = "pause").json()) {} }
    androidx.compose.runtime.DisposableEffect(hostId) {
        onDispose { if (held && conn?.info?.restart != null) conn.request(Msg.Restart(`do` = "free").json()) {} }
    }
    val blockers = conn?.info?.restart?.blockers.orEmpty()
    fun answer(now: Boolean) {
        held = false
        conn?.request(Msg.Restart(`do` = if (now) "now" else "free").json()) {}
        done()
    }
    AlertDialog(onDismissRequest = { done() }, // (gone from the screen: let go of, above)
        title = { Text(Wording.Hosts.restartBlockers) },
        text = {
            Column {
                if (blockers.isEmpty()) Text(Wording.Hosts.restartNothing, style = Type.body, color = c.dim)
                for (b in blockers) Row(Modifier.padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Icon(when (b.kind) {
                        "task" -> Adwaita.MediaPlaybackStart
                        "agent" -> Adwaita.EmblemSystem
                        "terminal" -> Adwaita.InputKeyboard
                        else -> Adwaita.UtilitiesTerminal
                    }, null, tint = c.fg, modifier = Modifier.size(16.dp.icon))
                    Text(b.name?.ifBlank { null } ?: b.what.orEmpty(), style = Type.body, color = c.fg, maxLines = 1,
                        overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                    if (!b.session.isNullOrEmpty() || !b.terminal.isNullOrEmpty())
                        Button(Wording.Hosts.seeThread) { held = false; done(); see(b) }
                }
            }
        },
        confirmButton = { Button(Wording.SharedSettings.restart, suggested = true) { answer(false) } },
        dismissButton = { Button(Wording.Hosts.forceRestart, destructive = true) { answer(true) } }, containerColor = c.dialog)
}

/** A section's header (Baude's header_row): its name (.sidebar-folder), the arrow to fold it, + for a new session
 * there. */
@Composable
private fun Header(sec: Section, folded: Boolean, plus: (() -> Unit)?, toggle: () -> Unit) {
    val c = LocalBaude.current
    Row(Modifier.fillMaxWidth().padding(top = 6.dp, start = 4.dp).clip(RoundedCornerShape(6.dp)).clickable(onClick = toggle),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(2.dp)) {
        Row(Modifier.weight(1f), verticalAlignment = Alignment.CenterVertically) {
            Text(sec.label, style = Type.folder.copy(fontSize = 14.sp), color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.alpha(0.6f).weight(1f, fill = false))
            Box(Modifier.size(24.dp).alpha(if (folded) 0.75f else 0.4f), contentAlignment = Alignment.Center) {
                Icon(if (folded) Adwaita.PanEnd else Adwaita.PanDown,
                    if (folded) Wording.Common.expand else Wording.Common.collapse, tint = c.fg, modifier = Modifier.size(16.dp.icon))
            }
        }
        if (plus != null) Box(Modifier.size(24.dp).alpha(0.75f).clip(RoundedCornerShape(6.dp)).clickable(onClick = plus),
            contentAlignment = Alignment.Center) {
            Icon(Adwaita.ListAdd, sec.hostId?.let {
                Wording.Sidebar.newSessionInOn(folder = shortPath(it, sec.folder.orEmpty()), host = Mesh.label(it))
            } ?: Wording.Sidebar.newSessionIn(folder = sec.full), tint = c.fg, modifier = Modifier.size(16.dp.icon))
        }
    }
}

/** A row's long-press, where it was pressed: its menu opens at the finger (Baude's right-click, at the pointer). */
@OptIn(ExperimentalFoundationApi::class)
private fun Modifier.pressed(at: (Offset) -> Unit, onClick: () -> Unit, onLong: () -> Unit): Modifier =
    this.pointerInput(Unit) { awaitEachGesture { at(awaitFirstDown(requireUnconsumed = false).position) } }
        .combinedClickable(onClick = onClick, onLongClick = onLong)

/** A chat's row (Baude's session_row): its title on one line, nested under its parent with its parents' lines
 * (underBot: the top one comes down from a Bot's picture), its status dot; a long-press: its menu. A host that's gone or deactivated: its title dim, and a crossed-out cloud if it's gone. */
@Composable
private fun SessionRow(item: Item, through: Set<Int>, underBot: Boolean, onClick: () -> Unit) {
    val c = LocalBaude.current
    val s = item.s
    val off = off(item.hostId)
    val dead = dead(item.hostId)
    var menu by remember { mutableStateOf(false) }
    var pressed by remember { mutableStateOf(Offset.Zero) }
    var height by remember { mutableStateOf(0) }
    Box(Modifier.onSizeChanged { height = it.height }) {
        Row(Modifier.fillMaxWidth().treeLines(item.depth, through, c.fg.copy(alpha = 0.3f), root = if (underBot) BOT_START else 6.dp)
            .padding(top = 1.dp).heightIn(min = Gap.homeRow).clip(RoundedCornerShape(6.dp))
            .pressed({ pressed = it }, onClick) { menu = true }
            .padding(start = 6.dp + 18.dp * item.depth, end = 4.dp, top = 1.dp, bottom = 1.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            val title = chatName(s) ?: Wording.Common.untitled
            val mark = metaMark(item.hostId, item.id)
            Text(markedName(title, mark?.how, mark != null), style = Type.body, color = if (dead || off) c.dim else c.fg,
                maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            if (dead) Icon(Adwaita.NoConnection, Wording.Sidebar.noConnection, tint = c.dim, modifier = Modifier.size(16.dp.icon))
            else HeldTag(s)
            StatusDot(if (dead || off) "" else Mesh.dot(s), !dead && !off && (!s.asks.isNullOrEmpty() || s.state == "waiting"))
            MarkNote(mark)
        }
        RowMenu(item, menu, pressed, height) { menu = false }
    }
}

/** Where a Bot's row starts its picture in (bot_row's margin). */
private val BOT_START = 4.dp

/** A Bot's row (Baude's bot_row): its picture, its name (nothing else of its chat's), its status dot; a long-press: its
 * menu. kids: the line to the chats under it starts under its picture. */
@Composable
private fun BotRow(item: Item, kids: Boolean, onClick: () -> Unit) {
    val c = LocalBaude.current
    val contact = Contact(item.hostId, item.s)
    var menu by remember { mutableStateOf(false) }
    var pressed by remember { mutableStateOf(Offset.Zero) }
    var height by remember { mutableStateOf(0) }
    LaunchedEffect(contact.pictureKey) { Mesh.picture(contact) }
    Box(Modifier.onSizeChanged { height = it.height }) {
        Row(Modifier.fillMaxWidth()
            .then(if (kids) Modifier.treeStem(BOT_START + 5.dp, c.fg.copy(alpha = 0.3f)) { (size.height + Gap.avatar.toPx()) / 2 }
                  else Modifier)
            .heightIn(min = Gap.homeBotRow).clip(RoundedCornerShape(6.dp))
            .pressed({ pressed = it }, onClick) { menu = true }
            .padding(horizontal = BOT_START, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Avatar(contact.name, Mesh.pictures[contact.pictureKey])
            Spacer(Modifier.width(Gap.avatarGap))
            val mark = metaMark(item.hostId, item.id)
            Text(markedName(contact.name, mark?.how, mark != null), style = Type.bold, color = c.fg, maxLines = 1,
                overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            HeldTag(item.s)
            StatusDot(Mesh.dot(item.s), contact.asking)
            MarkNote(mark)
        }
        RowMenu(item, menu, pressed, height) { menu = false }
    }
}

/** A row's menu, opened where it was pressed (the row's height up from the box's foot, which it hangs from). */
@Composable
private fun RowMenu(item: Item, open: Boolean, pressed: Offset, height: Int, dismiss: () -> Unit) {
    val d = LocalDensity.current
    SessionMenu(item, open, with(d) { DpOffset(pressed.x.toDp(), (pressed.y - height).toDp()) }, dismiss)
}

/** A row's tag (Baude's .row-tag): a word after its title, dim on the raised fill. */
@Composable
private fun RowTag(text: String) {
    val c = LocalBaude.current
    Text(text, style = Type.caption, color = c.dim, modifier = Modifier.clip(RoundedCornerShape(4.dp))
        .background(c.raised).padding(horizontal = 5.dp))
}

/** A row's "held" mark (Baude's held_tag): its host holds messages for the chat, to send or delete in it. */
@Composable
private fun HeldTag(s: Msg.Session) {
    if (s.held.isNullOrEmpty()) return
    RowTag(Wording.Sidebar.held)
}

/** A flat button with an icon and a label, as the sidebar's New session (pickers.sidebar_action). */
@Composable
fun SidebarAction(icon: androidx.compose.ui.graphics.vector.ImageVector, label: String, modifier: Modifier = Modifier,
                  onClick: () -> Unit) {
    val c = LocalBaude.current
    Row(modifier.clip(RoundedCornerShape(6.dp)).clickable(onClick = onClick).padding(horizontal = 8.dp, vertical = 7.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Icon(icon, null, tint = c.fg, modifier = Modifier.size(16.dp.icon))
        Text(label, style = Type.body, color = c.fg, maxLines = 1)
    }
}

/** A search entry, as Gtk.SearchEntry's: its icon, rounded, on the faint fill. */
@Composable
fun SearchField(value: String, set: (String) -> Unit, hint: String, modifier: Modifier = Modifier) {
    val c = LocalBaude.current
    Row(modifier.clip(RoundedCornerShape(8.dp)).background(c.fg.copy(alpha = 0.08f)).padding(horizontal = 8.dp, vertical = 7.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        Icon(Adwaita.SystemSearch, null, tint = c.dim, modifier = Modifier.size(16.dp.icon))
        Box(Modifier.weight(1f)) {
            if (value.isEmpty()) Text(hint, style = Type.body, color = c.dim, maxLines = 1)
            BasicTextField(value, set, singleLine = true, textStyle = Type.body.copy(color = c.fg),
                cursorBrush = SolidColor(c.accentBg), modifier = Modifier.fillMaxWidth())
        }
    }
}

/** The host the home screen shows and a new session's made on (beside New session, as Baude's): its name cut with …
 * in the middle; listed local (this phone) first, then the rest by name; with one to pick, nothing to open. A long-press: its menu (Deactivate, Add Host…). */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun HostPick() {
    val c = LocalBaude.current
    var open by remember { mutableStateOf(false) }
    var menu by remember { mutableStateOf(false) }
    val pick = picked()
    val ids = newSessionHosts()
    val name = hostName()
    Box {
        Row(Modifier.clip(RoundedCornerShape(6.dp)).combinedClickable(onClick = { if (ids.size > 1) open = true },
            onLongClick = { menu = true }).padding(horizontal = 8.dp, vertical = 7.dp).alpha(if (ids.size > 1) 1f else 0.5f),
            verticalAlignment = Alignment.CenterVertically) {
            Text(pick?.let(name).orEmpty(), style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.MiddleEllipsis,
                modifier = Modifier.widthIn(max = 120.dp))
            Icon(Adwaita.PanDown, Wording.Sidebar.hostPickerTip, tint = c.fg, modifier = Modifier.size(16.dp.icon))
        }
        Popover(open, { open = false }, ticks = Ticks.END) {
            HostChoices(ids, pick) { id -> HomePrefs.pick(id); open = false }
        }
        HostMenu(menu) { menu = false }
    }
}

/** New session's hosts, as its picker lists them: every host known that takes chats, and the one picked, by the name it
 * had, while it isn't known. */
fun newSessionHosts(): List<String> {
    val pick = picked()
    val known = knownHosts()
    return if (pick != null && pick !in known) known + pick else known
}

private fun knownHosts() = (Mesh.hosts.map { it.id } + Mesh.sessions.keys + Mesh.members.keys.map { "mesh:$it" }).distinct()
    .filter { takes(Mesh.conn(it)?.info, "subscribe") }

/** A host's name as New session's picker says it (the one picked, while it isn't known, by the name it had). */
fun hostName(): (String) -> String {
    val known = knownHosts()
    return { id -> if (id in known) Mesh.label(id) else HomePrefs.hostName.value.ifBlank { Mesh.label(id) } }
}

/** New session's host list (HostPick's, and ⋮'s family rows' carets): ids, this phone first, then the rest by name,
 * the one picked ticked, one out of reach greyed; pick(id) as one's tapped. */
@Composable
fun HostChoices(ids: List<String>, picked: String?, pick: (String) -> Unit) {
    val c = LocalBaude.current
    val name = hostName()
    for (id in ids.sortedWith(compareBy<String>({ it != "phone" }, { name(it).lowercase() })))
        MenuChoice(name(id), picked == id, textColor = if (dead(id)) c.dim else null) { pick(id) }
}

/** ⋮ beside the search: Group by (Folder, Created, Updated), the groups shown (a long-press on one: Rename Group…,
 * Delete Group), New Group… (Baude's sidebar menu). */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun ViewMenu(snack: SnackbarHostState) {
    var open by remember { mutableStateOf(false) }
    var held by remember { mutableStateOf<String?>(null) } // (a group long-pressed: its menu)
    var naming by remember { mutableStateOf<Pair<Boolean, String>?>(null) } // (GroupName: new?, the one renamed)
    val scope = androidx.compose.runtime.rememberCoroutineScope()
    Box {
        IconButton(Adwaita.ViewMore, Wording.Sidebar.groupByTip) { open = true }
        Popover(open, { open = false }, ticks = Ticks.START) {
            MenuHeading(Wording.Sidebar.groupBy)
            MenuChoice(Wording.Common.folder, HomePrefs.by.value == "folder") { HomePrefs.by.value = "folder"; HomePrefs.save() }
            MenuChoice(Wording.Sidebar.created, HomePrefs.by.value == "created") { HomePrefs.by.value = "created"; HomePrefs.save() }
            MenuChoice(Wording.Sidebar.updated, HomePrefs.by.value == "updated") { HomePrefs.by.value = "updated"; HomePrefs.save() }
            MenuDivider()
            // (Groups: a square check each and one over them all, its label a heading, as the Kubernetes page's Namespaces)
            val names = Groups.names() + ""
            MenuChecks(Wording.Sidebar.groups, names.map { it to it.ifEmpty { Wording.Sidebar.noGroup } },
                if (HomePrefs.noGroups.value) emptyList() else HomePrefs.pickedOf(names), { next ->
                    HomePrefs.groups.clear()
                    if (next != null) HomePrefs.groups.addAll(next)
                    HomePrefs.allIfEvery()
                    HomePrefs.noGroups.value = next?.isEmpty() == true
                    HomePrefs.save()
                }, onLongClick = { g -> if (g.isNotEmpty() && g != Wording.Sidebar.desktopGroup) held = g }, heading = true) // (No group's
            // and Desktop's, Claude Desktop's chats put there by what they are: no menu)
            MenuDivider()
            MenuChoice(Wording.Sidebar.newGroupEllipsis, false) { open = false; naming = true to "" }
        }
        Popover(held != null, { held = null }) {
            val g = held.orEmpty()
            MenuChoice(Wording.Sidebar.renameGroupEllipsis, false) { held = null; open = false; naming = false to g }
            MenuChoice(Wording.Sidebar.deleteGroup, false) {
                held = null
                open = false
                val snap = Groups.delete(g)
                scope.launch {
                    val r = snack.showSnackbar(Wording.Sidebar.groupDeleted(name = g), Wording.Common.undo, duration = SnackbarDuration.Short)
                    if (r == SnackbarResult.ActionPerformed) Groups.restore(snap)
                }
            }
        }
    }
    naming?.let { (new, old) -> GroupName(new, old) { naming = null } }
}

@Composable
fun Heading(text: String) {
    val c = LocalBaude.current
    Text(text, style = Type.folder, color = c.dim, modifier = Modifier.padding(horizontal = 14.dp, vertical = 6.dp))
}

/** ☰'s menu of a bar: its items. */
@Composable
fun Menu(items: List<Pair<String, () -> Unit>>) {
    var open by remember { mutableStateOf(false) }
    Box {
        IconButton(Adwaita.ViewMore, Wording.ChatHeader.menu) { open = true }
        Popover(open, { open = false }) {
            for ((label, go) in items) MenuChoice(label, false) { open = false; go() }
        }
    }
}
