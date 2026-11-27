package dev.baude.android

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import org.json.JSONArray
import org.json.JSONObject
import android.widget.Toast
import java.util.Base64

/** A part of an action: what it did, its kind and its data (an action that bundles a turn's changes to the chats has
 * several; a setting's has one). */
class Part(val said: String, val kind: String, val data: JSONObject)

/** One of Meta's changes, as desktop Meta's journal keeps them: its id, what it did, the turn it was made in, and its
 * kind and data, from which it's undone and done again (kept with Meta's conversation, so it outlives a restart). A
 * change to what the left bar lists is marked there until it's kept: a mark is [kind, key, how, note]. */
class Action(val id: String, said: String, var kind: String, var data: JSONObject, val turn: Int) {
    var said by mutableStateOf(said)
    val undone = mutableStateOf(false)
    val kept = mutableStateOf(true)
    val marks = mutableStateListOf<JSONArray>()
    val parts = ArrayList<Part>()

    fun parts(): List<Part> = parts.ifEmpty { listOf(Part(said, kind, data)) }

    fun json(): JSONObject = JSONObject().put("id", id).put("said", said).put("kind", kind).put("data", data)
        .put("turn", turn).put("undone", undone.value).put("kept", kept.value).put("marks", JSONArray(marks.toList()))
        .put("parts", JSONArray(parts.map { JSONObject().put("said", it.said).put("kind", it.kind).put("data", it.data) }))

    companion object {
        fun of(o: JSONObject) = Action(o.optString("id"), o.optString("said"), o.optString("kind"),
            o.optJSONObject("data") ?: JSONObject(), o.optInt("turn")).also { a ->
            a.undone.value = o.optBoolean("undone")
            a.kept.value = o.optBoolean("kept", true)
            val m = o.optJSONArray("marks")
            for (i in 0 until (m?.length() ?: 0)) m!!.optJSONArray(i)?.let { a.marks.add(it) }
            val p = o.optJSONArray("parts")
            for (i in 0 until (p?.length() ?: 0)) p!!.optJSONObject(i)?.let {
                a.parts.add(Part(it.optString("said"), it.optString("kind"), it.optJSONObject("data") ?: JSONObject()))
            }
        }
    }
}

/** Meta's tools on the phone: desktop Meta's (metaagent.SHARED_TOOLS and baudemeta.BAUDE_TOOLS, by their names and what they
 * take) as they act on this app: its index, its settings, what's shown under a reply, reading and renaming chats,
 * the New session form, groups, archiving, undo and redo. Served as the MCP server "meta" that Meta's claude runs on
 * its host, relayed back over the phone's connection (start's app_tools): JSON-RPC lines in, answers out. */
object MetaTools {
    const val NAME = "meta"
    val actions = mutableStateListOf<Action>()
    val highlight = mutableStateListOf<String>() // ("host/session": chats highlighted on the home screen)
    val forms = mutableStateMapOf<String, JSONObject>() // (the forms brought up, by id: {id, form, values, state,
                                                         // pinned, said, error, filled: Meta's fills so far})
    var next = 1
    var turn = 0                                   // (the user's messages so far: an action's turn)
    val BUTTONS = mapOf( // (desktop Baude's, trash and host, and the phone's own)
        "trash" to "Move to Trash… a chat (items: its id), to its host's trash; asks first, and can't be undone here",
        "host" to "Open a host (items: its name): the home screen lists its chats, and New session's on it",
        "send_feedback" to Wording.Fixes.sendFeedback)

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
        tool("read_ask", Wording.Inbox.readAskTool, emptyList(), JSONObject()),
        tool("fill_ask", Wording.Inbox.fillAskTool, listOf("values"), JSONObject().put("values", obj())),
    ))

    /** A JSON-RPC line from Meta's claude: its answer, to then (null for a notification). */
    fun line(raw: String, item: ((String, JSONObject) -> MetaCall?)? = null, then: (String?) -> Unit) {
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
                val args = p.optJSONObject("arguments") ?: JSONObject()
                val at = item?.invoke(p.optString("name"), args)
                call(p.optString("name"), args, at) { text, error ->
                    if (at != null && at.id == null) { // (no card of claude's for it: its own say how it stands)
                        at.state = if (error) "error" else "done"
                        at.result = text
                    }
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

    /** A chat named as Meta names one: its short id (or more of it), or its name: a Bot's, else its title. */
    private fun find(ref: String): Pair<String, Msg.Session>? {
        val r = ref.trim().lowercase()
        if (r.isEmpty()) return null
        val all = all()
        return all.firstOrNull { (_, s) -> r.length >= 4 && s.id.orEmpty().lowercase().startsWith(r) }
            ?: all.firstOrNull { (_, s) -> chatName(s)?.lowercase() == r }
    }

    private fun title(s: Msg.Session) = chatName(s) ?: Wording.Common.untitled

    private fun chats(refs: List<String>): Pair<List<Pair<String, Msg.Session>>, List<String>> =
        refs.mapNotNull(::find).distinctBy { it.first + it.second.id.orEmpty() } to refs.filter { find(it) == null }

    private fun notFound(missing: List<String>) =
        "No chat ${missing.joinToString(", ") { "'$it'" }}: app_index chats lists them, each with its id."

    fun hostByName(name: String): String? =
        Mesh.reached("start").firstOrNull { it.second.equals(name, true) || it.first == name }?.first

    fun groupNames(): List<String> = (Mesh.sessions.keys.flatMap { id ->
        Mesh.conn(id)?.info?.groups.orEmpty() - (Mesh.conn(id)?.info?.let { it.group.orEmpty() } ?: "")
    } + HomePrefs.made + HomePrefs.chatGroups.values).filter { it.isNotBlank() }.distinct().sortedBy { it.lowercase() }
        .let { names -> // (Desktop first, while a host lists Claude Desktop chats: Baude's known_groups)
            val desktop = Wording.Sidebar.desktopGroup
            if (Mesh.sessions.values.any { list -> list.any { it.desktop == true } } || desktop in names) listOf(desktop) + (names - desktop)
            else names
        }

    private fun key(h: String, s: Msg.Session) = "$h/${s.id.orEmpty()}"

    // ---- the journal

    val told = ArrayList<String>() // (what the user did to Meta's actions: said with the next message)
    var bundle: String? = null     // (the action this turn's changes to the chats join)

    private val MARK_STRENGTH = mapOf("archived" to 4, "added" to 3, "moved" to 2, "edited" to 1)
    private val MARK_AFTER = mapOf<Pair<String, String>, String?>("added" to "removed" to null, "added" to "archived" to null,
        "archived" to "added" to null, "removed" to "added" to "edited", "moved" to "removed" to "removed",
        "edited" to "removed" to "removed")

    /** An action Meta's taken (done already): kept in the journal, under the call's card. A change to what the left bar
     * lists joins the turn's change, if it's made one (bundle): a turn's changes to it are one action. */
    fun act(said: String, kind: String, data: JSONObject, marks: List<JSONArray> = emptyList(), at: MetaCall? = null): Action {
        val joined = if (marks.isEmpty()) null else actions.firstOrNull { it.id == bundle && !it.undone.value && !it.kept.value }
        val a = if (joined != null) {
            join(joined, said, kind, data, marks)
            actions.remove(joined)
            actions.add(joined)
            joined
        } else Action("a${next++}", said, kind, data, turn).also {
            it.kept.value = marks.isEmpty()
            it.marks.addAll(marks)
            actions.add(it)
            if (marks.isNotEmpty()) bundle = it.id
            if (at == null) Meta.under(Line("action", it.id))
        }
        if (at != null && a.id !in at.actions) at.actions.add(a.id)
        return a
    }

    /** Action a with another part (desktop's bundle): its marks gathered, a row marked twice as MARK_AFTER says for the
     * two, else the stronger, with both their notes. */
    private fun join(a: Action, said: String, kind: String, data: JSONObject, marks: List<JSONArray>) {
        val parts = ArrayList(a.parts()) + Part(said, kind, data)
        val gathered = LinkedHashMap<String, JSONArray>()
        for (m in a.marks) gathered[m.optString(0) + "|" + m.optString(1)] = m
        for (m in marks) {
            val k = m.optString(0) + "|" + m.optString(1)
            var mark = m
            val old = gathered[k]
            if (old != null) {
                val pair = old.optString(2) to m.optString(2)
                val how = if (pair in MARK_AFTER) MARK_AFTER[pair]
                    else if ((MARK_STRENGTH[pair.first] ?: 0) > (MARK_STRENGTH[pair.second] ?: 0)) pair.first else pair.second
                if (how == null) {
                    gathered.remove(k)
                    continue
                }
                val note = listOf(old.optString(3), m.optString(3)).filter { it.isNotEmpty() }.distinct().joinToString(", ")
                mark = JSONArray(listOf(m.optString(0), m.optString(1), how, note))
            }
            gathered[k] = mark
        }
        a.kind = "bundle"
        a.data = JSONObject()
        a.parts.clear()
        a.parts.addAll(parts)
        a.kept.value = false
        a.marks.clear()
        a.marks.addAll(gathered.values)
        a.said = parts.map { it.said }.distinct().joinToString("; ")
    }

    /** The actions done, not undone and not kept: the left bar marks what they changed. */
    fun unkept(): List<Action> = actions.filter { !it.undone.value && !it.kept.value }

    /** What a call showed, under its card (or, with no card, after its reply). */
    private fun shown(at: MetaCall?, spec: JSONObject) {
        if (at != null) at.shown.add(spec) else Meta.under(Line("rows", spec.toString()))
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
        val parts = if (undo) a.parts().reversed() else a.parts()
        val ran = ArrayList<Part>()
        fun finish(e: String?) {
            if (e == null) a.undone.value = undo
            HomePrefs.save()
            Meta.save()
            done(e)
        }
        fun back(i: Int, why: String?) { // (a part that failed: the ones done put back)
            if (i < 0) return finish(why)
            applyPart(ran[i], !undo) { back(i - 1, why) }
        }
        fun step(i: Int) {
            if (i >= parts.size) return finish(null)
            applyPart(parts[i], undo) { e ->
                if (e == null) {
                    ran.add(parts[i])
                    step(i + 1)
                } else back(ran.size - 1, e)
            }
        }
        step(0)
    }

    /** The part an action's just been given, done (the others were, when they were made). */
    private fun applyNew(a: Action, done: (String?) -> Unit) = applyPart(a.parts().last(), false) { e ->
        HomePrefs.save()
        Meta.save()
        done(e)
    }

    private fun applyPart(p: Part, undo: Boolean, finish: (String?) -> Unit) {
        val d = p.data
        when (p.kind) {
            "rename" -> rename(d.optString("host"), d.optString("sid"), d.optString(if (undo) "from" else "to"), finish)
            "setting" -> {
                val s = MetaSet.ALL[d.optString("name")] ?: return finish("No setting '${d.optString("name")}'.")
                try {
                    s.put(s.coerce(d.opt(if (undo) "from" else "to")))
                    finish(null)
                } catch (e: IllegalArgumentException) {
                    finish(e.message)
                }
            }
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
                if (!undo) HomePrefs.allIfEvery()
                HomePrefs.noGroups.value = undo && d.optBoolean("from_none")
                finish(null)
            }
            "archive" -> {
                val op = d.optString("op").let { if (undo) (if (it == "archive") "unarchive" else "archive") else it }
                archive(d.optJSONArray("chats").list2(), op) { failed -> finish(failed.ifEmpty { null }?.joinToString("; ")) }
            }
            else -> finish("not an action of the phone's")
        }
    }

    private fun JSONArray?.list2(): List<Pair<String, String>> = if (this == null) emptyList()
    else (0 until length()).mapNotNull { optJSONArray(it)?.let { p -> p.optString(0) to p.optString(1) } }

    private fun runMany(acts: List<Action>, undo: Boolean, done: (List<String>) -> Unit) {
        val failed = ArrayList<String>()
        fun step(i: Int) {
            if (i < 0 || i >= acts.size) return done(failed)
            apply(acts[i], undo) { e -> if (e != null) failed.add("${acts[i].said}: $e"); step(if (undo) i - 1 else i + 1) }
        }
        step(if (undo) acts.size - 1 else 0) // (undone newest first)
    }

    private fun toast(text: String) = Toast.makeText(Mesh.app, text, Toast.LENGTH_SHORT).show()

    /** Actions kept: what they marked in the left bar goes; they can still be undone. */
    fun keep(acts: List<Action>) {
        for (a in acts) a.kept.value = true
        Meta.save()
    }

    /** Actions undone, newest first (byUser: Meta's told, with its next message, and what failed is toasted). */
    fun undoMany(acts: List<Action>, byUser: Boolean, done: (List<String>) -> Unit = {}) {
        val todo = acts.filter { !it.undone.value }
        runMany(todo, true) { failed ->
            if (byUser) {
                for (a in todo) if (a.undone.value) told.add("(The user undid your action ${a.id}: ${a.said}.)")
                if (failed.isNotEmpty()) toast(Wording.MetaActions.couldntUndo(failed = failed.joinToString("; ")))
                Meta.save()
            }
            done(failed)
        }
    }

    /** Actions done again, in order; what they marked is marked again. */
    fun redoMany(acts: List<Action>, byUser: Boolean, done: (List<String>) -> Unit = {}) {
        val todo = acts.filter { it.undone.value }
        val failed = ArrayList<String>()
        fun step(i: Int) {
            if (i >= todo.size) {
                if (byUser) Meta.save()
                return done(failed)
            }
            val a = todo[i]
            apply(a, false) { e ->
                if (e == null) {
                    a.kept.value = a.marks.isEmpty()
                    if (byUser) told.add("(The user redid your action ${a.id}: ${a.said}.)")
                } else {
                    failed.add("${a.said}: $e")
                    if (byUser) toast(Wording.MetaActions.couldntRedo(action = a.said, why = e))
                }
                step(i + 1)
            }
        }
        step(0)
    }

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

    // ---- the forms (desktop Meta's: Baude's forms(), as the phone has them)

    /** A form: its heading, its verb, and its fields, each (name, what it takes, its choices or null); said: what was
     * done is said on its card once it's submitted (Form.said). */
    class FormDef(val heading: String, val verb: String, val about: String, val fields: List<Triple<String, String, (JSONObject) -> List<String>?>>,
                  val said: Boolean = true)

    /** What a host offers to run (spawn_agent's agent): its models, by name. */
    private fun agents(values: JSONObject): List<String> =
        hostByName(values.optString("host"))?.let { h -> models(h).map { it.name } }.orEmpty()

    val FORMS = linkedMapOf(
        "add_host" to FormDef(Wording.NewSession.addHost, Wording.Common.add, "add a host Baude reaches over ssh", listOf(
            Triple("destination", "user@host (or user@host:port): over Tailscale, its Tailscale name", { _: JSONObject -> null })),
            said = false),
        "new_group" to FormDef(Wording.NewSession.newGroup, Wording.Common.create, "make a group of chats", listOf(
            Triple("name", "the group's name", { _: JSONObject -> null }))),
        "spawn_agent" to FormDef(Wording.NewSession.startChat, Wording.NewSession.start, "start an agent (a session) on a host: the host, then what it " +
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
            if (n.isEmpty()) Wording.NewSession.groupNeedsName else if (n in groupNames()) Wording.NewSession.groupExists(name = n) else null
        }
        "spawn_agent" -> when {
            hostByName(values.optString("host")) == null && values.optString("host").isNotBlank() ->
                "host: ${values.optString("host")} isn't up now; pick another of its choices"
            hostByName(values.optString("host")) == null -> "host: pick one of its choices"
            agents(values).isEmpty() -> "${values.optString("host")} offers nothing to run"
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

    /** A form's card's own edits, kept in its form (so Meta's next fill keeps what the user wrote). */
    fun edited(id: String, values: JSONObject) {
        val f = forms[id] ?: return
        if (f.optJSONObject("values")?.toString() == values.toString()) return
        forms[id] = JSONObject(f.toString()).put("values", values).put("error", JSONObject.NULL)
        Meta.save()
    }

    /** A form submitted (what was done) or cancelled: kept so, Meta told with the next message. */
    fun closed(id: String, done: String?) {
        val f = forms[id]?.takeIf { it.optString("state", "open") == "open" } ?: return
        val heading = FORMS[f.optString("form")]?.heading ?: f.optString("form")
        forms[id] = JSONObject(f.toString()).put("state", if (done != null) "submitted" else "cancelled")
            .put("said", done ?: JSONObject.NULL).put("error", JSONObject.NULL)
        told.add(if (done != null) "(The user submitted the form $id, $heading: $done)" else "(The user cancelled the form $id, $heading.)")
        Meta.save()
    }

    /** A chat's conversation as Meta reads it (read_chat): its end, if it's long; or why it couldn't be read. */
    private fun readChat(h: String, s: Msg.Session, done: (String?, String?) -> Unit) {
        val ok = { t: String -> done(t, null) }
        val no = { t: String -> done(null, t) }
        val size = s.size?.toLong() ?: 0
        Mesh.ask(h, Msg.Transcript(session = s.id.orEmpty(),
            offset = (size - 60_000).coerceAtLeast(0)).json()) { r ->
            if (r.optString("op") != Op.TRANSCRIPT) return@ask no(Msg.Error.from(r).said.orEmpty())
            val data = Msg.TranscriptReply.from(r).data.orEmpty()
            val text = if (data.isEmpty()) "" else String(Base64.getDecoder().decode(data), Charsets.UTF_8)
            val said = parse(text, cutFirst = size > 60_000).mapNotNull {
                when (it.kind) {
                    "you", "bot", "note", "error" -> "[${it.kind}] ${it.text}"
                    "call" -> "[tool] " + toolLine(JSONObject(it.text))
                    "task" -> "[note] " + taskSaid(JSONObject(it.text))
                    "stopped" -> "[note] " + Wording.ChatNotes.interrupted
                    else -> null
                }
            }.joinToString("\n").takeLast(20_000).ifEmpty { "(nothing said yet)" }
            ok("<chat title=\"${title(s).replace("\"", "&quot;")}\">\n$said\n</chat>")
        }
    }

    // ---- the tools

    /** A tool called: what it says back (and whether it went wrong), to done, on the main thread. */
    fun call(name: String, args: JSONObject, at: MetaCall?, done: (String, Boolean) -> Unit) {
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
                val n = args.optString("name").trim()
                val st = MetaSet.ALL[n] ?: return no("No setting '$n': they're ${MetaSet.ALL.keys.joinToString(", ")}.")
                if (!st.metaSets) return no("$n is the user's to change: show setting:$n for them to.")
                val before = st.get()
                var only: List<String>? = null
                val now = try {
                    st.coerce(args.opt("value")).also {
                        only = st.changed?.invoke(args.opt("value"))
                        st.put(it)
                    }
                } catch (e: IllegalArgumentException) {
                    return no(e.message.orEmpty())
                }
                shown(at, JSONObject().put("setting", n).put("set", now).apply { only?.let { put("hosts", JSONArray(it)) } })
                if (now == before) return ok("$n is ${saidValue(now)} already. Its row is under your reply.")
                val a = act(Wording.MetaActions.settingChanged(setting = st.title(), before = saidValue(before), after = saidValue(now)),
                    "setting", JSONObject().put("name", n).put("from", before).put("to", now), at = at)
                ok("$n is ${saidValue(now)} now (action ${a.id})" + (st.note?.let { "; $it" } ?: "") + ". Its row is under your reply.")
            }
            "show" -> show(args, at, ok, no)
            "read_chat" -> {
                val (h, s) = find(args.optString("chat")) ?: return no(notFound(listOf(args.optString("chat"))))
                readChat(h, s) { text, error -> if (error != null) no(error) else ok(text.orEmpty()) }
            }
            "read_ask" -> {
                val (h, id) = Inbox.shown ?: return no("No ask has been opened in the Inbox.")
                val a = Inbox.find(h, id) ?: return no(Wording.Inbox.notWaiting)
                val about = Inbox.described(h, a)
                if (Inbox.chats(a)) {  // (a chat's own ask: the asking chat, no run)
                    val chat = Mesh.session(h, a.session.orEmpty()) ?: return ok("$about\n(the asking chat isn't listed here)")
                    readChat(h, chat) { said, error -> ok("$about\n" + (said ?: "(the asking chat couldn't be read: $error)")) }
                    return
                }
                Mesh.ask(h, Msg.TaskRunGet(session = a.session.orEmpty(), run = a.run.orEmpty()).json()) { r ->
                    val log = if (r.optString("op") != Op.TASK_RUN) "(its run's log couldn't be read: ${Msg.Error.from(r).said.orEmpty()})"
                        else Msg.TaskRunReply.from(r).log.orEmpty().takeLast(200).joinToString("\n") { it.text.orEmpty() }.ifEmpty { "(empty)" }
                    val run = "<run_log>\n$log\n</run_log>"
                    val bot = Mesh.session(h, a.session.orEmpty())
                        ?: return@ask ok("$about\n$run\n(the asking Bot's chat isn't listed here)")
                    readChat(h, bot) { chat, error -> ok("$about\n$run\n" + (chat ?: "(its Bot's chat couldn't be read: $error)")) }
                }
            }
            "fill_ask" -> {
                val (h, id) = Inbox.shown ?: return no("No ask has been opened in the Inbox.")
                val a = Inbox.find(h, id)?.takeIf { it.status == "waiting" } ?: return no(Wording.Inbox.notWaiting)
                val v = a.versions?.lastOrNull() ?: return no(Wording.Inbox.notWaiting)
                val wrong = Inbox.fill(h, a, v, args.optJSONObject("values") ?: JSONObject())
                ok(if (wrong.isEmpty()) "Filled, for the person to check and press a button." else "Filled what fit. Didn't fit: ${wrong.joinToString("; ")}.")
            }
            "rename_chat" -> {
                val (h, s) = find(args.optString("chat")) ?: return no(notFound(listOf(args.optString("chat"))))
                val to = args.optString("title").split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")
                if (to.isEmpty()) return no("A title is needed.")
                val before = s.title?.ifBlank { null } ?: ""
                if (to == before) return ok("It's called that already.")
                rename(h, s.id.orEmpty(), to) { e ->
                    if (e != null) return@rename no("Couldn't rename it: $e")
                    val a = act(Wording.MetaActions.renamed(before = before, after = to), "rename", JSONObject().put("host", h).put("sid", s.id.orEmpty())
                        .put("from", before).put("to", to), listOf(JSONArray(listOf("chat", key(h, s), "moved", Wording.MetaActions.renamedNote))), at)
                    ok("Renamed (action ${a.id}).")
                }
            }
            "undo" -> {
                val ref = args.optString("action").trim()
                val a = if (ref.isEmpty() || ref == "last") actions.lastOrNull { !it.undone.value } else actions.firstOrNull { it.id == ref }
                a ?: return no("No such action of yours: its id is in your replies.")
                val acts = if (args.optBoolean("turn")) actions.filter { it.turn == a.turn } else listOf(a)
                if (acts.all { it.undone.value }) return ok("That's undone already.")
                undoMany(acts.filter { !it.undone.value }, false) { failed ->
                    ok("Undone: ${acts.filter { it.undone.value }.joinToString(", ") { it.said }}." +
                        if (failed.isNotEmpty()) " Not undone: ${failed.joinToString("; ")}." else "")
                }
            }
            "redo" -> {
                val a = actions.firstOrNull { it.id == args.optString("action") && it.undone.value }
                    ?: return no("No action of yours with that id is undone.")
                redoMany(listOf(a), false) { failed -> if (failed.isNotEmpty()) no(failed.first()) else ok("Done again: ${a.said}.") }
            }
            "open_form" -> {
                val f = args.optString("form").trim()
                val def = FORMS[f] ?: return no("No form '$f': they're ${FORMS.keys.joinToString(", ")} (app_index forms).")
                val values = JSONObject().apply { def.fields.forEach { put(it.first, "") } }
                fill(f, values, args.optJSONObject("values") ?: JSONObject())?.let { return no(it) }
                val id = "f${next++}"
                forms[id] = JSONObject().put("id", id).put("form", f).put("values", values).put("state", "open")
                    .put("pinned", true).put("filled", 0)
                shown(at, JSONObject().put("form_id", id))
                Meta.save()
                val w = wrong(f, values)
                ok("Form $id (${def.heading}) is up for the user to finish and submit; you don't submit it. Its fields: " +
                    "${described(f, values)}. " + (w?.let { "Still wrong: $it" } ?: "It's ready to submit."))
            }
            "fill_form" -> {
                val id = args.optString("id").trim()
                val f = forms[id] ?: return no("No form '$id' of yours: open_form gives a form's id.")
                if (f.optString("state", "open") != "open") return no("That form is ${f.optString("state")} already.")
                val values = JSONObject(f.optJSONObject("values")?.toString() ?: "{}")
                fill(f.optString("form"), values, args.optJSONObject("values") ?: JSONObject())?.let { return no(it) }
                forms[id] = JSONObject(f.toString()).put("values", values).put("filled", f.optInt("filled") + 1)
                    .put("error", JSONObject.NULL)
                Meta.save()
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
                val a = act(Wording.MetaChats.moved(chats = found.filter { (h, s) -> groupOf(h, s) != to }.let { m ->
                    if (m.size == 1) Wording.Senders.chat(name = title(m[0].second)) else Wording.MetaChats.chats(n = m.size) },
                    where = if (to.isEmpty()) Wording.MetaChats.noGroup else Wording.MetaChats.groupNamed(name = to)),
                    "move", JSONObject().put("keys", keys).put("to", to).put("made", made),
                    keys.keys().asSequence().map { k -> JSONArray(listOf("chat", k, "moved",
                        Wording.MetaChats.movedNote(group = to.ifEmpty { Wording.MetaChats.noGroup }))) }.toList(), at)
                applyNew(a) { ok("Moved (action ${a.id}).") }
            }
            "make_group" -> {
                val g = args.optString("name").trim().ifEmpty { return no("A name is needed.") }
                if (g in groupNames()) return no("There's a group '$g' already.")
                val a = act(Wording.MetaChats.madeGroup(name = g), "make_group", JSONObject().put("name", g), at = at)
                applyNew(a) { ok("Made (action ${a.id}).") }
            }
            "rename_group", "delete_group" -> {
                val g = args.optString("group")
                if (g !in groupNames()) return no("No group '$g': app_index groups lists them.")
                val to = if (name == "rename_group") args.optString("name").trim().ifEmpty { return no("A name is needed.") } else ""
                if (to == g) return ok("It's called that already.")
                val keys = JSONObject()
                for ((h, s) in all()) if (groupOf(h, s) == g) keys.put(key(h, s), HomePrefs.chatGroups[key(h, s)] ?: JSONObject.NULL)
                val a = act(if (to.isEmpty()) Wording.MetaChats.deletedGroup(name = g) else Wording.MetaChats.renamedGroup(old = g, new = to), "group", JSONObject()
                    .put("keys", keys).put("from", g).put("to", to).put("from_made", g in HomePrefs.made)
                    .put("to_made", to.isNotEmpty() && to in HomePrefs.made), at = at)
                applyNew(a) { ok("${if (to.isEmpty()) "Deleted" else "Renamed"} (action ${a.id}).") }
            }
            "show_groups" -> {
                val given = args.optJSONArray("groups").strings().map { if (it == "No group") "" else it }
                val unknown = given.filter { it.isNotEmpty() && it !in groupNames() }
                if (unknown.isNotEmpty()) return no("No group ${unknown.joinToString(", ") { "'$it'" }}: app_index groups lists them.")
                val a = act(if (given.isEmpty()) Wording.MetaChats.listedEvery(what = "group")
                    else Wording.MetaChats.listed(names = given.joinToString(", ") { it.ifEmpty { "No group" } }),
                    "show_groups", JSONObject().put("from", JSONArray(HomePrefs.groups.toList())).put("from_none", HomePrefs.noGroups.value)
                        .put("to", JSONArray(given)), at = at)
                applyNew(a) { ok("Shown (action ${a.id}).") }
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
                    val chats = if (went.size == 1) went[0].let { (h, sid) ->
                        Wording.Senders.chat(name = Mesh.session(h, sid)?.let(::title) ?: sid.take(8)) }
                        else Wording.MetaChats.chats(n = went.size)
                    val a = act(if (op == "archive") Wording.MetaChats.archived(chats = chats)
                        else Wording.MetaChats.unarchived(chats = chats), "archive",
                        JSONObject().put("op", op).put("chats", JSONArray(went.map { JSONArray(listOf(it.first, it.second)) })),
                        went.map { (h, sid) -> JSONArray(listOf("chat", "$h/$sid", if (op == "archive") "archived" else "added",
                            if (op == "archive") Wording.MetaChats.archivedNote else Wording.MetaChats.backNote)) }, at)
                    ok("${op.replaceFirstChar(Char::uppercase)}d (action ${a.id})." +
                        if (failed.isNotEmpty()) " Not ${op}d: ${failed.joinToString("; ")}." else "")
                }
            }
            "delete_chats" -> no("These are hosts' sessions, and Meta deletes only this machine's own chats: the phone " +
                "has none. archive_chats puts them out of the way.")
            else -> no("No tool '$name'.")
        }
    }

    private fun show(args: JSONObject, at: MetaCall?, ok: (String) -> Unit, no: (String) -> Unit) {
        val what = args.optString("what").trim()
        val items = args.optJSONArray("items").strings()
        val kind = what.substringBefore(":")
        val rest = what.substringAfter(":", "")
        when (kind) {
            "setting" -> {
                if (rest !in MetaSet.ALL) return no("No setting '$rest': they're ${MetaSet.ALL.keys.joinToString(", ")}.")
                shown(at, JSONObject().put("setting", rest))
            }
            "chats" -> {
                val (found, missing) = chats(items)
                if (missing.isNotEmpty()) return no(notFound(missing))
                shown(at, JSONObject().put("chats", JSONArray(found.map { (h, s) -> JSONArray(listOf(h, s.id.orEmpty())) })))
            }
            "pages" -> {
                val have = Meta.pages().map { it.title }
                val missing = items.filter { i -> have.none { it.equals(i, true) } }
                if (missing.isNotEmpty()) return no("No page ${missing.joinToString(", ") { "'$it'" }}: the chat open has " +
                    have.joinToString(", ") + " (app_index pages).")
                shown(at, JSONObject().put("pages", JSONArray(items.map { i -> have.first { it.equals(i, true) } })))
            }
            "changelog" -> shown(at, JSONObject().put("changelog", true))
            "button" -> {
                if (rest !in BUTTONS) return no("No button '$rest': they're ${BUTTONS.keys.joinToString(", ")} (app_index buttons).")
                val item = items.firstOrNull()
                when (rest) {
                    "trash" -> {
                        val (h, s) = item?.let(::find) ?: return no("There's no button trash without items: app_index buttons.")
                        shown(at, JSONObject().put("button", "trash").put("host", h).put("sid", s.id.orEmpty())
                            .put("label", Wording.Trash.moveTitle(title = title(s))))
                    }
                    "host" -> {
                        val h = item?.let { hostByName(it) } ?: return no("No host '${item.orEmpty()}': they're " +
                            Mesh.reached("start").joinToString(", ") { it.second } + ".")
                        shown(at, JSONObject().put("button", "host").put("host", h)
                            .put("label", Wording.Meta.openItem(name = Mesh.label(h))))
                    }
                    else -> shown(at, JSONObject().put("button", rest).put("label", BUTTONS[rest]!!.substringBefore(":")))
                }
            }
            else -> return no("show takes setting:<name>, chats, pages, changelog or button:<action>.")
        }
        ok("Shown under your reply.")
    }

    const val PARTS = "chats, hosts, groups, settings, forms, buttons, pages, changelog"

    /** A part of the index, a row a line. */
    fun section(name: String): List<String>? = when (name) {
        "hosts" -> (Mesh.hosts.map { it.id } + Mesh.members.keys.map { "mesh:$it" }).map { id ->
            "${Mesh.label(id)}: ${Mesh.conn(id)?.state ?: "not connected"}" + if (id.startsWith("mesh:")) " (through the mesh)" else ""
        }
        "chats", "sessions" -> all().map { (h, s) ->
            "${s.id.orEmpty().take(8)} " + listOfNotNull("\"${chatName(s) ?: "(untitled)"}\"", Mesh.label(h),
                shortPath(h, s.cwd.orEmpty()), s.state.orEmpty(),
                groupOf(h, s).ifEmpty { null }?.let { "group $it" }, if (s.archived == true) "archived" else null,
                s.model?.ifBlank { null }).joinToString(" · ")
        }
        "groups" -> listOf("No group") + groupNames()
        "settings" -> MetaSet.ALL.values.map { st ->
            "${st.name} = ${saidValue(st.get())} (${st.takes()}) · ${st.title()}" + (st.note?.let { " · $it" } ?: "")
        }
        "changelog" -> AppVersion.changes(Mesh.app).take(60).map { "${it.short} ${madeWhen(it.made)} · ${it.subject}" }
        "forms" -> FORMS.map { (k, f) -> "$k: ${f.about}" }
        "buttons" -> BUTTONS.map { (k, v) -> "$k: $v" }
        "pages" -> Meta.pages().map { it.title }.ifEmpty { listOf("(no chat open)") }
        else -> null
    }
}
