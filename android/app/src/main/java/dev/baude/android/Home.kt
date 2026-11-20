package dev.baude.android

import android.content.Context
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.NoteAdd
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Dns
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Search
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
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import java.util.Locale

/** How the home screen lists (Baude's sidebar settings, kept on this phone): grouped by folder or by date, the
 * sections folded, the groups shown (none picked: every one), the host shown ("all": every host's); and the groups
 * as this phone has them (Baude's group_names and chat_groups): those made here, and each chat moved to one. */
object HomePrefs {
    val by = mutableStateOf("folder")
    val collapsed = mutableStateListOf<String>()
    val groups = mutableStateListOf<String>()
    val host = mutableStateOf("all")
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
        host.value = p.getString("host", "all") ?: "all"
        made.addAll(p.getStringSet("made", emptySet()).orEmpty())
        val moved = org.json.JSONObject(p.getString("chat_groups", "{}") ?: "{}")
        for (k in moved.keys()) chatGroups[k] = moved.optString(k)
    }

    fun save() {
        ctx?.getSharedPreferences("home", Context.MODE_PRIVATE)?.edit()?.putString("by", by.value)
            ?.putStringSet("collapsed", collapsed.toSet())?.putStringSet("groups", groups.toSet())
            ?.putString("host", host.value)?.putStringSet("made", made.toSet())
            ?.putString("chat_groups", org.json.JSONObject(chatGroups.toMap()).toString())?.apply()
    }

    /** The folder a new session on a host starts in, as last given (Baude's remote_folders). */
    fun folder(c: Context, hostId: String): String? =
        c.getSharedPreferences("home", Context.MODE_PRIVATE).getString("folder:$hostId", null)

    fun setFolder(c: Context, hostId: String, folder: String) {
        c.getSharedPreferences("home", Context.MODE_PRIVATE).edit().putString("folder:$hostId", folder).apply()
    }
}

/** A row of the home screen: a session on a host, nested depth deep under the one that forked or started it. */
data class Item(val hostId: String, val s: JSONObject, val depth: Int) {
    val id: String get() = s.optString("id")
    val bot: JSONObject? get() = s.optJSONObject("bot")
}

/** A section: a folder, a day, the Bots (no header) or the archived ones; top: its rows at depth 0, for its limit. */
data class Section(val key: String, val label: String, val hostId: String?, val folder: String?, val rows: List<Item>,
                   val full: String = label)

/** The group a session's in, as Baude's chat_group has it from a host: its group, unless it's the host's own. */
fun groupOf(hostId: String, s: JSONObject): String {
    HomePrefs.chatGroups["$hostId/${s.optString("id")}"]?.let { return it } // (moved here: this phone's say)
    val g = if (s.isNull("group")) "" else s.optString("group")
    return if (g == Mesh.conn(hostId)?.info?.optString("group")) "" else g
}

private fun said(s: JSONObject) = s.optDouble("said", s.optDouble("updated", 0.0))

/** A folder as the sidebar says it: home as ~. */
fun shortPath(hostId: String, cwd: String): String {
    val home = Mesh.conn(hostId)?.info?.optString("home").orEmpty()
    return if (home.isNotEmpty() && (cwd == home || cwd.startsWith("$home/"))) "~" + cwd.removePrefix(home) else cwd
}

/** The sections, as Baude's sidebar makes them (groups()): the Bots first (by folder), then each folder by its newest
 * chat, or each day; a chat nested under the one that forked or started it; archived ones last. query: what's
 * searched for (title, folder, host, Bot's name). */
fun sections(query: String): List<Section> {
    val q = query.trim().lowercase()
    val pick = HomePrefs.host.value
    val shown = Mesh.sessions.keys.filter { pick == "all" || it == pick }.sorted()
    val live = ArrayList<Item>()
    val archived = ArrayList<Item>()
    val groups = HomePrefs.groups.toSet()
    // (as Baude's sidebar lists them: each host's sessions that pass the host picked and the groups shown; a chat an
    // agent started, on another host or in another group, pulled in to go under it if that's listed; then the search)
    val everywhere = Mesh.sessions.flatMap { (h, list) -> list.filter { !it.optBoolean("draft") }.map { h to it } }
    fun key(h: String, s: JSONObject) = "$h/${s.optString("id")}"
    val byKey = HashMap<String, Pair<String, JSONObject>>() // ("host/id" and "host/cli_id": the session)
    val byId = HashMap<String, Pair<String, JSONObject>>()  // (id or cli_id alone: an agent on another host)
    for ((h, s) in everywhere) for (id in listOfNotNull(s.optString("id"), s.str("cli_id"))) {
        byKey["$h/$id"] = h to s
        byId.putIfAbsent(id, h to s)
    }
    fun agent(h: String, s: JSONObject) = s.str("spawned_by")?.let { p -> byKey["$h/$p"] ?: byId[p] }?.takeIf { it.second !== s }
    val listedKeys = everywhere.filter { (h, s) -> h in shown && (groups.isEmpty() || groupOf(h, s) in groups) }
        .map { (h, s) -> key(h, s) }.toMutableSet()
    val pulled = HashSet<String>()
    while (true) {
        val more = everywhere.filter { (h, s) -> key(h, s) !in listedKeys && agent(h, s)?.let { key(it.first, it.second) } in listedKeys }
            .map { (h, s) -> key(h, s) }
        if (more.isEmpty()) break
        listedKeys.addAll(more)
        pulled.addAll(more)
    }
    val found = everywhere.filter { (h, s) -> key(h, s) in listedKeys && (q.isEmpty() || listOf(s.str("title").orEmpty(),
        s.optString("cwd"), Mesh.label(h), s.optJSONObject("bot")?.optString("name").orEmpty()).any { it.lowercase().contains(q) }) }
    val kept = found.map { (h, s) -> key(h, s) }.toMutableSet()
    while (true) { // (one pulled in whose agent isn't listed after all (not found by the search, say): nor is it)
        val lost = found.filter { (h, s) -> key(h, s) in kept && key(h, s) in pulled && agent(h, s)?.let { key(it.first, it.second) } !in kept }
        if (lost.isEmpty()) break
        lost.forEach { (h, s) -> kept.remove(key(h, s)) }
    }
    // (nested under the one it forked from, on its host, or the agent that started it, wherever that is, if that's
    // listed; in its own place otherwise)
    fun parent(h: String, s: JSONObject): Pair<String, JSONObject>? {
        s.str("fork_from")?.let { f -> byKey["$h/$f"]?.takeIf { it.second !== s && key(it.first, it.second) in kept }?.let { return it } }
        return agent(h, s)?.takeIf { key(it.first, it.second) in kept }
    }
    val children = HashMap<String, MutableList<Pair<String, JSONObject>>>()
    val top = ArrayList<Pair<String, JSONObject>>()
    for ((h, s) in found) {
        if (key(h, s) !in kept) continue
        val p = parent(h, s)
        if (p != null) children.getOrPut(key(p.first, p.second)) { ArrayList() }.add(h to s) else top.add(h to s)
    }
    fun flat(h: String, s: JSONObject, depth: Int, into: MutableList<Item>, seen: MutableSet<String>) {
        if (!seen.add(key(h, s))) return // (a loop of parents: each once)
        into.add(Item(h, s, depth))
        for ((ch, c) in children[key(h, s)].orEmpty().sortedByDescending { said(it.second) }) flat(ch, c, depth + 1, into, seen)
    }
    val seen = HashSet<String>()
    for ((h, s) in top.sortedByDescending { said(it.second) }) flat(h, s, 0, if (s.optBoolean("archived")) archived else live, seen)
    for ((h, s) in found) if (key(h, s) in kept && key(h, s) !in seen) // (in a loop of parents: in its own place)
        flat(h, s, 0, if (s.optBoolean("archived")) archived else live, seen)
    val several = shown.count { !Mesh.sessions[it].isNullOrEmpty() } > 1
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
                0L -> "Today"
                1L -> "Yesterday"
                7L -> "Older"
                else -> today.minusDays(n).format(DateTimeFormatter.ofPattern("MMM d", Locale.ENGLISH))
            }
            out.add(Section(if (n == 7L) "date:older" else "date:$n", label, null, null, group.flatten()))
        }
    } else {
        val (bots, chats) = runs(live).partition { it[0].bot != null }
        if (bots.isNotEmpty()) out.add(Section("bots", "Bots", null, null, bots.sortedByDescending { said(it[0].s) }.flatten()))
        val folders = chats.groupBy { it[0].hostId to it[0].s.optString("cwd") }
        for ((key, group) in folders.entries.sortedByDescending { e -> e.value.maxOf { said(it[0].s) } }) {
            val (hostId, cwd) = key
            val name = if (cwd.isEmpty()) "Unknown folder" else shortPath(hostId, cwd).trimEnd('/').substringAfterLast('/')
                .ifBlank { "/" }
            val label = if (several) "$name on ${Mesh.label(hostId)}" else name
            out.add(Section("remote:$hostId:$cwd", label, hostId, cwd, group.flatten(),
                full = shortPath(hostId, cwd) + " on " + Mesh.label(hostId)))
        }
    }
    if (archived.isNotEmpty()) out.add(Section("archived", "Archived", null, null, archived))
    return out
}

/** The top-level rows a section shows before "Show N more", as Baude's (by date: all today's, 10 yesterday's, 5 of
 * an earlier day's; a folder's 8; the Bots' and archived ones', all). */
private fun limit(key: String): Int = when {
    key == "date:0" || key == "bots" || key == "archived" -> Int.MAX_VALUE
    key == "date:1" -> 10
    key.startsWith("date:") -> 5
    else -> 8
}

/** The home screen: Baude's whole sidebar. New session (on the host picked beside it), search, the sessions of every
 * host (or one) grouped as Baude groups them: Bots first, each folder, archived folded; ⋮ to group by date and pick
 * groups. */
@Composable
fun Home(open: (String, String) -> Unit, hosts: () -> Unit, feedback: () -> Unit) {
    val c = LocalBaude.current
    val ctx = androidx.compose.ui.platform.LocalContext.current
    LaunchedEffect(Unit) { HomePrefs.load(ctx) }
    Mesh.tick.value // (a host's state changed: drawn again)
    var query by rememberSaveable { mutableStateOf("") }
    var starting by remember { mutableStateOf<Pair<String?, String?>?>(null) } // (New session: its host and folder)
    val more = remember { mutableStateListOf<String>() } // (sections shown whole this run: Show N more)
    Column(Modifier.fillMaxSize().background(c.frame)) {
        Bar("Baude", end = {
            IconButton(Icons.Filled.Dns, "Hosts", onClick = hosts)
            Menu(listOf("Send Feedback…" to feedback))
        })
        val down = Mesh.hosts.mapNotNull { h -> Mesh.conn(h.id)?.takeIf { it.state == "down" || it.state == "key" } }
        if (Mesh.key.value == null || Mesh.hosts.isEmpty()) GreyBanner("Add a host to see its sessions.", onClick = hosts)
        for (conn in down) GreyBanner("Can't connect to ${Mesh.label(conn.id)}: ${conn.why ?: "no answer"}", onClick = hosts)
        // the top: New session and the host it's on, then the search and ⋮ (Baude's sidebar's top box)
        Column(Modifier.padding(start = 8.dp, end = 8.dp, top = 8.dp, bottom = 4.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(2.dp)) {
                SidebarAction(Icons.AutoMirrored.Filled.NoteAdd, "New session", Modifier.weight(1f)) {
                    starting = (HomePrefs.host.value.takeIf { it != "all" }) to null
                }
                HostPick()
            }
            Row(Modifier.padding(top = 6.dp), verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(2.dp)) {
                SearchField(query, { query = it }, "Search sessions", Modifier.weight(1f))
                ViewMenu()
            }
        }
        val all = sections(query)
        if (all.isEmpty()) {
            Text(if (query.isNotBlank()) "No sessions match" else "No sessions yet", style = Type.body, color = c.dim,
                textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth().padding(top = 24.dp))
            return@Column
        }
        LazyColumn(Modifier.fillMaxSize().navigationBarsPadding().padding(horizontal = Gap.inside)) {
            for (sec in all) {
                val folded = query.isBlank() && sec.key in HomePrefs.collapsed
                if (sec.key != "bots") item(key = "h:" + sec.key) {
                    Header(sec, folded, plus = sec.hostId?.takeIf { Mesh.conn(it)?.state == "up" }?.let { h ->
                        { starting = h to sec.folder } }) {
                        if (sec.key in HomePrefs.collapsed) HomePrefs.collapsed.remove(sec.key) else HomePrefs.collapsed.add(sec.key)
                        HomePrefs.save()
                    }
                }
                if (folded) continue
                val tops = sec.rows.count { it.depth == 0 }
                val cap = if (query.isNotBlank() || sec.key in more) Int.MAX_VALUE else limit(sec.key)
                var seen = 0
                val rows = sec.rows.filter { if (it.depth == 0) seen++; seen <= cap }
                items(rows, key = { sec.key + "/" + it.hostId + "/" + it.id }) { item ->
                    if (item.bot != null && item.depth == 0) BotRow(item) { open(item.hostId, item.id) }
                    else SessionRow(item) { open(item.hostId, item.id) }
                }
                if (tops > cap) item(key = "m:" + sec.key) { // (every row it hides counted, nested ones too)
                    Text("Show ${sec.rows.size - rows.size} more", style = Type.body, color = c.dim,
                        modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(6.dp)).clickable { more.add(sec.key) }
                            .padding(start = 12.dp, top = 4.dp, bottom = 4.dp))
                }
            }
        }
    }
    starting?.let { (h, f) -> NewSession(h, f, done = { starting = null }, opened = open) }
}

/** A section's header (Baude's header_row): its name (.sidebar-folder), the arrow to fold it, + for a new session
 * there. */
@Composable
private fun Header(sec: Section, folded: Boolean, plus: (() -> Unit)?, toggle: () -> Unit) {
    val c = LocalBaude.current
    Row(Modifier.fillMaxWidth().padding(top = 6.dp, start = 4.dp).clip(RoundedCornerShape(6.dp)).clickable(onClick = toggle),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(2.dp)) {
        Row(Modifier.weight(1f), verticalAlignment = Alignment.CenterVertically) {
            Text(sec.label, style = Type.folder, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.alpha(0.6f).weight(1f, fill = false))
            Box(Modifier.size(20.dp).alpha(if (folded) 0.75f else 0.4f), contentAlignment = Alignment.Center) {
                Icon(if (folded) Icons.Filled.KeyboardArrowRight else Icons.Filled.KeyboardArrowDown,
                    if (folded) "Expand" else "Collapse", tint = c.fg, modifier = Modifier.size(16.dp))
            }
        }
        if (plus != null) Box(Modifier.size(24.dp).alpha(0.75f).clip(RoundedCornerShape(6.dp)).clickable(onClick = plus),
            contentAlignment = Alignment.Center) {
            Icon(Icons.Filled.Add, "New session in ${sec.full}", tint = c.fg, modifier = Modifier.size(16.dp))
        }
    }
}

/** A chat's row (Baude's session_row): its title on one line, nested under its parent, its status dot. */
@Composable
private fun SessionRow(item: Item, onClick: () -> Unit) {
    val c = LocalBaude.current
    val s = item.s
    Row(Modifier.fillMaxWidth().padding(top = 1.dp).heightIn(min = 26.dp).clip(RoundedCornerShape(6.dp))
        .then(if ("${item.hostId}/${item.id}" in MetaTools.highlight) Modifier.background(c.accentBg.copy(alpha = 0.2f)) else Modifier)
        .clickable(onClick = onClick).padding(start = 6.dp + 18.dp * item.depth, end = 4.dp, top = 1.dp, bottom = 1.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        val title = s.str("title") ?: "(untitled)"
        val bot = item.bot?.optString("name")?.ifBlank { null }
        Text(bot ?: title, style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f))
        HeldTag(s)
        StatusDot(s.optString("state"), (s.optJSONArray("asks")?.length() ?: 0) > 0 || s.optString("state") == "waiting")
    }
}

/** A Bot's row (Baude's bot_row): its picture, its name, what it's on, its status dot. */
@Composable
private fun BotRow(item: Item, onClick: () -> Unit) {
    val c = LocalBaude.current
    val contact = Contact(item.hostId, item.s)
    LaunchedEffect(contact.pictureKey) { Mesh.picture(contact) }
    Row(Modifier.fillMaxWidth().heightIn(min = Gap.botRow).clip(RoundedCornerShape(6.dp))
        .then(if ("${item.hostId}/${item.id}" in MetaTools.highlight) Modifier.background(c.accentBg.copy(alpha = 0.2f)) else Modifier)
        .clickable(onClick = onClick)
        .padding(horizontal = 4.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Avatar(contact.name, Mesh.pictures[contact.pictureKey])
        Spacer(Modifier.width(Gap.avatarGap))
        Column(Modifier.weight(1f)) {
            Text(contact.name, style = Type.bold, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis)
            val what = item.s.str("title")
            if (what != null) Text(what, style = Type.caption, color = c.dim, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        HeldTag(item.s)
        StatusDot(contact.state, contact.asking)
    }
}

/** A row's "held" mark (Baude's held_tag): its host holds messages for the chat, to send or delete in it. */
@Composable
private fun HeldTag(s: org.json.JSONObject) {
    val c = LocalBaude.current
    if ((s.optJSONArray("held")?.length() ?: 0) == 0) return
    Text("held", style = Type.caption, color = c.dim, modifier = Modifier.clip(RoundedCornerShape(4.dp))
        .background(c.raised).padding(horizontal = 5.dp))
}

/** A flat button with an icon and a label, as the sidebar's New session (widgets.sidebar_action). */
@Composable
fun SidebarAction(icon: androidx.compose.ui.graphics.vector.ImageVector, label: String, modifier: Modifier = Modifier,
                  onClick: () -> Unit) {
    val c = LocalBaude.current
    Row(modifier.clip(RoundedCornerShape(6.dp)).clickable(onClick = onClick).padding(horizontal = 8.dp, vertical = 7.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Icon(icon, null, tint = c.fg, modifier = Modifier.size(16.dp))
        Text(label, style = Type.body, color = c.fg, maxLines = 1)
    }
}

/** A search entry, as Gtk.SearchEntry's: its icon, rounded, on the faint fill. */
@Composable
fun SearchField(value: String, set: (String) -> Unit, hint: String, modifier: Modifier = Modifier) {
    val c = LocalBaude.current
    Row(modifier.clip(RoundedCornerShape(8.dp)).background(c.fg.copy(alpha = 0.08f)).padding(horizontal = 8.dp, vertical = 7.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        Icon(Icons.Filled.Search, null, tint = c.dim, modifier = Modifier.size(16.dp))
        Box(Modifier.weight(1f)) {
            if (value.isEmpty()) Text(hint, style = Type.body, color = c.dim, maxLines = 1)
            BasicTextField(value, set, singleLine = true, textStyle = Type.body.copy(color = c.fg),
                cursorBrush = SolidColor(c.accentBg), modifier = Modifier.fillMaxWidth())
        }
    }
}

/** The host the home screen shows (beside New session, as Baude's): every host's, or one's. */
@Composable
private fun HostPick() {
    val c = LocalBaude.current
    var open by remember { mutableStateOf(false) }
    val pick = HomePrefs.host.value
    val ids = (Mesh.hosts.map { it.id } + Mesh.sessions.keys).distinct()
        .filter { Mesh.conn(it)?.info == null || offers(it, "subscribe") }
    Box {
        Row(Modifier.clip(RoundedCornerShape(6.dp)).clickable { open = true }.padding(horizontal = 8.dp, vertical = 7.dp),
            verticalAlignment = Alignment.CenterVertically) {
            Text(if (pick == "all" || pick !in ids) "All hosts" else Mesh.label(pick), style = Type.body, color = c.fg,
                maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.widthIn(max = 120.dp))
            Icon(Icons.Filled.ArrowDropDown, null, tint = c.fg, modifier = Modifier.size(18.dp))
        }
        Popover(open, { open = false }) {
            MenuChoice("All hosts", pick == "all") { HomePrefs.host.value = "all"; HomePrefs.save(); open = false }
            for (id in ids.sortedBy { Mesh.label(it).lowercase() }) MenuChoice(Mesh.label(id), pick == id) {
                HomePrefs.host.value = id; HomePrefs.save(); open = false
            }
        }
    }
}

/** ⋮ beside the search: Group by (Folder, Date), and the groups shown (Baude's sidebar menu). */
@Composable
private fun ViewMenu() {
    val c = LocalBaude.current
    var open by remember { mutableStateOf(false) }
    Box {
        IconButton(Icons.Filled.MoreVert, "View") { open = true }
        Popover(open, { open = false }) {
            Heading("Group by")
            MenuChoice("Folder", HomePrefs.by.value == "folder") { HomePrefs.by.value = "folder"; HomePrefs.save() }
            MenuChoice("Date", HomePrefs.by.value == "date") { HomePrefs.by.value = "date"; HomePrefs.save() }
            val names = MetaTools.groupNames()
            if (names.isNotEmpty()) {
                HorizontalDivider(color = c.rule)
                Heading("Groups")
                for (g in listOf("") + names) MenuChoice(g.ifEmpty { "No group" }, g in HomePrefs.groups) {
                    if (g in HomePrefs.groups) HomePrefs.groups.remove(g) else HomePrefs.groups.add(g)
                    HomePrefs.save()
                }
                if (HomePrefs.groups.isNotEmpty()) DropdownMenuItem({ Text("Show All", style = Type.body, color = c.fg) },
                    { HomePrefs.groups.clear(); HomePrefs.save() })
            }
        }
    }
}

/** A menu as libadwaita's popover is: its own fill, rounded. */
@Composable
fun Popover(open: Boolean, dismiss: () -> Unit, content: @Composable androidx.compose.foundation.layout.ColumnScope.() -> Unit) {
    val c = LocalBaude.current
    DropdownMenu(open, dismiss, containerColor = c.dialog, shape = RoundedCornerShape(12.dp), content = content)
}

@Composable
fun Heading(text: String) {
    val c = LocalBaude.current
    Text(text, style = Type.folder, color = c.dim, modifier = Modifier.padding(horizontal = 14.dp, vertical = 6.dp))
}

/** A menu's item, checked if it's the one (or one of those) picked. */
@Composable
fun MenuChoice(label: String, on: Boolean, enabled: Boolean = true, onClick: () -> Unit) {
    val c = LocalBaude.current
    DropdownMenuItem({ Text(label, style = Type.body, color = if (enabled) c.fg else c.dim) }, onClick, enabled = enabled,
        trailingIcon = { if (on) Icon(Icons.Filled.Check, null, tint = c.accent, modifier = Modifier.size(16.dp)) },
        modifier = Modifier.heightIn(min = 38.dp))
}

/** ☰'s menu of a bar: its items. */
@Composable
fun Menu(items: List<Pair<String, () -> Unit>>) {
    var open by remember { mutableStateOf(false) }
    Box {
        IconButton(Icons.Filled.MoreVert, "Menu") { open = true }
        Popover(open, { open = false }) {
            for ((label, go) in items) MenuChoice(label, false) { open = false; go() }
        }
    }
}
