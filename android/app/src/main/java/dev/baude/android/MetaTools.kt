package dev.baude.android

import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import org.json.JSONArray
import org.json.JSONObject
import java.util.Base64

/** One of Meta's changes (an action, as desktop Meta's journal has them): what it did, undone and done again. */
class Action(val id: String, val said: String, val undo: () -> Unit, val redo: () -> Unit) {
    val undone = mutableStateOf(false)
}

/** Meta's tools on the phone: desktop Meta's (meta.SHARED_TOOLS and baude.BAUDE_TOOLS, by their names) as they make
 * sense here, acting on the app: its index, its settings, rows under a reply, reading and renaming chats, groups,
 * archiving, the New session form, undo and redo. Served as the MCP server "meta" that Meta's claude runs on its
 * host, relayed back over the phone's connection (start's app_tools): JSON-RPC lines in, answers out. */
object MetaTools {
    const val NAME = "meta"
    val actions = mutableStateListOf<Action>()
    val highlight = mutableStateListOf<String>() // ("host/session": chats highlighted on the home screen)
    val form = mutableStateOf<JSONObject?>(null)  // (the form brought up: {id, form, values})
    val formVersion = mutableStateOf(0)
    private var next = 1

    private fun tool(name: String, description: String, required: List<String>, props: JSONObject) =
        JSONObject().put("name", name).put("description", description)
            .put("inputSchema", JSONObject().put("type", "object").put("required", JSONArray(required)).put("properties", props))

    private fun str() = JSONObject().put("type", "string")
    private fun strs() = JSONObject().put("type", "array").put("items", str())

    val TOOLS: JSONArray = JSONArray(listOf(
        tool("app_index", "A part of the app's index in full, a row a line (its parts: the index's last line). filter: " +
            "only rows containing it (any case).", listOf("section"), JSONObject().put("section", str()).put("filter", str())),
        tool("set_setting", "Change a setting at once (app_index settings: their names and values). Its row shows under " +
            "your reply for the user to adjust.", listOf("name", "value"), JSONObject().put("name", str())
            .put("value", JSONObject().put("type", JSONArray(listOf("string", "number", "boolean"))))),
        tool("show", "Show UI under your reply: setting:<name> (its row), or chats (rows to tap, each opening its chat: " +
            "their ids or names in items).", listOf("what"), JSONObject().put("what", str()).put("items", strs())),
        tool("read_chat", "A chat's conversation as text (its end, if it's long).", listOf("chat"), JSONObject().put("chat", str())),
        tool("rename_chat", "Rename a chat.", listOf("chat", "title"), JSONObject().put("chat", str()).put("title", str())),
        tool("undo", "Undo one of your actions: its id, or last.", listOf("action"), JSONObject().put("action", str())),
        tool("redo", "Do again an action of yours that was undone: its id.", listOf("action"), JSONObject().put("action", str())),
        tool("open_form", "Bring up one of the app's forms in your pane (app_index forms: their names), for the user to " +
            "finish and submit: its id.", listOf("form"), JSONObject().put("form", str())),
        tool("fill_form", "Fill fields of a form you brought up (its id): values {field: value}. The user submits it.",
            listOf("form", "values"), JSONObject().put("form", str()).put("values", JSONObject().put("type", "object"))),
        tool("highlight_chats", "Highlight chats on the home screen, changing nothing, to show the user which you mean " +
            "(till their next message).", listOf("chats"), JSONObject().put("chats", strs())),
        tool("move_chats", "Put chats in a group (a new name makes it; \"\" for no group).", listOf("chats", "group"),
            JSONObject().put("chats", strs()).put("group", str())),
        tool("make_group", "Make a group, empty till chats are moved to it.", listOf("name"), JSONObject().put("name", str())),
        tool("rename_group", "Rename a group; to a group there already, its chats join that one.", listOf("group", "name"),
            JSONObject().put("group", str()).put("name", str())),
        tool("delete_group", "Delete a group; its chats are in no group then.", listOf("group"), JSONObject().put("group", str())),
        tool("show_groups", "Which groups the home screen lists (\"No group\" for chats in none; none given: all).",
            listOf("groups"), JSONObject().put("groups", strs())),
        tool("archive_chats", "Archive chats: listed under Archived, out of the way.", listOf("chats"), JSONObject().put("chats", strs())),
        tool("unarchive_chats", "Bring archived chats back to the list.", listOf("chats"), JSONObject().put("chats", strs())),
        tool("delete_chats", "Delete chats of this machine's own. The phone has none: its chats are all hosts'.",
            listOf("chats"), JSONObject().put("chats", strs())),
    ))

    /** A JSON-RPC line from Meta's claude: its answer, to then (null for a notification). */
    fun line(raw: String, then: (String?) -> Unit) {
        val msg = try {
            JSONObject(raw)
        } catch (_: Exception) {
            return then(null)
        }
        if (!msg.has("id")) return then(null)
        val id = msg.get("id")
        fun answer(result: JSONObject) = then(JSONObject().put("jsonrpc", "2.0").put("id", id).put("result", result).toString())
        when (msg.optString("method")) {
            "initialize" -> answer(JSONObject().put("protocolVersion",
                msg.optJSONObject("params")?.optString("protocolVersion")?.ifBlank { null } ?: "2025-06-18")
                .put("capabilities", JSONObject().put("tools", JSONObject()))
                .put("serverInfo", JSONObject().put("name", NAME).put("version", "1")))
            "tools/list" -> answer(JSONObject().put("tools", TOOLS))
            "tools/call" -> {
                val p = msg.optJSONObject("params") ?: JSONObject()
                call(p.optString("name"), p.optJSONObject("arguments") ?: JSONObject()) { text, error ->
                    answer(JSONObject().put("content", JSONArray(listOf(JSONObject().put("type", "text").put("text", text))))
                        .put("isError", error))
                }
            }
            "ping" -> answer(JSONObject())
            else -> then(JSONObject().put("jsonrpc", "2.0").put("id", id).put("error", JSONObject().put("code", -32601)
                .put("message", "no method ${msg.optString("method")}")).toString())
        }
    }

    /** A chat named as Meta names one: its short id (or more of it), its title, or its Bot's name. */
    private fun find(ref: String): Pair<String, JSONObject>? {
        val r = ref.trim().lowercase()
        if (r.isEmpty()) return null
        val all = Mesh.sessions.flatMap { (h, list) -> list.filter { !it.optBoolean("draft") }.map { h to it } }
        return all.firstOrNull { (_, s) -> r.length >= 4 && s.optString("id").lowercase().startsWith(r) }
            ?: all.firstOrNull { (_, s) -> s.str("title")?.lowercase() == r }
            ?: all.firstOrNull { (_, s) -> s.optJSONObject("bot")?.str("name")?.lowercase() == r }
    }

    private fun title(s: JSONObject) = s.optJSONObject("bot")?.str("name")?.let { "Bot $it" } ?: s.str("title") ?: "(untitled)"

    private fun chats(args: JSONObject): Pair<List<Pair<String, JSONObject>>, List<String>> {
        val refs = args.optJSONArray("chats").strings()
        val found = refs.mapNotNull(::find)
        return found to refs.filter { find(it) == null }
    }

    private fun act(said: String, undo: () -> Unit, redo: () -> Unit): String {
        val a = Action("a${next++}", said, undo, redo)
        actions.add(a)
        Meta.under(Line("action", a.id))
        return a.id
    }

    /** A tool called: what it says back (and whether it went wrong), to done, on the main thread. */
    fun call(name: String, args: JSONObject, done: (String, Boolean) -> Unit) {
        val ok = { t: String -> done(t, false) }
        val no = { t: String -> done(t, true) }
        when (name) {
            "app_index" -> {
                val f = args.optString("filter").lowercase()
                val rows = section(args.optString("section")) ?: return no("no part ${args.optString("section")}: " + PARTS)
                ok(rows.filter { f.isEmpty() || it.lowercase().contains(f) }.joinToString("\n").ifEmpty { "(none)" })
            }
            "set_setting" -> {
                val v = args.opt("value")?.toString().orEmpty()
                when (args.optString("name")) {
                    "sidebar_group" -> {
                        if (v !in setOf("folder", "date")) return no("sidebar_group is folder or date")
                        val was = HomePrefs.by.value
                        HomePrefs.by.value = v; HomePrefs.save()
                        val id = act("Grouped by $v", { HomePrefs.by.value = was; HomePrefs.save() },
                            { HomePrefs.by.value = v; HomePrefs.save() })
                        Meta.under(Line("rows", JSONObject().put("setting", "sidebar_group").toString()))
                        ok("sidebar_group=$v ($id)")
                    }
                    "sidebar_host" -> {
                        val to = if (v == "all") "all" else hostByName(v) ?: return no("no host $v: all, or " +
                            Mesh.reached().joinToString(", ") { it.second })
                        val was = HomePrefs.host.value
                        HomePrefs.host.value = to; HomePrefs.save()
                        val id = act("Showing ${if (to == "all") "all hosts" else Mesh.label(to)}",
                            { HomePrefs.host.value = was; HomePrefs.save() }, { HomePrefs.host.value = to; HomePrefs.save() })
                        Meta.under(Line("rows", JSONObject().put("setting", "sidebar_host").toString()))
                        ok("sidebar_host=$v ($id)")
                    }
                    else -> no("no setting ${args.optString("name")}: sidebar_group, sidebar_host")
                }
            }
            "show" -> {
                val what = args.optString("what")
                when {
                    what == "chats" -> {
                        val (found, missing) = chats(JSONObject().put("chats", args.optJSONArray("items") ?: JSONArray()))
                        Meta.under(Line("rows", JSONObject().put("chats", JSONArray(found.map { (h, s) ->
                            JSONArray(listOf(h, s.optString("id"))) })).toString()))
                        if (missing.isNotEmpty()) no("shown; not found: " + missing.joinToString(", ")) else ok("shown")
                    }
                    what.startsWith("setting:") && what.removePrefix("setting:") in setOf("sidebar_group", "sidebar_host") -> {
                        Meta.under(Line("rows", JSONObject().put("setting", what.removePrefix("setting:")).toString()))
                        ok("shown")
                    }
                    else -> no("the phone shows chats, or setting:sidebar_group or setting:sidebar_host")
                }
            }
            "read_chat" -> {
                val (h, s) = find(args.optString("chat")) ?: return no("no chat ${args.optString("chat")}")
                val size = s.optLong("size", 0)
                Mesh.ask(h, JSONObject().put("op", "transcript").put("session", s.optString("id"))
                    .put("offset", (size - 60_000).coerceAtLeast(0))) { r ->
                    if (r.optString("op") != "transcript") return@ask no(r.optString("error"))
                    val data = r.optString("data", "")
                    val text = if (data.isEmpty()) "" else String(Base64.getDecoder().decode(data), Charsets.UTF_8)
                    ok(parse(text, cutFirst = size > 60_000).filter { it.kind in setOf("you", "bot", "tool", "note", "error") }
                        .joinToString("\n") { "[${it.kind}] ${it.text}" }.takeLast(20_000).ifEmpty { "(nothing said yet)" })
                }
            }
            "rename_chat" -> {
                val (h, s) = find(args.optString("chat")) ?: return no("no chat ${args.optString("chat")}")
                val sid = s.optString("id")
                val was = s.str("title") ?: ""
                val to = args.optString("title").trim().ifEmpty { return no("a title, please") }
                rename(h, sid, to) { e ->
                    if (e != null) no(e)
                    else ok("renamed (" + act("Renamed “$was” to “$to”", { rename(h, sid, was) {} }, { rename(h, sid, to) {} }) + ")")
                }
            }
            "undo", "redo" -> {
                val ref = args.optString("action")
                val undo = name == "undo"
                val a = (if (ref == "last") actions.lastOrNull { it.undone.value != undo } else actions.firstOrNull { it.id == ref })
                    ?: return no("no action $ref")
                if (a.undone.value == undo) return no("${a.id} is ${if (undo) "undone" else "done"} already")
                if (undo) a.undo() else a.redo()
                a.undone.value = undo
                ok("${if (undo) "undid" else "redid"} ${a.id}")
            }
            "open_form" -> {
                if (args.optString("form") != "new_session") return no("its forms: new_session")
                val id = "f${next++}"
                form.value = JSONObject().put("id", id).put("form", "new_session").put("values", JSONObject())
                formVersion.value++
                ok("$id: new_session, fields host, folder, group, prompt")
            }
            "fill_form" -> {
                val f = form.value?.takeIf { it.optString("id") == args.optString("form") } ?: return no("no form ${args.optString("form")} open")
                val values = f.optJSONObject("values") ?: JSONObject()
                val given = args.optJSONObject("values") ?: JSONObject()
                for (k in given.keys()) {
                    if (k !in setOf("host", "folder", "group", "prompt")) return no("new_session's fields: host, folder, group, prompt")
                    if (k == "host" && hostByName(given.optString(k)) == null) return no("no host ${given.optString(k)} connected")
                    values.put(k, given.optString(k))
                }
                form.value = JSONObject(f.toString()).put("values", values)
                formVersion.value++
                ok("filled")
            }
            "highlight_chats" -> {
                val (found, missing) = chats(args)
                highlight.clear()
                highlight.addAll(found.map { (h, s) -> h + "/" + s.optString("id") })
                if (missing.isNotEmpty()) no("highlighted; not found: " + missing.joinToString(", ")) else ok("highlighted")
            }
            "move_chats" -> {
                val (found, missing) = chats(args)
                if (found.isEmpty()) return no("no such chats: " + missing.joinToString(", "))
                val to = args.optString("group").trim()
                val was = found.associate { (h, s) -> "$h/${s.optString("id")}" to groupOf(h, s) }
                fun put(g: (String) -> String) {
                    for (k in was.keys) HomePrefs.chatGroups[k] = g(k)
                    if (to.isNotEmpty() && to !in HomePrefs.made) HomePrefs.made.add(to)
                    HomePrefs.save()
                }
                put { to }
                ok("moved (" + act("Moved ${found.size} chat(s) to ${to.ifEmpty { "no group" }}", { put { was[it]!! } }, { put { to } }) + ")" +
                    if (missing.isNotEmpty()) "; not found: " + missing.joinToString(", ") else "")
            }
            "make_group" -> {
                val g = args.optString("name").trim().ifEmpty { return no("a name, please") }
                if (g in groupNames()) return no("there's a group $g already")
                HomePrefs.made.add(g); HomePrefs.save()
                ok("made (" + act("Made group $g", { HomePrefs.made.remove(g); HomePrefs.save() },
                    { HomePrefs.made.add(g); HomePrefs.save() }) + ")")
            }
            "rename_group", "delete_group" -> {
                val g = args.optString("group")
                if (g !in groupNames()) return no("no group $g")
                val to = if (name == "rename_group") args.optString("name").trim().ifEmpty { return no("a name, please") } else ""
                val members = Mesh.sessions.flatMap { (h, list) -> list.filter { groupOf(h, it) == g }.map { "$h/${it.optString("id")}" } }
                val wasMade = g in HomePrefs.made
                fun apply(from: String, into: String) {
                    for (k in members) HomePrefs.chatGroups[k] = into
                    HomePrefs.made.remove(from)
                    if (into.isNotEmpty() && into !in HomePrefs.made) HomePrefs.made.add(into)
                    HomePrefs.save()
                }
                apply(g, to)
                val said = if (to.isEmpty()) "Deleted group $g" else "Renamed group $g to $to"
                ok("done (" + act(said, {
                    apply(to, g)
                    if (!wasMade) { HomePrefs.made.remove(g); HomePrefs.save() }
                }, { apply(g, to) }) + ")")
            }
            "show_groups" -> {
                val given = args.optJSONArray("groups").strings().map { if (it == "No group") "" else it }
                val was = HomePrefs.groups.toList()
                fun put(l: List<String>) { HomePrefs.groups.clear(); HomePrefs.groups.addAll(l); HomePrefs.save() }
                put(given)
                ok("showing " + (if (given.isEmpty()) "all" else given.joinToString(", ") { it.ifEmpty { "No group" } }) +
                    " (" + act("Showing " + (if (given.isEmpty()) "all groups" else given.joinToString(", ") { it.ifEmpty { "No group" } }),
                    { put(was) }, { put(given) }) + ")")
            }
            "archive_chats", "unarchive_chats" -> {
                val (found, missing) = chats(args)
                if (found.isEmpty()) return no("no such chats: " + missing.joinToString(", "))
                val op = if (name == "archive_chats") "archive" else "unarchive"
                val back = if (op == "archive") "unarchive" else "archive"
                fun send(o: String) = found.forEach { (h, s) -> Mesh.ask(h, JSONObject().put("op", o).put("session", s.optString("id"))) {} }
                send(op)
                ok("${op}d ${found.size} (" + act((if (op == "archive") "Archived " else "Unarchived ") +
                    found.joinToString(", ") { title(it.second) }, { send(back) }, { send(op) }) + ")" +
                    if (missing.isNotEmpty()) "; not found: " + missing.joinToString(", ") else "")
            }
            "delete_chats" -> no("the phone's chats are all hosts' sessions, which aren't deleted from here: archive them")
            else -> no("no tool $name")
        }
    }

    private fun rename(h: String, sid: String, title: String, then: (String?) -> Unit) {
        var answered = false
        Mesh.ask(h, JSONObject().put("op", "rename").put("session", sid).put("title", title)) { r ->
            if (answered) return@ask
            answered = true
            then(if (r.optString("op") == "renamed") null else r.optString("error").ifBlank { "it said no" })
        }
        Mesh.later(30_000) { if (!answered) { answered = true; then("its host didn't answer") } }
    }

    fun hostByName(name: String): String? = Mesh.reached().firstOrNull { it.second.equals(name, true) || it.first == name }?.first

    fun groupNames(): List<String> = (Mesh.sessions.keys.flatMap { id ->
        Mesh.conn(id)?.info?.optJSONArray("groups").strings() - (Mesh.conn(id)?.info?.optString("group") ?: "")
    } + HomePrefs.made + HomePrefs.chatGroups.values).filter { it.isNotBlank() }.distinct().sorted()

    const val PARTS = "hosts, sessions, groups, settings, forms"

    /** A part of the index, a row a line. */
    fun section(name: String): List<String>? = when (name) {
        "hosts" -> (Mesh.hosts.map { it.id } + Mesh.members.keys.map { "mesh:$it" }).map { id ->
            "${Mesh.label(id)}: ${Mesh.conn(id)?.state ?: "not connected"}" + if (id.startsWith("mesh:")) " (through the mesh)" else ""
        }
        "sessions" -> Mesh.sessions.flatMap { (h, list) ->
            list.filter { !it.optBoolean("draft") }.map { s ->
                "${s.optString("id").take(8)} " + listOfNotNull("\"${s.str("title") ?: "(untitled)"}\"", Mesh.label(h),
                    shortPath(h, s.optString("cwd")), s.optString("state"), s.optJSONObject("bot")?.str("name")?.let { "Bot $it" },
                    groupOf(h, s).ifEmpty { null }?.let { "group $it" }, if (s.optBoolean("archived")) "archived" else null,
                    s.str("model")).joinToString(" · ")
            }
        }
        "groups" -> listOf("No group") + groupNames()
        "settings" -> listOf("sidebar_group=${HomePrefs.by.value} (folder, date: how the home screen groups chats)",
            "sidebar_host=${HomePrefs.host.value.let { if (it == "all") "all" else Mesh.label(it) }} (all, or a host: whose " +
                "chats the home screen lists)")
        "forms" -> listOf("new_session: host, folder, group, prompt (the New session dialog)")
        else -> null
    }
}
