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
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
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
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
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
 * sections folded, the groups shown (none picked: every one), the host shown (none kept: picked()); the sessions
 * deleted from the list (hidden, as Baude's unhide_remote brings them back); and the groups
 * as this phone has them (Baude's group_names and chat_groups): those made here, and each chat moved to one. */
object HomePrefs {
    val by = mutableStateOf("folder")
    val collapsed = mutableStateListOf<String>()
    val groups = mutableStateListOf<String>()
    val host = mutableStateOf("") // (the host kept: its connection id; "" or "all" from before: none)
    val hostName = mutableStateOf("") // (its name as it was last said, for when it's no longer listed)
    val hidden = androidx.compose.runtime.mutableStateMapOf<String, JSONObject>() // ("host/session": {updated, dead, at} when it was deleted)
    val made = mutableStateListOf<String>()
    val chatGroups = androidx.compose.runtime.mutableStateMapOf<String, String>() // ("host/session": its group here)
    private var ctx: Context? = null

    fun load(c: Context) {
        if (ctx != null) return
        ctx = c.applicationContext
        val p = c.getSharedPreferences("home", Context.MODE_PRIVATE)
        by.value = p.getString("by", "folder") ?: "folder"
        collapsed.addAll(p.getStringSet("collapsed", setOf("archived")).orEmpty())
        groups.addAll(p.getStringSet("groups", emptySet()).orEmpty())
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
            ?.putStringSet("collapsed", collapsed.toSet())?.putStringSet("groups", groups.toSet())
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

/** The group a session's in, as Baude's chat_group has it from a host: its group, unless it's the host's own. */
fun groupOf(hostId: String, s: Msg.Session): String {
    HomePrefs.chatGroups["$hostId/${s.id.orEmpty()}"]?.let { return it } // (moved here: this phone's say)
    val g = s.group.orEmpty()
    return if (g == Mesh.conn(hostId)?.info?.let { it.group.orEmpty() }) "" else g
}

/** The host whose chats are listed and a new session's made on: the one kept, else your first (Baude's shown_host),
 * none with no host. */
fun picked(): String? = HomePrefs.host.value.takeIf { it != "" && it != "all" } ?: Mesh.hosts.firstOrNull()?.id

/** The host's deactivated (Deactivate): not reached, its chats as last seen. */
fun off(id: String): Boolean = id in Mesh.deactivated

/** The host's gone: it has no connection that's up or being made (and isn't switched off). */
fun dead(id: String): Boolean = !off(id) && Mesh.conn(id)?.state.let { it != "up" && it != "connecting" }

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

/** The sections, as Baude's sidebar makes them (groups()): the Bots first (by folder), then each folder by its newest
 * chat, or each day; a chat nested under the one that forked or started it; archived ones last. query: what's
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
    val listedKeys = everywhere.filter { (h, s) -> h in shown && (groups.isEmpty() || groupOf(h, s) in groups) && key(h, s) !in far }
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
    if (HomePrefs.by.value == "date") {
        val today = LocalDate.now()
        val days = runs(live).groupBy { run ->
            val d = Instant.ofEpochMilli((said(run[0].s) * 1000).toLong()).atZone(ZoneId.systemDefault()).toLocalDate()
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
        val (bots, chats) = runs(live).partition { it[0].bot != null }
        if (bots.isNotEmpty()) out.add(Section("bots", Wording.Common.bots, null, null, bots.sortedByDescending { said(it[0].s) }.flatten()))
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
 * host (or one) grouped as Baude groups them: Bots first, each folder, archived folded; ⋮ to group by date and pick
 * groups. */
@Composable
fun Home(open: (String, String) -> Unit, feedback: () -> Unit, phone: () -> Unit = {},
         settings: () -> Unit = {}, mesh: () -> Unit = {}, changelog: () -> Unit = {}, fixes: () -> Unit = {}, note: @Composable () -> Unit = {}) {
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
            HomeMenu(settings, changelog, mesh, feedback, fixes)
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
        AnimatedVisibility(said != null, enter = expandVertically()) { HostBanner(said.orEmpty()) }
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
    SnackbarHost(snack, Modifier.align(Alignment.BottomCenter).navigationBarsPadding())
    }
    choosing?.let { h -> OpenFolder(h, done = { choosing = null }) { choosing = null; newSession(h, it) } }
    if (adding) AddHost(done = { adding = false })
}

/** Open folder… (Baude's folder chooser): the folder a new chat on host h starts in, as a path there, given as it was
 * last (else the host's home); Open: picked. */
@Composable
private fun OpenFolder(h: String, done: () -> Unit, picked: (String) -> Unit) {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    var folder by remember { mutableStateOf(HomePrefs.folder(ctx, h) ?: Mesh.conn(h)?.info?.home?.ifBlank { null } ?: "~") }
    androidx.compose.material3.AlertDialog(
        onDismissRequest = done,
        title = { Text(Wording.Sidebar.openFolderOn(host = Mesh.label(h))) },
        text = { NameField(folder, { folder = it }, "~", { picked(folder) }) },
        confirmButton = { Button(Wording.Common.open, suggested = true) { picked(folder) } },
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
            StatusDot(if (dead || off) "" else s.state.orEmpty(), !dead && !off && (!s.asks.isNullOrEmpty() || s.state == "waiting"))
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
            StatusDot(contact.state, contact.asking)
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
 * in the middle; listed local (this phone) first, then the rest by name; with one to pick, nothing to open. A long-press: its menu (Host Command…, Deactivate, Add Host…). */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun HostPick() {
    val c = LocalBaude.current
    var open by remember { mutableStateOf(false) }
    var menu by remember { mutableStateOf(false) }
    val pick = picked()
    val known = (Mesh.hosts.map { it.id } + Mesh.sessions.keys + Mesh.members.keys.map { "mesh:$it" }).distinct()
        .filter { takes(Mesh.conn(it)?.info, "subscribe") }
    val ids = if (pick != null && pick !in known) known + pick else known // (kept, by the name it had)
    fun name(id: String) = if (id in known) Mesh.label(id) else HomePrefs.hostName.value.ifBlank { Mesh.label(id) }
    Box {
        Row(Modifier.clip(RoundedCornerShape(6.dp)).combinedClickable(onClick = { if (ids.size > 1) open = true },
            onLongClick = { menu = true }).padding(horizontal = 8.dp, vertical = 7.dp).alpha(if (ids.size > 1) 1f else 0.5f),
            verticalAlignment = Alignment.CenterVertically) {
            Text(pick?.let(::name).orEmpty(), style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.MiddleEllipsis,
                modifier = Modifier.widthIn(max = 120.dp))
            Icon(Adwaita.PanDown, Wording.Sidebar.hostPickerTip, tint = c.fg, modifier = Modifier.size(16.dp.icon))
        }
        Popover(open, { open = false }) {
            for (id in ids.sortedWith(compareBy<String>({ it != "phone" }, { name(it).lowercase() }))) MenuChoice(name(id), pick == id, textColor = if (dead(id)) c.dim else null) {
                HomePrefs.pick(id); open = false
            }
        }
        HostMenu(menu) { menu = false }
    }
}

/** ⋮ beside the search: Group by (Folder, Date), the groups shown (a long-press on one: Rename Group…, Delete Group),
 * New Group…, Show All (Baude's sidebar menu). */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun ViewMenu(snack: SnackbarHostState) {
    val c = LocalBaude.current
    var open by remember { mutableStateOf(false) }
    var held by remember { mutableStateOf<String?>(null) } // (a group long-pressed: its menu)
    var naming by remember { mutableStateOf<Pair<Boolean, String>?>(null) } // (GroupName: new?, the one renamed)
    val scope = androidx.compose.runtime.rememberCoroutineScope()
    Box {
        IconButton(Adwaita.ViewMore, Wording.Sidebar.groupByTip) { open = true }
        Popover(open, { open = false }) {
            Heading(Wording.Sidebar.groupBy)
            MenuChoice(Wording.Common.folder, HomePrefs.by.value == "folder", leading = true) { HomePrefs.by.value = "folder"; HomePrefs.save() }
            MenuChoice(Wording.Sidebar.date, HomePrefs.by.value == "date", leading = true) { HomePrefs.by.value = "date"; HomePrefs.save() }
            HorizontalDivider(color = c.rule)
            Heading(Wording.Sidebar.groups)
            val names = Groups.names() + ""
            for (g in names) MenuChoice(g.ifEmpty { Wording.Sidebar.noGroup }, HomePrefs.groups.isEmpty() || g in HomePrefs.groups,
                leading = true, onLongClick = if (g.isEmpty()) null else { { held = g } }) {
                val now = if (HomePrefs.groups.isEmpty()) names else HomePrefs.groups.toList()
                val next = if (g in now) now - g else now + g
                HomePrefs.groups.clear()
                if (next.toSet() != names.toSet()) HomePrefs.groups.addAll(next)
                HomePrefs.save()
            }
            MenuChoice(Wording.Sidebar.newGroupEllipsis, false) { open = false; naming = true to "" }
            if (HomePrefs.groups.isNotEmpty()) MenuChoice(Wording.Sidebar.showAll, false) { HomePrefs.groups.clear(); HomePrefs.save() }
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

/** A menu as libadwaita's popover is: its own fill, rounded. */
@Composable
fun Popover(open: Boolean, dismiss: () -> Unit, offset: DpOffset = DpOffset.Zero,
            content: @Composable androidx.compose.foundation.layout.ColumnScope.() -> Unit) {
    val c = LocalBaude.current
    DropdownMenu(open, dismiss, offset = offset, containerColor = c.dialog, shape = RoundedCornerShape(12.dp), content = content)
}

@Composable
fun Heading(text: String) {
    val c = LocalBaude.current
    Text(text, style = Type.folder, color = c.dim, modifier = Modifier.padding(horizontal = 14.dp, vertical = 6.dp))
}

/** A menu's item, checked if it's the one (or one of those) picked: the tick at its end, or at its start (leading, as
 * a menu's check items have it, cut to one line); onLongClick: pressed and held. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun MenuChoice(label: String, on: Boolean, enabled: Boolean = true, leading: Boolean = false, textColor: Color? = null,
               onLongClick: (() -> Unit)? = null, onClick: () -> Unit) {
    val c = LocalBaude.current
    val tick: @Composable () -> Unit = {
        if (on) Icon(Adwaita.ObjectSelect, null, tint = c.accent, modifier = Modifier.size(16.dp.icon)) else if (leading) Spacer(Modifier.size(16.dp.icon))
    }
    val text: @Composable () -> Unit = {
        Text(label, style = Type.body, color = textColor ?: if (enabled) c.fg else c.dim, maxLines = if (leading) 1 else Int.MAX_VALUE,
            overflow = TextOverflow.Ellipsis, modifier = if (leading) Modifier.widthIn(max = 220.dp) else Modifier)
    }
    if (onLongClick != null) {
        Row(Modifier.heightIn(min = 38.dp).combinedClickable(enabled = enabled, onClick = onClick, onLongClick = onLongClick)
            .padding(horizontal = 12.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (leading) tick()
            text()
        }
        return
    }
    DropdownMenuItem(text, onClick, enabled = enabled, leadingIcon = if (leading) tick else null,
        trailingIcon = if (leading) null else tick, modifier = Modifier.heightIn(min = 38.dp))
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
