package dev.baude.android

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.unit.DpOffset
import org.json.JSONObject

/** What the phone keeps of a group deleted, for Undo (Baude's delete_group's state). */
class GroupSnapshot(val made: List<String>, val groups: List<String>, val chatGroups: Map<String, String?>)

/** The groups as this phone has them (Baude's move_to_group, make_group, rename_group, delete_group and
 * restore_group_state), over what HomePrefs keeps. */
object Groups {
    /** Every group's name, alphabetical case-insensitively. */
    fun names(): List<String> = MetaTools.groupNames()

    fun move(key: String, g: String) {
        HomePrefs.chatGroups[key] = g
        HomePrefs.save()
    }

    fun make(name: String) {
        if (name !in HomePrefs.made) HomePrefs.made.add(name)
        HomePrefs.save()
    }

    /** The chats ("host/session") in group g. */
    fun members(g: String): List<String> {
        val moved = HomePrefs.chatGroups.filterValues { it == g }.keys
        val own = Mesh.sessions.flatMap { (h, list) -> list.filter { s -> "$h/${s.id.orEmpty()}" !in HomePrefs.chatGroups && groupOf(h, s) == g }
            .map { "$h/${it.id.orEmpty()}" } }
        return (moved + own).toList()
    }

    fun rename(old: String, new: String) {
        if (old == new || old.isBlank() || new.isBlank()) return
        for (k in members(old)) HomePrefs.chatGroups[k] = new
        val i = HomePrefs.made.indexOf(old)
        if (i >= 0) HomePrefs.made.removeAt(i)
        if (new !in HomePrefs.made) HomePrefs.made.add(new)
        val j = HomePrefs.groups.indexOf(old)
        if (j >= 0) {
            HomePrefs.groups.removeAt(j)
            if (new !in HomePrefs.groups) HomePrefs.groups.add(new)
        }
        HomePrefs.save()
    }

    fun delete(name: String): GroupSnapshot {
        val keys = members(name)
        val snap = GroupSnapshot(HomePrefs.made.toList(), HomePrefs.groups.toList(), keys.associateWith { HomePrefs.chatGroups[it] })
        for (k in keys) HomePrefs.chatGroups[k] = ""
        HomePrefs.made.remove(name)
        HomePrefs.groups.remove(name)
        HomePrefs.save()
        return snap
    }

    fun restore(snap: GroupSnapshot) {
        HomePrefs.made.clear()
        HomePrefs.made.addAll(snap.made)
        HomePrefs.groups.clear()
        HomePrefs.groups.addAll(snap.groups)
        for ((k, v) in snap.chatGroups) if (v == null) HomePrefs.chatGroups.remove(k) else HomePrefs.chatGroups[k] = v
        HomePrefs.save()
    }

    /** A group listed, if groups are picked (Baude's put_in): a chat made in g is shown. */
    fun putIn(g: String) {
        if ((HomePrefs.groups.isNotEmpty() || HomePrefs.noGroups.value) && g !in HomePrefs.groups) {
            HomePrefs.groups.add(g)
            HomePrefs.allIfEvery()
            HomePrefs.noGroups.value = false
            HomePrefs.save()
        }
    }
}

/** A session row's menu (Baude's show_menu), opened at the finger by a long-press: Rename…, Bot items (Rotate among them),
 * Move to Group ▸ (its list in place of the items, a back row first), Archive, Stop, Access…, Move to Trash…; Delete alone
 * while its host's gone. at: where it was pressed, in the row. */
@Composable
fun SessionMenu(item: Item, open: Boolean, at: DpOffset, dismiss: () -> Unit) {
    val s = item.s
    val h = item.hostId
    val sid = s.id.orEmpty()
    var page by remember { mutableStateOf("items") }
    var renaming by remember { mutableStateOf(false) }
    var trashing by remember { mutableStateOf(false) }
    var botting by remember { mutableStateOf(false) }
    var grouping by remember { mutableStateOf(false) }
    var accessing by remember { mutableStateOf(false) }
    LaunchedEffect(open) { if (!open) page = "items" }
    fun said(r: JSONObject) = Msg.Error.from(r).said.orEmpty()
    val alive = Mesh.conn(h)?.state == "up" && !off(h)
    val gone = dead(h) || !alive
    val top = item.depth == 0 && s.forkFrom.isNullOrBlank()
    val active = !dead(h) && s.state in listOf("working", "waiting")
    val archived = s.archived == true
    val canBot = alive && takes(h, "bot") && program(s.model) in listOf("claude", "pi")
    Popover(open, dismiss, offset = at, ticks = if (page == "groups") Ticks.END else Ticks.NONE) {
        if (page == "groups") {
            MenuBack(Wording.Sidebar.moveToGroup) { page = "items" }
            MenuDivider()
            val now = groupOf(h, s)
            for (g in Groups.names()) MenuChoice(g, now == g) { dismiss(); Groups.move("$h/$sid", g) }
            MenuChoice(Wording.Sidebar.noGroupItem, now == "") { dismiss(); Groups.move("$h/$sid", "") }
            MenuDivider()
            MenuChoice(Wording.Sidebar.newGroupEllipsis, false) { dismiss(); grouping = true }
            return@Popover
        }
        if (!gone) MenuChoice(Wording.Common.renameEllipsis, false) { dismiss(); renaming = true }
        val canRotate = !gone && !active && s.bot != null && takes(h, "rotate") // (a Codex Bot's too; not at a turn,
        // as Archive isn't)
        fun rotate() {
            dismiss()
            Mesh.ask(h, Msg.Rotate(session = sid).json()) { r ->
                if (r.optString("op") != "rotated") toast(Wording.Trash.hostRefused(host = Mesh.label(h), op = "rotate", why = said(r)))
            }
        }
        if (canRotate && !canBot) MenuChoice(Wording.Sidebar.rotate, false) { rotate() }
        if (!gone && canBot && (top || s.bot != null)) {
            if (s.bot != null) {
                MenuChoice(Wording.Sidebar.botSettingsEllipsis, false) { dismiss(); botting = true }
                if (canRotate) MenuChoice(Wording.Sidebar.rotate, false) { rotate() }
                MenuChoice(Wording.Sidebar.unbot, false) {
                    dismiss()
                    Mesh.ask(h, Msg.Bot(session = sid, bot = null).json().put("bot", JSONObject.NULL)) { r ->
                        if (r.optString("op") == Op.ERROR) toast(Wording.BotSettings.failed(why = said(r)))
                    }
                }
            } else MenuChoice(Wording.Sidebar.makeBotEllipsis, false) { dismiss(); botting = true }
        }
        MenuRow(Wording.Sidebar.moveToGroup) { page = "groups" }
        if (gone) MenuChoice(Wording.Common.delete, false) {
            dismiss()
            HomePrefs.hide(h, s, dead(h) || off(h))
        } else {
            if (archived) MenuChoice(Wording.Sidebar.unarchive, false) {
                dismiss()
                Mesh.ask(h, Msg.Unarchive(session = sid).json()) { r ->
                    if (r.optString("op") != "unarchived") toast(Wording.Trash.hostRefused(host = Mesh.label(h), op = "unarchive", why = said(r)))
                }
            } else if (!active) MenuChoice(Wording.Sidebar.archive, false) {
                dismiss()
                Mesh.ask(h, Msg.Archive(session = sid).json()) { r ->
                    if (r.optString("op") != "archived") toast(Wording.Trash.hostRefused(host = Mesh.label(h), op = "archive", why = said(r)))
                }
            }
            if (active) MenuChoice(Wording.Common.stop, false) {
                dismiss()
                Mesh.conn(h)?.request(Msg.Stop(session = sid).json()) {}
            }
            if (s.takenUp != false) MenuChoice(Wording.Access.accessEllipsis, false) { dismiss(); accessing = true } // (a
            // Claude Desktop chat only listed: the host's rule, till it's written in)
            MenuChoice(Wording.Trash.moveDots, false) { dismiss(); trashing = true }
        }
    }
    if (renaming) RenameSession(item) { renaming = false }
    if (trashing) MoveToTrash(item) { trashing = false }
    if (botting) BotSettings(item) { botting = false }
    if (grouping) GroupName(new = true, moveKey = "$h/$sid") { grouping = false }
    if (accessing) ChatAccess(item) { accessing = false }
}
