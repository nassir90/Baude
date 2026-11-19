package dev.baude.android

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.nio.file.FileSystems
import java.util.Base64
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** The tools an agent on this phone has: files in the agent's folder (Read, Write, Edit, Glob, named and shaped as Claude Code's so heads show them as they show
 * claude's); the user, with a question (AskUserQuestion, answered as claude's is); and the mesh, through this phone's
 * own connections to it (mcp__mesh__*, as baudelib.meshtools names a host's): every host this phone reaches, their
 * chats, a chat read or sent a message. Each runs as the chat's permission mode has it (need). */
object PhoneTools {
    const val RESULT_MAX = 40_000 // (characters of a tool's result given back to the model)
    private val GONE = "This agent's been deleted" to true
    private const val READ_LINES = 2000
    private const val READ_MAX = 256 * 1024
    private const val WAIT_S = 300
    private const val WAIT_MAX_S = 1800

    private fun prop(type: String, about: String) = JSONObject().put("type", type).put("description", about)

    private val TOOLS: List<Triple<String, String, JSONObject>> = listOf(
        Triple("Read", "Read a text file in your folder: its lines, numbered (offset and limit for part of a long one).",
            schema(listOf("file_path"), "file_path" to prop("string", "The file's path, relative to your folder"),
                "offset" to prop("integer", "The line to start at (1 is the first)"),
                "limit" to prop("integer", "How many lines"))),
        Triple("Write", "Write a file in your folder, made or replaced whole.",
            schema(listOf("file_path", "content"), "file_path" to prop("string", "The file's path, relative to your folder"),
                "content" to prop("string", "All of what it holds"))),
        Triple("Edit", "Change a file in your folder: old_string replaced by new_string (it must be there once, unless " +
            "replace_all).",
            schema(listOf("file_path", "old_string", "new_string"),
                "file_path" to prop("string", "The file's path, relative to your folder"),
                "old_string" to prop("string", "The text to replace, exactly as it is"),
                "new_string" to prop("string", "What it becomes"),
                "replace_all" to prop("boolean", "Every one of them (default false)"))),
        Triple("Glob", "The files in your folder whose paths match a pattern (** for any folders, * for any name).",
            schema(emptyList(), "pattern" to prop("string", "A glob, as **/*.md (default: every file)"))),
        Triple("AskUserQuestion", "Ask the user a question or two, each with options to pick from (they can write " +
            "their own answer too).",
            schema(listOf("questions"), "questions" to JSONObject().put("type", "array").put("items", JSONObject()
                .put("type", "object").put("required", JSONArray(listOf("question", "options"))).put("properties", JSONObject()
                    .put("question", prop("string", "The question, in full"))
                    .put("header", prop("string", "A word or two it's about"))
                    .put("multiSelect", prop("boolean", "More than one may be picked"))
                    .put("options", JSONObject().put("type", "array").put("items", JSONObject().put("type", "object")
                        .put("required", JSONArray(listOf("label"))).put("properties", JSONObject()
                            .put("label", prop("string", "What's picked")).put("description", prop("string", "What it means"))))))))),
        Triple("mcp__mesh__hosts", "The mesh's hosts this phone reaches (this phone first): each one's id, name, " +
            "whether it's connected now, and the models it runs.", schema(emptyList())),
        Triple("mcp__mesh__chats", "The chats on a host (this phone if none's given), newest first: each one's id, " +
            "title, state, and its Bot's name if it's a Bot.",
            schema(emptyList(), "host" to prop("string", "A host's id or name (mcp__mesh__hosts)"),
                "bots" to prop("boolean", "Only Bots"))),
        Triple("mcp__mesh__read_chat", "What a chat on a host said last: its last messages, yours and its.",
            schema(listOf("session"), "session" to prop("string", "The chat's id (mcp__mesh__chats)"),
                "host" to prop("string", "Its host's id or name; looked for on every host if none"),
                "last" to prop("integer", "How many messages (default 6)"))),
        Triple("mcp__mesh__message_chat", "Send a message to a chat or Bot on a host, as its user would, and unless " +
            "wait is false wait for its answer. Only when the user asked for this.",
            schema(listOf("session", "text"), "session" to prop("string", "The chat's id"),
                "text" to prop("string", "What to say, in full: it doesn't see this chat"),
                "host" to prop("string", "Its host's id or name; looked for if none"),
                "wait" to prop("boolean", "Wait for its answer (default true)"),
                "timeout_s" to prop("integer", "How long to wait (default $WAIT_S)"))),
    )

    /** Plan mode's way out, as claude's: the plan put to the user, who says whether to go on with it. */
    private val EXIT_PLAN = Triple("ExitPlanMode", "In plan mode: put your plan to the user. If they agree, you may " +
        "carry it out (your file tools work again).", schema(listOf("plan"), "plan" to prop("string", "The plan, in Markdown")))

    /** The permission modes, as claude's (baudelib.claude.MODES): Manual, Accept edits, Plan, Auto, Don't ask, Bypass. */
    val MODES = listOf("default" to "Manual", "acceptEdits" to "Accept edits", "plan" to "Plan", "auto" to "Auto",
        "dontAsk" to "Don't ask", "bypassPermissions" to "Bypass permissions")
    private val READS = setOf("Read", "Glob")
    private val EDITS = setOf("Write", "Edit")
    private val MESH_LOOKS = setOf("mcp__mesh__hosts", "mcp__mesh__chats", "mcp__mesh__read_chat") // (meshtools.READ_ONLY)

    /** What a tool call needs, in a chat's permission mode, as a Baude chat's claude has it: "allow" (run, unasked),
     * "ask" (the user's asked), or why it's refused. Reads and the mesh's looking never asked; a Bot's mesh tools
     * never asked (as a host's Bot's); edits asked in Manual, run in Accept edits, Auto and Bypass, refused in Plan and
     * Don't ask; another action (a message to a chat) asked in Manual and Accept edits; a question to you, or a plan
     * put to you, always asked. */
    fun need(mode: String, tool: String, bot: Boolean): String = when {
        tool == "AskUserQuestion" || tool == EXIT_PLAN.first -> "ask"
        tool in READS || tool in MESH_LOOKS -> "allow"
        tool.startsWith("mcp__mesh__") && bot -> "allow"
        mode == "bypassPermissions" -> "allow"
        mode == "plan" -> "It's plan mode: nothing's changed till the user agrees to your plan (ExitPlanMode)"
        mode == "dontAsk" -> "It's Don't ask mode: only what's always allowed in this chat runs"
        tool in EDITS && mode in setOf("acceptEdits", "auto") -> "allow"
        mode == "auto" -> "allow"
        else -> "ask"
    }

    private fun schema(required: List<String>, vararg props: Pair<String, JSONObject>) = JSONObject().put("type", "object")
        .put("properties", JSONObject().apply { for ((k, v) in props) put(k, v) }).put("required", JSONArray(required))

    fun names(mode: String = ""): List<String> = tools(mode).map { it.first }

    private fun tools(mode: String) = if (mode == "plan") TOOLS + EXIT_PLAN else TOOLS

    /** The tools, as OpenAI's API is given them (ExitPlanMode too, in plan mode). */
    fun schemas(mode: String): JSONArray = JSONArray(tools(mode).map { (name, about, params) ->
        JSONObject().put("type", "function").put("function", JSONObject().put("name", name).put("description", about)
            .put("parameters", params))
    })

    /** A tool run: (what it gives back, whether that's an error). */
    fun run(s: PhoneSession, name: String, input: JSONObject): Pair<String, Boolean> = when (name) {
        "Read" -> read(s, input)
        "Write" -> write(s, input)
        "Edit" -> edit(s, input)
        "Glob" -> glob(s, input)
        "AskUserQuestion" -> asked(input)
        "ExitPlanMode" -> PhoneHost.setMode(s, "default").let { "The user agreed to your plan: carry it out." to false }
        "mcp__mesh__hosts" -> hosts()
        "mcp__mesh__chats" -> chats(input)
        "mcp__mesh__read_chat" -> readChat(input)
        "mcp__mesh__message_chat" -> message(input)
        else -> "No such tool: $name" to true
    }

    // ---- its folder

    /** A path in the agent's folder (relative to it, or absolute and in it), or null for one outside it. */
    private fun inside(s: PhoneSession, path: String): File? {
        if (path.isBlank()) return null
        val root = s.folder.canonicalFile
        val f = (if (path.startsWith("/")) File(path) else File(root, path)).canonicalFile
        return f.takeIf { it == root || it.path.startsWith(root.path + File.separator) }
    }

    private fun outside(path: String) = "$path isn't in your folder: your file tools reach only it" to true

    private fun rel(s: PhoneSession, f: File) = f.canonicalFile.relativeTo(s.folder.canonicalFile).path

    private fun read(s: PhoneSession, input: JSONObject): Pair<String, Boolean> {
        val path = input.optString("file_path")
        val f = inside(s, path) ?: return outside(path)
        if (!f.isFile) return "There's no file $path in your folder" to true
        if (f.length() > 8L * READ_MAX && !input.has("offset")) return "${rel(s, f)} is ${f.length()} bytes: read it in parts (offset, limit)" to true
        val lines = f.readLines()
        val from = (input.optInt("offset", 1)).coerceAtLeast(1)
        val count = input.optInt("limit", READ_LINES).coerceIn(1, READ_LINES)
        val out = StringBuilder()
        for (i in from - 1 until minOf(lines.size, from - 1 + count)) {
            out.append((i + 1).toString().padStart(6)).append('\t').append(lines[i].take(2000)).append('\n')
            if (out.length > READ_MAX) break
        }
        if (lines.isEmpty()) return "(${rel(s, f)} is empty)" to false
        return out.toString() to false
    }

    private fun write(s: PhoneSession, input: JSONObject): Pair<String, Boolean> {
        val path = input.optString("file_path")
        val f = inside(s, path) ?: return outside(path)
        if (f.isDirectory) return "$path is a folder" to true
        val text = input.optString("content")
        if (!PhoneHost.live(s) {
                f.parentFile?.mkdirs()
                f.writeText(text)
            }) return GONE
        return "Wrote ${rel(s, f)} (${text.toByteArray().size} bytes)" to false
    }

    private fun edit(s: PhoneSession, input: JSONObject): Pair<String, Boolean> {
        val path = input.optString("file_path")
        val f = inside(s, path) ?: return outside(path)
        if (!f.isFile) return "There's no file $path in your folder" to true
        val old = input.optString("old_string")
        val new = input.optString("new_string")
        if (old.isEmpty()) return "old_string is empty: use Write for a new file" to true
        val text = f.readText()
        var n = 0
        var at = text.indexOf(old)
        while (at >= 0) {
            n++
            at = text.indexOf(old, at + old.length)
        }
        if (n == 0) return "old_string isn't in ${rel(s, f)}" to true
        val all = input.optBoolean("replace_all")
        if (n > 1 && !all) return "old_string is in ${rel(s, f)} $n times: give more of it, or replace_all" to true
        if (!PhoneHost.live(s) { f.writeText(if (all) text.replace(old, new) else text.replaceFirst(old, new)) }) return GONE
        return "Edited ${rel(s, f)}" + (if (all) " ($n places)" else "") to false
    }

    private fun glob(s: PhoneSession, input: JSONObject): Pair<String, Boolean> {
        val pattern = input.optString("pattern").ifBlank { "**" }
        val fs = FileSystems.getDefault()
        val matchers = listOfNotNull(fs.getPathMatcher("glob:$pattern"), // (**/x matching x at the top too, as claude's)
            if (pattern.startsWith("**/")) fs.getPathMatcher("glob:" + pattern.removePrefix("**/")) else null)
        val root = s.folder.canonicalFile
        val found = root.walkTopDown().filter { it.isFile }.map { it.relativeTo(root).path }
            .filter { p -> pattern == "**" || matchers.any { it.matches(java.nio.file.Paths.get(p)) } }.sorted().take(500).toList()
        return (if (found.isEmpty()) "No files match $pattern" else found.joinToString("\n")) to false
    }

    // ---- you

    /** Its questions' answers, as you gave them (they come in the ask's answer: updatedInput's answers). */
    private fun asked(input: JSONObject): Pair<String, Boolean> {
        val answers = input.optJSONObject("answers") ?: return "The user didn't answer" to true
        val said = answers.keys().asSequence().joinToString("\n") { q -> "\"$q\" = \"${answers.optString(q)}\"" }
        return "The user answered: $said" to false
    }

    // ---- the mesh

    private data class Reached(val id: String, val name: String, val conn: Connection)

    /** The machines this phone reaches now: itself, its hosts, and their mesh's members (their connections). */
    private fun reached(): List<Reached> = Mesh.states.values.toList().mapNotNull { c ->
        val info = c.info ?: return@mapNotNull null
        Reached(info.optString("id"), info.optString("name").ifBlank { Mesh.label(c.id) }, c)
    }.sortedBy { if (it.conn.place is Place.Phone) 0 else 1 }

    private fun find(host: String?): Reached? = if (host.isNullOrBlank()) reached().firstOrNull { it.conn.place is Place.Phone }
    else reached().firstOrNull { it.id == host } ?: reached().firstOrNull { it.name.equals(host, ignoreCase = true) }

    private fun hosts(): Pair<String, Boolean> {
        val out = JSONArray()
        for (p in reached()) {
            val models = p.conn.info?.optJSONArray("models").list().map { it.optString("value") }
            out.put(JSONObject().put("id", p.id).put("name", p.name).put("connected", p.conn.state == "up")
                .put("this_phone", p.conn.place is Place.Phone).put("models", JSONArray(models.take(40))))
        }
        return out.toString(1) to false
    }

    private fun sessionsOf(p: Reached): List<JSONObject> = Mesh.sessions[p.conn.id].orEmpty()

    private fun chats(input: JSONObject): Pair<String, Boolean> {
        val p = find(input.optString("host")) ?: return "No host ${input.optString("host")} in reach" to true
        val bots = input.optBoolean("bots")
        val out = JSONArray()
        for (x in sessionsOf(p).filter { !bots || it.optJSONObject("bot") != null }.sortedByDescending { it.optDouble("said") }.take(50)) {
            out.put(JSONObject().put("id", x.optString("id")).put("title", x.opt("title")).put("state", x.optString("state"))
                .put("bot", x.optJSONObject("bot")?.optString("name") ?: JSONObject.NULL))
        }
        return out.toString(1) to false
    }

    private fun owner(sid: String, host: String?): Reached? = if (!host.isNullOrBlank()) find(host)
    else reached().firstOrNull { p -> sessionsOf(p).any { it.optString("id") == sid } }

    /** A request to a host, waited for (at most seconds). */
    private fun ask(c: Connection, msg: JSONObject, seconds: Long = 30): JSONObject {
        val done = CountDownLatch(1)
        var got: JSONObject? = null
        c.request(msg) { got = it; done.countDown() }
        if (!done.await(seconds, TimeUnit.SECONDS)) return JSONObject().put("op", "error").put("error", "no answer")
        return got ?: JSONObject().put("op", "error").put("error", "no answer")
    }

    private fun tail(p: Reached, sid: String, last: Int): List<Line> {
        val size = sessionsOf(p).firstOrNull { it.optString("id") == sid }?.optLong("size", 0) ?: 0
        val from = (size - 200_000).coerceAtLeast(0)
        val r = ask(p.conn, JSONObject().put("op", "transcript").put("session", sid).put("offset", from))
        val text = String(Base64.getDecoder().decode(r.optString("data", "")), Charsets.UTF_8)
        return parse(text, cutFirst = from > 0).filter { it.kind == "you" || it.kind == "bot" }.takeLast(last)
    }

    private fun readChat(input: JSONObject): Pair<String, Boolean> {
        val sid = input.optString("session")
        val p = owner(sid, input.optString("host")) ?: return "No chat $sid in reach" to true
        val lines = tail(p, sid, input.optInt("last", 6).coerceIn(1, 50))
        if (lines.isEmpty()) return "Nothing's been said in it yet" to false
        return lines.joinToString("\n\n") { (if (it.kind == "you") "User: " else "It: ") + it.text }.take(RESULT_MAX) to false
    }

    private fun message(input: JSONObject): Pair<String, Boolean> {
        val sid = input.optString("session")
        val p = owner(sid, input.optString("host")) ?: return "No chat $sid in reach" to true
        val text = input.optString("text").ifBlank { return "Nothing to say" to true }
        val before = sessionsOf(p).firstOrNull { it.optString("id") == sid }?.optLong("size", 0) ?: 0
        val msg = JSONObject().put("type", "user").put("message", JSONObject().put("role", "user")
            .put("content", JSONArray().put(JSONObject().put("type", "text").put("text", text))))
        val r = ask(p.conn, JSONObject().put("op", "input").put("session", sid).put("msg", msg))
        if (r.optString("op") == "error") return "${p.name} didn't take it: ${r.optString("error")}" to true
        if (!input.optBoolean("wait", true)) return "Sent to ${p.name}" to false
        val until = System.currentTimeMillis() + 1000L * input.optInt("timeout_s", WAIT_S).coerceIn(5, WAIT_MAX_S)
        var began = false
        while (System.currentTimeMillis() < until) {
            Thread.sleep(1000)
            val now = sessionsOf(p).firstOrNull { it.optString("id") == sid } ?: continue
            val state = now.optString("state")
            if (state == "working" || state == "waiting") began = true
            if ((began || now.optLong("size", 0) > before) && state != "working" && state != "waiting") {
                val last = tail(p, sid, 1).lastOrNull { it.kind == "bot" }?.text
                return (last ?: "It answered nothing") .take(RESULT_MAX) to false
            }
        }
        return "Sent; no answer yet after the wait (mcp__mesh__read_chat later)" to false
    }
}
