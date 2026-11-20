package dev.baude.android

import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import org.json.JSONArray
import org.json.JSONObject
import java.util.Base64

/** One of Meta's changes, as desktop Meta's journal keeps them: its id, what it did, the turn it was made in, and its
 * kind and data, from which it's undone and done again (kept with Meta's conversation, so it outlives a restart). */
class Action(val id: String, val said: String, val kind: String, val data: JSONObject, val turn: Int) {
    val undone = mutableStateOf(false)

    fun json(): JSONObject = JSONObject().put("id", id).put("said", said).put("kind", kind).put("data", data)
        .put("turn", turn).put("undone", undone.value)

    companion object {
        fun of(o: JSONObject) = Action(o.optString("id"), o.optString("said"), o.optString("kind"),
            o.optJSONObject("data") ?: JSONObject(), o.optInt("turn")).also { it.undone.value = o.optBoolean("undone") }
    }
}

/** Meta's tools on the phone: desktop Meta's (meta.SHARED_TOOLS and baude.BAUDE_TOOLS, by their names and what they
 * take) as they act on this app: its index, its settings, what's shown under a reply, reading and renaming chats,
 * the New session form, groups, archiving, undo and redo. Served as the MCP server "meta" that Meta's claude runs on
 * its host, relayed back over the phone's connection (start's app_tools): JSON-RPC lines in, answers out. */
object MetaTools {
    const val NAME = "meta"
    val actions = mutableStateListOf<Action>()
    val highlight = mutableStateListOf<String>() // ("host/session": chats highlighted on the home screen)
    val form = mutableStateOf<JSONObject?>(null)  // (the form brought up: {id, form, values, state})
    val formVersion = mutableStateOf(0)           // (filled by Meta: the dialog drawn anew with its values)
    var next = 1
    var turn = 0                                   // (the user's messages so far: an action's turn)
    val BUTTONS = mapOf( // (desktop Baude's, trash and host, and the phone's own)
        "trash" to "Move to Trash… a chat (items: its id), to its host's trash; asks first, and can't be undone here",
        "host" to "Open a host (items: its name): the home screen lists its chats, and New session's on it",
        "send_feedback" to "Send Feedback…", "hosts" to "Hosts: the phone's key, its hosts, the mesh")

    private fun tool(name: String, description: String, required: List<String>, props: JSONObject) =
        JSONObject().put("name", name).put("description", description)
            .put("inputSchema", JSONObject().put("type", "object").put("required", JSONArray(required)).put("properties", props))

    private fun str() = JSONObject().put("type", "string")
    private fun strs() = JSONObject().put("type", "array").put("items", str())
    private fun obj() = JSONObject().put("type", "object")

    val TOOLS: JSONArray = JSONArray(listOf(
        tool("app_index", "A part of the app's index in full, a row a line (its parts: the index's last line). filter: " +
            "only rows containing it (any case).", listOf("section"), JSONObject().put("section", str()).put("filter", str())),
        tool("set_setting", "Change a setting at once (app_index settings: their names and values). Its row shows under " +
            "your reply for the user to adjust.", listOf("name", "value"), JSONObject().put("name", str())
            .put("value", JSONObject().put("type", JSONArray(listOf("string", "number", "boolean"))))),
        tool("show", "Show UI under your reply: setting:<name> (its row), chats or pages (rows to click: their ids or " +
            "names in items), changelog, or button:<action> (a control for the user to press, for what you don't do " +
            "yourself; its actions: app_index buttons).", listOf("what"), JSONObject().put("what", str()).put("items", strs())),
        tool("read_chat", "A chat's conversation as text (its end, if it's long).", listOf("chat"), JSONObject().put("chat", str())),
        tool("rename_chat", "Rename a chat.", listOf("chat", "title"), JSONObject().put("chat", str()).put("title", str())),
        tool("undo", "Undo one of your actions: its id, or last. turn: true undoes all of the turn it was made in.",
            listOf("action"), JSONObject().put("action", str()).put("turn", JSONObject().put("type", "boolean"))),
        tool("redo", "Do again an action of yours that was undone: its id.", listOf("action"), JSONObject().put("action", str())),
        tool("open_form", "Bring up one of the app's forms in your pane (app_index forms: their names), filled with " +
            "values if you know them ({field: value}), for the user to finish and submit; you never submit one. Returns " +
            "its id, its fields (with their choices) and what's still wrong.", listOf("form"),
            JSONObject().put("form", str()).put("values", obj())),
        tool("fill_form", "Fill fields of a form you brought up (its id): values {field: value}. Returns what's still " +
            "wrong, if anything.", listOf("id", "values"), JSONObject().put("id", str()).put("values", obj())),
        tool("highlight_chats", "Highlight chats in the left bar, changing nothing, to show the user which you mean " +
            "(till their next message).", listOf("chats"), JSONObject().put("chats", strs())),
        tool("move_chats", "Put chats in a group (a new name makes it; \"\" for no group).", listOf("chats", "group"),
            JSONObject().put("chats", strs()).put("group", str())),
        tool("make_group", "Make a group, empty till chats are moved to it.", listOf("name"), JSONObject().put("name", str())),
        tool("rename_group", "Rename a group; to a group there already, its chats join that one.", listOf("group", "name"),
            JSONObject().put("group", str()).put("name", str())),
        tool("delete_group", "Delete a group; its chats are in no group then.", listOf("group"), JSONObject().put("group", str())),
        tool("show_groups", "Which groups the left bar lists (\"No group\" for chats in none; none given: all).",
            listOf("groups"), JSONObject().put("groups", strs())),
        tool("archive_chats", "Archive chats: listed under Archived, out of the way.", listOf("chats"), JSONObject().put("chats", strs())),
        tool("unarchive_chats", "Bring archived chats back to the list.", listOf("chats"), JSONObject().put("chats", strs())),
        tool("delete_chats", "Delete chats of this machine's own: their transcripts go to Baude's trash, and undo brings " +
            "them back. Not a host's session, nor the chat open.", listOf("chats"), JSONObject().put("chats", strs())),
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
                    Meta.save()
                }
            }
            "ping" -> answer(JSONObject())
            else -> then(JSONObject().put("jsonrpc", "2.0").put("id", id).put("error", JSONObject().put("code", -32601)
                .put("message", "no method ${msg.optString("method")}")).toString())
        }
    }

    // ---- chats, hosts, groups

    private fun all(): List<Pair<String, Msg.Session>> =
        Mesh.sessions.flatMap { (h, list) -> list.filter { it.draft != true }.map { h to it } }

    /** A chat named as Meta names one: its short id (or more of it), its title, or its Bot's name. */
    private fun find(ref: String): Pair<String, Msg.Session>? {
        val r = ref.trim().lowercase()
        if (r.isEmpty()) return null
        val all = all()
        return all.firstOrNull { (_, s) -> r.length >= 4 && s.id.orEmpty().lowercase().startsWith(r) }
            ?: all.firstOrNull { (_, s) -> s.title?.ifBlank { null }?.lowercase() == r }
            ?: all.firstOrNull { (_, s) -> s.bot?.name?.ifBlank { null }?.lowercase() == r }
    }

    private fun title(s: Msg.Session) = s.bot?.name?.ifBlank { null }?.let { "Bot $it" } ?: s.title?.ifBlank { null } ?: "(untitled)"

    private fun chats(refs: List<String>): Pair<List<Pair<String, Msg.Session>>, List<String>> =
        refs.mapNotNull(::find).distinctBy { it.first + it.second.id.orEmpty() } to refs.filter { find(it) == null }

    private fun notFound(missing: List<String>) =
        "No chat ${missing.joinToString(", ") { "'$it'" }}: app_index chats lists them, each with its id."

    fun hostByName(name: String): String? =
        Mesh.reached("start").firstOrNull { it.second.equals(name, true) || it.first == name }?.first

    fun groupNames(): List<String> = (Mesh.sessions.keys.flatMap { id ->
        Mesh.conn(id)?.info?.groups.orEmpty() - (Mesh.conn(id)?.info?.let { it.group.orEmpty() } ?: "")
    } + HomePrefs.made + HomePrefs.chatGroups.values).filter { it.isNotBlank() }.distinct().sorted()

    private fun key(h: String, s: Msg.Session) = "$h/${s.id.orEmpty()}"

    // ---- the journal

    private fun act(said: String, kind: String, data: JSONObject): Action {
        val a = Action("a${next++}", said, kind, data, turn)
        actions.add(a)
        Meta.under(Line("action", a.id))
        return a
    }

    private fun overrides(keys: JSONObject, to: (String) -> String?) {
        for (k in keys.keys()) {
            val v = to(k)
            if (v == null) HomePrefs.chatGroups.remove(k) else HomePrefs.chatGroups[k] = v
        }
    }

    private fun was(o: JSONObject, k: String): String? = if (o.isNull(k)) null else o.optString(k)

    /** An action undone (or done again): what went wrong, if anything, to done. */
    fun apply(a: Action, undo: Boolean, done: (String?) -> Unit) {
        val d = a.data
        val finish = { e: String? -> if (e == null) a.undone.value = undo; HomePrefs.save(); Meta.save(); done(e) }
        when (a.kind) {
            "rename" -> rename(d.optString("host"), d.optString("sid"), d.optString(if (undo) "from" else "to"), finish)
            "setting" -> finish(setting(d.optString("name"), d.optString(if (undo) "from" else "to")))
            "move" -> {
                val keys = d.optJSONObject("keys") ?: JSONObject()
                val to = d.optString("to")
                if (undo) {
                    overrides(keys) { was(keys, it) }
                    if (d.optBoolean("made")) HomePrefs.made.remove(to)
                } else {
                    overrides(keys) { to }
                    if (d.optBoolean("made") && to !in HomePrefs.made) HomePrefs.made.add(to)
                }
                finish(null)
            }
            "make_group" -> {
                val g = d.optString("name")
                if (undo) HomePrefs.made.remove(g) else if (g !in HomePrefs.made) HomePrefs.made.add(g)
                finish(null)
            }
            "group" -> {
                val keys = d.optJSONObject("keys") ?: JSONObject()
                val from = d.optString("from")
                val to = d.optString("to")
                if (undo) {
                    overrides(keys) { was(keys, it) }
                    if (d.optBoolean("from_made") && from !in HomePrefs.made) HomePrefs.made.add(from)
                    if (to.isNotEmpty() && !d.optBoolean("to_made")) HomePrefs.made.remove(to)
                } else {
                    overrides(keys) { to }
                    if (d.optBoolean("from_made")) HomePrefs.made.remove(from)
                    if (to.isNotEmpty() && to !in HomePrefs.made) HomePrefs.made.add(to)
                }
                finish(null)
            }
            "show_groups" -> {
                val l = d.optJSONArray(if (undo) "from" else "to").strings()
                HomePrefs.groups.clear(); HomePrefs.groups.addAll(l)
                finish(null)
            }
            "archive" -> {
                val op = d.optString("op").let { if (undo) (if (it == "archive") "unarchive" else "archive") else it }
                archive(d.optJSONArray("chats").list2(), op) { failed -> finish(failed.ifEmpty { null }?.joinToString("; ")) }
            }
            else -> done("not an action of the phone's")
        }
    }

    private fun JSONArray?.list2(): List<Pair<String, String>> = if (this == null) emptyList()
    else (0 until length()).mapNotNull { optJSONArray(it)?.let { p -> p.optString(0) to p.optString(1) } }

    private fun undoMany(acts: List<Action>, undo: Boolean, done: (List<String>) -> Unit) {
        val failed = ArrayList<String>()
        fun step(i: Int) {
            if (i < 0 || i >= acts.size) return done(failed)
            apply(acts[i], undo) { e -> if (e != null) failed.add("${acts[i].said}: $e"); step(if (undo) i - 1 else i + 1) }
        }
        step(if (undo) acts.size - 1 else 0) // (undone newest first)
    }

    /** An action of Meta's undone or done again from its row (Undo, Redo). */
    fun button(a: Action, undo: Boolean) = apply(a, undo) { e -> if (e != null) Meta.chat.said.value = "Couldn't: $e" }

    // ---- the app's own changes, each checked with its host where it's the host's

    private fun rename(h: String, sid: String, title: String, then: (String?) -> Unit) {
        var answered = false
        Mesh.ask(h, Msg.Rename(session = sid, title = title).json()) { r ->
            if (answered) return@ask
            answered = true
            then(if (r.optString("op") == Op.RENAMED) null else Msg.Error.from(r).said.orEmpty().ifBlank { "it said no" })
        }
        Mesh.later(30_000) { if (!answered) { answered = true; then("its host didn't answer") } }
    }

    /** Each chat archived or unarchived (op), its host's answer checked: what didn't go, to then. */
    private fun archive(chats: List<Pair<String, String>>, op: String, then: (List<String>) -> Unit) {
        val failed = ArrayList<String>()
        var left = chats.size
        if (left == 0) return then(failed)
        for ((h, sid) in chats) Mesh.ask(h, (if (op == "archive") Msg.Archive(session = sid).json() else Msg.Unarchive(session = sid).json())) { r ->
            if (r.optString("op") != op + "d") failed.add("${Mesh.session(h, sid)?.let(::title) ?: sid.take(8)}: " +
                Msg.Error.from(r).said.orEmpty().ifBlank { "it said no" })
            if (--left == 0) then(failed)
        }
    }

    private fun setting(name: String, v: String): String? = when (name) {
        "sidebar_group" -> if (v !in setOf("folder", "date")) "sidebar_group is folder or date"
            else { HomePrefs.by.value = v; HomePrefs.save(); null }
        "sidebar_host" -> {
            val to = if (v == "all") "all" else hostByName(v) ?: Mesh.conn(v)?.id
            if (to == null) "no host $v: all, or ${Mesh.reached("subscribe").joinToString(", ") { it.second }}"
            else { HomePrefs.host.value = to; HomePrefs.save(); null }
        }
        else -> "No setting '$name': they're sidebar_group, sidebar_host."
    }

    private fun settingValue(name: String) = when (name) {
        "sidebar_group" -> HomePrefs.by.value
        "sidebar_host" -> HomePrefs.host.value.let { if (it == "all") "all" else Mesh.label(it) }
        else -> ""
    }

    // ---- the forms (desktop Meta's: Baude's forms(), as the phone has them)

    /** A form: its heading, its verb, and its fields, each (name, what it takes, its choices or null). */
    class FormDef(val heading: String, val verb: String, val about: String, val fields: List<Triple<String, String, (JSONObject) -> List<String>?>>)

    /** What a host offers to run (spawn_agent's agent): its models, by name. */
    private fun agents(values: JSONObject): List<String> =
        hostByName(values.optString("host"))?.let { h -> models(h).map { it.name } }.orEmpty()

    val FORMS = linkedMapOf(
        "add_host" to FormDef("Add a host", "Add", "add a host Baude reaches over ssh", listOf(
            Triple("destination", "user@host (or user@host:port): over Tailscale, its Tailscale name", { _: JSONObject -> null }),
            Triple("command", "its host process's command there, run as `<command> connect`: empty if baude_headless.py " +
                "is on its PATH, else its path (in a git checkout of Baude, <checkout>/baude_headless.py)", { _: JSONObject -> null }))),
        "new_group" to FormDef("New group", "Create", "make a group of chats", listOf(
            Triple("name", "the group's name", { _: JSONObject -> null }))),
        "spawn_agent" to FormDef("Spawn an agent", "Start", "start an agent (a session) on a host: the host, then what it " +
            "offers to run, the folder, group and prompt", listOf(
            Triple("host", "the host it runs on", { _: JSONObject -> Mesh.reached("start").map { it.second } }),
            Triple("agent", "what runs it: one of the host's", ::agents),
            Triple("folder", "the folder it starts in, on that host", { _: JSONObject -> null }),
            Triple("group", "its group (or empty)", { _: JSONObject -> null }),
            Triple("prompt", "what it's asked first", { _: JSONObject -> null }))),
    )

    /** Its fields as Meta's told them (Form.described): each one's name, what it takes, what it has, its choices. */
    private fun described(form: String, values: JSONObject): JSONArray = JSONArray(FORMS[form]!!.fields.map { (name, takes, choices) ->
        JSONObject().put("field", name).put("takes", takes).put("value", values.optString(name)).apply {
            choices(values)?.let { put("choices", JSONArray(it)) }
        }
    })

    /** What's still wrong with it (Form.check), or null: it's ready for the user to submit. */
    fun wrong(form: String, values: JSONObject): String? = when (form) {
        "add_host" -> if (Host.parse(values.optString("destination")) == null) "Where ssh goes: user@host (or user@host:port)" else null
        "new_group" -> {
            val n = values.optString("name").split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")
            if (n.isEmpty()) "A group needs a name" else if (n in groupNames()) "There's a group $n already" else null
        }
        "spawn_agent" -> when {
            hostByName(values.optString("host")) == null -> "host: pick one of its choices"
            agents(values).isEmpty() -> "${values.optString("host")} offers no agent"
            values.optString("agent").isNotEmpty() && agents(values).none { it.equals(values.optString("agent"), true) } ->
                "agent: pick one of ${values.optString("host")}'s"
            else -> null
        }
        else -> null
    }

    private fun fill(form: String, values: JSONObject, given: JSONObject): String? {
        val names = FORMS[form]!!.fields.map { it.first }
        for (k in given.keys()) {
            if (k !in names) return "$form's fields are ${names.joinToString(", ")}"
            values.put(k, given.optString(k))
        }
        return null
    }

    /** The dialog's own edits, kept in the form (so Meta's next fill keeps what the user wrote). */
    fun edited(values: JSONObject) {
        val f = form.value ?: return
        form.value = JSONObject(f.toString()).put("values", values)
    }

    // ---- the tools

    /** A tool called: what it says back (and whether it went wrong), to done, on the main thread. */
    fun call(name: String, args: JSONObject, done: (String, Boolean) -> Unit) {
        val ok = { t: String -> done(t, false) }
        val no = { t: String -> done(t, true) }
        when (name) {
            "app_index" -> {
                val f = args.optString("filter").lowercase()
                val rows = section(args.optString("section")) ?: return no("No part '${args.optString("section")}': " +
                    "they're $PARTS.")
                ok(rows.filter { f.isEmpty() || it.lowercase().contains(f) }.joinToString("\n").ifEmpty { "(none)" })
            }
            "set_setting" -> {
                val n = args.optString("name")
                val v = args.opt("value")?.toString().orEmpty()
                val from = settingValue(n)
                setting(n, v)?.let { return no(it) }
                Meta.under(Line("rows", JSONObject().put("setting", n).toString()))
                if (from == settingValue(n)) return ok("It's $v already.")
                val a = act("$n: $from → $v", "setting", JSONObject().put("name", n).put("from", from).put("to", v))
                ok("$n is $v now (action ${a.id}).")
            }
            "show" -> show(args, ok, no)
            "read_chat" -> {
                val (h, s) = find(args.optString("chat")) ?: return no(notFound(listOf(args.optString("chat"))))
                val size = s.size?.toLong() ?: 0
                Mesh.ask(h, Msg.Transcript(session = s.id.orEmpty(),
                    offset = (size - 60_000).coerceAtLeast(0)).json()) { r ->
                    if (r.optString("op") != Op.TRANSCRIPT) return@ask no(Msg.Error.from(r).said.orEmpty())
                    val data = Msg.TranscriptReply.from(r).data.orEmpty()
                    val text = if (data.isEmpty()) "" else String(Base64.getDecoder().decode(data), Charsets.UTF_8)
                    val said = parse(text, cutFirst = size > 60_000).filter { it.kind in setOf("you", "bot", "tool", "note", "error") }
                        .joinToString("\n") { "[${it.kind}] ${it.text}" }.takeLast(20_000).ifEmpty { "(nothing said yet)" }
                    ok("<chat title=\"${title(s).replace("\"", "&quot;")}\">\n$said\n</chat>")
                }
            }
            "rename_chat" -> {
                val (h, s) = find(args.optString("chat")) ?: return no(notFound(listOf(args.optString("chat"))))
                val to = args.optString("title").split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")
                if (to.isEmpty()) return no("A title is needed.")
                val before = s.title?.ifBlank { null } ?: ""
                if (to == before) return ok("It's called that already.")
                rename(h, s.id.orEmpty(), to) { e ->
                    if (e != null) return@rename no("Couldn't rename it: $e")
                    val a = act("Renamed “$before” to “$to”", "rename", JSONObject().put("host", h).put("sid", s.id.orEmpty())
                        .put("from", before).put("to", to))
                    ok("Renamed (action ${a.id}).")
                }
            }
            "undo" -> {
                val ref = args.optString("action").trim()
                val a = if (ref.isEmpty() || ref == "last") actions.lastOrNull { !it.undone.value } else actions.firstOrNull { it.id == ref }
                a ?: return no("No such action of yours: its id is in your replies.")
                val acts = if (args.optBoolean("turn")) actions.filter { it.turn == a.turn } else listOf(a)
                if (acts.all { it.undone.value }) return ok("That's undone already.")
                undoMany(acts.filter { !it.undone.value }, true) { failed ->
                    ok("Undone: ${acts.filter { it.undone.value }.joinToString(", ") { it.said }}." +
                        if (failed.isNotEmpty()) " Not undone: ${failed.joinToString("; ")}." else "")
                }
            }
            "redo" -> {
                val a = actions.firstOrNull { it.id == args.optString("action") && it.undone.value }
                    ?: return no("No action of yours with that id is undone.")
                apply(a, false) { e -> if (e != null) no(e) else ok("Done again: ${a.said}.") }
            }
            "open_form" -> {
                val f = args.optString("form").trim()
                val def = FORMS[f] ?: return no("No form '$f': they're ${FORMS.keys.joinToString(", ")} (app_index forms).")
                val values = JSONObject().apply { def.fields.forEach { put(it.first, "") } }
                fill(f, values, args.optJSONObject("values") ?: JSONObject())?.let { return no(it) }
                val id = "f${next++}"
                form.value = JSONObject().put("id", id).put("form", f).put("values", values).put("state", "open")
                formVersion.value++
                val w = wrong(f, values)
                ok("Form $id (${def.heading}) is up for the user to finish and submit; you don't submit it. Its fields: " +
                    "${described(f, values)}. " + (w?.let { "Still wrong: $it" } ?: "It's ready to submit."))
            }
            "fill_form" -> {
                val id = args.optString("id").trim()
                val f = form.value?.takeIf { it.optString("id") == id } ?: return no("No form '$id' of yours: open_form " +
                    "gives a form's id.")
                if (f.optString("state", "open") != "open") return no("That form is ${f.optString("state")} already.")
                val values = JSONObject(f.optJSONObject("values")?.toString() ?: "{}")
                fill(f.optString("form"), values, args.optJSONObject("values") ?: JSONObject())?.let { return no(it) }
                form.value = JSONObject(f.toString()).put("values", values)
                formVersion.value++
                val w = wrong(f.optString("form"), values)
                ok(if (w != null) "Filled. Still wrong: $w" else "Filled: it's ready for the user to submit.")
            }
            "highlight_chats" -> {
                val (found, missing) = chats(args.optJSONArray("chats").strings())
                if (missing.isNotEmpty()) return no(notFound(missing))
                highlight.clear()
                highlight.addAll(found.map { (h, s) -> key(h, s) })
                ok("Highlighted till the user's next message.")
            }
            "move_chats" -> {
                val (found, missing) = chats(args.optJSONArray("chats").strings())
                if (missing.isNotEmpty()) return no(notFound(missing))
                val to = args.optString("group").trim()
                val keys = JSONObject()
                for ((h, s) in found) if (groupOf(h, s) != to) keys.put(key(h, s), HomePrefs.chatGroups[key(h, s)] ?: JSONObject.NULL)
                if (keys.length() == 0) return ok("They're in ${to.ifEmpty { "no group" }} already.")
                val made = to.isNotEmpty() && to !in groupNames()
                val a = act("Moved ${keys.length()} chat${if (keys.length() != 1) "s" else ""} to ${to.ifEmpty { "no group" }}",
                    "move", JSONObject().put("keys", keys).put("to", to).put("made", made))
                apply(a, false) { ok("Moved (action ${a.id}).") }
            }
            "make_group" -> {
                val g = args.optString("name").trim().ifEmpty { return no("A name is needed.") }
                if (g in groupNames()) return no("There's a group '$g' already.")
                val a = act("Made group $g", "make_group", JSONObject().put("name", g))
                apply(a, false) { ok("Made (action ${a.id}).") }
            }
            "rename_group", "delete_group" -> {
                val g = args.optString("group")
                if (g !in groupNames()) return no("No group '$g': app_index groups lists them.")
                val to = if (name == "rename_group") args.optString("name").trim().ifEmpty { return no("A name is needed.") } else ""
                if (to == g) return ok("It's called that already.")
                val keys = JSONObject()
                for ((h, s) in all()) if (groupOf(h, s) == g) keys.put(key(h, s), HomePrefs.chatGroups[key(h, s)] ?: JSONObject.NULL)
                val a = act(if (to.isEmpty()) "Deleted group $g" else "Renamed group $g to $to", "group", JSONObject()
                    .put("keys", keys).put("from", g).put("to", to).put("from_made", g in HomePrefs.made)
                    .put("to_made", to.isNotEmpty() && to in HomePrefs.made))
                apply(a, false) { ok("${if (to.isEmpty()) "Deleted" else "Renamed"} (action ${a.id}).") }
            }
            "show_groups" -> {
                val given = args.optJSONArray("groups").strings().map { if (it == "No group") "" else it }
                val unknown = given.filter { it.isNotEmpty() && it !in groupNames() }
                if (unknown.isNotEmpty()) return no("No group ${unknown.joinToString(", ") { "'$it'" }}: app_index groups lists them.")
                val a = act("Showing " + (if (given.isEmpty()) "all groups" else given.joinToString(", ") { it.ifEmpty { "No group" } }),
                    "show_groups", JSONObject().put("from", JSONArray(HomePrefs.groups.toList())).put("to", JSONArray(given)))
                apply(a, false) { ok("Shown (action ${a.id}).") }
            }
            "archive_chats", "unarchive_chats" -> {
                val (found, missing) = chats(args.optJSONArray("chats").strings())
                if (missing.isNotEmpty()) return no(notFound(missing))
                val op = if (name == "archive_chats") "archive" else "unarchive"
                val change = found.filter { (_, s) -> (s.archived == true) != (op == "archive") } // (only those it
                if (change.isEmpty()) return ok("They're ${op}d already.")                             // changes: undone)
                val pairs = change.map { (h, s) -> h to s.id.orEmpty() }
                archive(pairs, op) { failed ->
                    val went = pairs.filter { (h, sid) -> failed.none { it.startsWith((Mesh.session(h, sid)?.let(::title) ?: sid.take(8)) + ":") } }
                    if (went.isEmpty()) return@archive no("Couldn't: ${failed.joinToString("; ")}")
                    val a = act((if (op == "archive") "Archived " else "Unarchived ") +
                        went.joinToString(", ") { (h, sid) -> Mesh.session(h, sid)?.let(::title) ?: sid.take(8) }, "archive",
                        JSONObject().put("op", op).put("chats", JSONArray(went.map { JSONArray(listOf(it.first, it.second)) })))
                    ok("${op.replaceFirstChar(Char::uppercase)}d (action ${a.id})." +
                        if (failed.isNotEmpty()) " Not ${op}d: ${failed.joinToString("; ")}." else "")
                }
            }
            "delete_chats" -> no("These are hosts' sessions, and Meta deletes only this machine's own chats: the phone " +
                "has none. archive_chats puts them out of the way.")
            else -> no("No tool '$name'.")
        }
    }

    private fun show(args: JSONObject, ok: (String) -> Unit, no: (String) -> Unit) {
        val what = args.optString("what").trim()
        val items = args.optJSONArray("items").strings()
        val kind = what.substringBefore(":")
        val rest = what.substringAfter(":", "")
        when (kind) {
            "setting" -> {
                if (rest !in setOf("sidebar_group", "sidebar_host")) return no("No setting '$rest': they're sidebar_group, sidebar_host.")
                Meta.under(Line("rows", JSONObject().put("setting", rest).toString()))
            }
            "chats" -> {
                val (found, missing) = chats(items)
                if (missing.isNotEmpty()) return no(notFound(missing))
                Meta.under(Line("rows", JSONObject().put("chats", JSONArray(found.map { (h, s) ->
                    JSONArray(listOf(h, s.id.orEmpty())) })).toString()))
            }
            "pages" -> {
                val have = Meta.pages().map { it.title }
                val missing = items.filter { i -> have.none { it.equals(i, true) } }
                if (missing.isNotEmpty()) return no("No page ${missing.joinToString(", ") { "'$it'" }}: the chat open has " +
                    have.joinToString(", ") + " (app_index pages).")
                Meta.under(Line("rows", JSONObject().put("pages", JSONArray(items.map { i -> have.first { it.equals(i, true) } })).toString()))
            }
            "changelog" -> {
                val h = Meta.host.value ?: return no("Meta's host isn't connected.")
                Meta.under(Line("rows", JSONObject().put("changelog", h).toString()))
            }
            "button" -> {
                if (rest !in BUTTONS) return no("No button '$rest': they're ${BUTTONS.keys.joinToString(", ")} (app_index buttons).")
                val item = items.firstOrNull()
                when (rest) {
                    "trash" -> {
                        val (h, s) = item?.let(::find) ?: return no("There's no button trash without items: app_index buttons.")
                        Meta.under(Line("rows", JSONObject().put("button", "trash").put("host", h).put("sid", s.id.orEmpty())
                            .put("label", "Move “${title(s)}” to Trash…").toString()))
                    }
                    "host" -> {
                        val h = item?.let { hostByName(it) } ?: return no("No host '${item.orEmpty()}': they're " +
                            Mesh.reached("start").joinToString(", ") { it.second } + ".")
                        Meta.under(Line("rows", JSONObject().put("button", "host").put("host", h)
                            .put("label", "Open ${Mesh.label(h)}").toString()))
                    }
                    else -> Meta.under(Line("rows", JSONObject().put("button", rest).put("label", BUTTONS[rest]!!.substringBefore(":")).toString()))
                }
            }
            else -> return no("show takes setting:<name>, chats, pages, changelog or button:<action>.")
        }
        ok("Shown under your reply.")
    }

    const val PARTS = "chats, hosts, groups, settings, forms, buttons, pages"

    /** A part of the index, a row a line. */
    fun section(name: String): List<String>? = when (name) {
        "hosts" -> (Mesh.hosts.map { it.id } + Mesh.members.keys.map { "mesh:$it" }).map { id ->
            "${Mesh.label(id)}: ${Mesh.conn(id)?.state ?: "not connected"}" + if (id.startsWith("mesh:")) " (through the mesh)" else ""
        }
        "chats", "sessions" -> all().map { (h, s) ->
            "${s.id.orEmpty().take(8)} " + listOfNotNull("\"${s.title?.ifBlank { null } ?: "(untitled)"}\"", Mesh.label(h),
                shortPath(h, s.cwd.orEmpty()), s.state.orEmpty(), s.bot?.name?.ifBlank { null }?.let { "Bot $it" },
                groupOf(h, s).ifEmpty { null }?.let { "group $it" }, if (s.archived == true) "archived" else null,
                s.model?.ifBlank { null }).joinToString(" · ")
        }
        "groups" -> listOf("No group") + groupNames()
        "settings" -> listOf("sidebar_group=${settingValue("sidebar_group")} (folder or date: how the left bar, the home " +
            "screen, groups chats)", "sidebar_host=${settingValue("sidebar_host")} (all, or a host's name: whose chats it lists)")
        "forms" -> FORMS.map { (k, f) -> "$k: ${f.about}" }
        "buttons" -> BUTTONS.map { (k, v) -> "$k: $v" }
        "pages" -> Meta.pages().map { it.title }.ifEmpty { listOf("(no chat open)") }
        else -> null
    }
}
