package dev.baude.android

import android.content.Context
import android.os.Handler
import android.os.Looper
import androidx.compose.runtime.mutableStateOf
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.RandomAccessFile
import java.util.Base64
import java.util.UUID
import java.util.concurrent.CompletableFuture
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/** Where what this phone's host says goes: this app's own head, or a tunnel from the mesh. */
abstract class Head {
    @Volatile var subscribed = false
    @Volatile var archived = false
    abstract fun send(msg: JSONObject)
}

/** One of this phone's agents: what's kept of it (meta), and what's going on in it now. */
class PhoneSession(val meta: JSONObject) {
    val id: String get() = meta.getString("id")
    val viewers = HashSet<Head>()  // (attached: told what it asks)
    val live = HashSet<Head>()     // (attached live: told everything it says, as it says it)
    val asks = LinkedHashMap<String, JSONObject>() // (a permission it asks, not answered yet: its control_request)
    val waits = HashMap<String, CompletableFuture<JSONObject?>>() // (each ask's answer, waited for at its turn)
    val alwaysAllow = HashSet<String>()
    var convo: JSONArray? = null // (its conversation as the model's API has it, read once, then added to as it goes)
    @Volatile var busy = false
    @Volatile var acting = false
    @Volatile var cancel = false
    @Volatile var deleted = false
    @Volatile var stopModel: (() -> Unit)? = null // (the model call going on, let go of: its host stops generating)
    val folder: File get() = File(meta.getString("cwd"))
    val queued: JSONArray get() = meta.optJSONArray("queued") ?: JSONArray().also { meta.put("queued", it) }
}

/** This phone as a host of the mesh: agents, each a Bot (a name, and a memory in its folder), whose loop and tools run
 * here (PhoneAgent, PhoneTools) and whose model calls go to a member of the mesh that has the model (the API proxy:
 * its host process's infer). Heads talk to it in the wire's own lines (wire.py), as to any host process: this app's
 * own (connection, in the app), and the mesh's, through the phone's link (PhoneLink), so Baude and the other members
 * list its agents and read them as any host's. Its hello names the ops it takes (ops), and claims no version. Kept in
 * the app's own folder: phone/sessions (each one's meta), phone/transcripts (each one's, as Claude Code writes one,
 * which is what the heads read) and phone/agents (each one's folder, the only files its tools reach). */
object PhoneHost {
    /** The ops it takes (hello's ops: wire.speaks). */
    val OPS = listOf("hello", "ping", "subscribe", "start", "attach", "detach", "input", "stop", "transcript",
        "subagents", "rename", "archive", "unarchive", "delete", "always_allow", "bot", "bot_picture", "memory")
    lateinit var ctx: Context
    @Volatile var id = ""
    @Volatile var name = "Phone"
    private val lock = Any()
    private val sessions = LinkedHashMap<String, PhoneSession>()
    private val heads = HashSet<Head>()
    private val work = Executors.newSingleThreadScheduledExecutor { r -> Thread(r, "phone-host").apply { isDaemon = true } }
    private val main = Handler(Looper.getMainLooper())
    private var dirty = false
    private var started = false
    private lateinit var root: File

    fun start(c: Context) {
        synchronized(lock) {
            if (started) return
            started = true
            ctx = c.applicationContext
            val prefs = ctx.getSharedPreferences("phone", Context.MODE_PRIVATE)
            id = prefs.getString("id", null) ?: UUID.randomUUID().toString().also { prefs.edit().putString("id", it).apply() }
            name = deviceName()
            root = File(ctx.filesDir, "phone")
            for (d in listOf("sessions", "transcripts", "agents")) File(root, d).mkdirs()
            for (f in File(root, "sessions").listFiles().orEmpty().filter { it.name.endsWith(".json") }) {
                try {
                    val meta = JSONObject(f.readText())
                    if (!isId(meta.optString("id"))) continue
                    val s = PhoneSession(meta)
                    sessions[s.id] = s
                    recover(s)
                } catch (_: Exception) {
                }
            }
        }
    }

    /** (lock held) A turn the app stopped at (it was closed, or killed): marked cut, as a host marks one, with a result
     * kept for each tool call it made that had none, and what was sent meanwhile kept as said. */
    private fun recover(s: PhoneSession) {
        if (!s.meta.optBoolean("turn")) return
        val open = LinkedHashSet<String>()
        transcript(s).takeIf { it.exists() }?.forEachLine { row ->
            val o = try {
                JSONObject(row)
            } catch (_: Exception) {
                return@forEachLine
            }
            for (b in o.optJSONObject("message")?.optJSONArray("content").list()) when (b.optString("type")) {
                "tool_use" -> open.add(b.optString("id"))
                "tool_result" -> open.remove(b.optString("tool_use_id"))
            }
        }
        if (open.isNotEmpty()) record(s, "user", JSONObject().put("role", "user").put("content", JSONArray(open.map {
            JSONObject().put("type", "tool_result").put("tool_use_id", it).put("content", "It didn't run: the app stopped first.")
                .put("is_error", true)
        })), null)
        record(s, "user", textMessage(PhoneAgent.STOPPED), null)
        for (m in s.queued.list()) record(s, "user", m, null)
        s.meta.remove("queued")
        s.meta.put("turn", false).put("cut", now())
        save(s)
    }

    /** What the phone's called: its name in Android's settings, else its model's. */
    private fun deviceName(): String {
        val set = try {
            android.provider.Settings.Global.getString(ctx.contentResolver, "device_name")
        } catch (_: Exception) {
            null
        }
        return (set ?: android.os.Build.MODEL ?: "Phone").trim().take(64).ifBlank { "Phone" }
    }

    /** This app's own head on it, as a Connection (the rest of the app talks to it as to any host's). */
    fun connection(c: Context, heard: (Connection, JSONObject) -> Unit, changed: (Connection) -> Unit): Connection {
        start(c)
        return Local(c, heard, changed)
    }

    private class Local(ctx: Context, private val hear: (Connection, JSONObject) -> Unit,
                        private val told: (Connection) -> Unit) :
        Connection(ctx = ctx, place = Place.Phone, identify = { _, _ -> }, route = { null }, heard = hear, changed = told) {
        private val waiting = ConcurrentHashMap<Int, (JSONObject) -> Unit>()
        private val next = AtomicInteger(1)
        private val head = object : Head() {
            override fun send(msg: JSONObject) {
                val req = msg.optInt("req", -1)
                if (msg.optString("op") == "sessions") msg.optJSONObject("server")?.let { info = it }
                val call = if (req < 0) null else waiting.remove(req)
                if (call != null) call(msg) else hear(this@Local, msg)
            }
        }

        override fun start() {
            info = PhoneHost.info()
            state = "up"
            told(this)
            send(JSONObject().put("op", "subscribe").put("archived", true))
        }

        override fun stop() {
            take(head, null)
        }

        override fun send(msg: JSONObject): Boolean {
            take(head, msg)
            return true
        }

        override fun request(msg: JSONObject, then: (JSONObject) -> Unit) {
            val req = next.getAndIncrement()
            waiting[req] = then
            send(msg.put("req", req))
        }
    }

    /** A head's line, handled in turn on the host's own thread (msg null: the head's gone). */
    fun take(head: Head, msg: JSONObject?) {
        work.execute {
            try {
                if (msg == null) gone(head) else handle(head, msg)
            } catch (e: Exception) {
                msg?.let { head.send(JSONObject().put("op", "error").put("req", it.opt("req")).put("error", "${e.javaClass.simpleName}: ${e.message}")) }
            }
        }
    }

    private fun gone(head: Head) {
        synchronized(lock) {
            heads.remove(head)
            for (s in sessions.values) {
                s.viewers.remove(head)
                s.live.remove(head)
            }
        }
    }

    private const val ASK_TOOL = "AskUserQuestion" // (never allowed without asking: its ask is the question)

    private fun handle(head: Head, msg: JSONObject) {
        val op = msg.optString("op")
        val req = msg.opt("req")
        fun reply(kind: String, vararg kv: Pair<String, Any?>) {
            val o = JSONObject().put("op", kind)
            if (req != null) o.put("req", req)
            for ((k, v) in kv) o.put(k, v ?: JSONObject.NULL)
            head.send(o)
        }
        synchronized(lock) { heads.add(head) }
        when (op) {
            "ping" -> return
            "hello" -> return reply("hello", "server" to info())
            "subscribe" -> {
                head.subscribed = true
                head.archived = msg.optBoolean("archived")
                return reply("sessions", "sessions" to listing(head.archived), "server" to info())
            }
        }
        if (op !in OPS) return reply("error", "error" to "unknown op '$op' (${name} is a phone: it takes ${OPS.joinToString()})")
        if (op == "start") return start(head, msg, ::reply)
        val s = synchronized(lock) { sessions[msg.optString("session")] }
            ?: return reply("error", "error" to "no session ${msg.optString("session")}")
        when (op) {
            "attach" -> synchronized(lock) {
                s.viewers.add(head)
                if (msg.optBoolean("live")) s.live.add(head)
                msg.optJSONArray("always_allow")?.let { a ->
                    for (i in 0 until a.length()) a.optString(i).takeIf { it != ASK_TOOL }?.let { s.alwaysAllow.add(it) }
                }
                reply("attached", "session" to listing(s), "asks" to JSONArray(s.asks.values.toList()))
            }
            "detach" -> synchronized(lock) {
                s.viewers.remove(head)
                s.live.remove(head)
            }
            "input" -> {
                input(head, s, msg.optJSONObject("msg") ?: JSONObject())
                if (req != null) reply("accepted", "session" to s.id)
            }
            "stop" -> {
                stopTurn(s)
                reply("stopped", "session" to s.id)
            }
            "transcript" -> reply("transcript", "session" to s.id, *lines(transcript(s), msg.optLong("offset", 0),
                msg.optInt("limit", 0)))
            "subagents" -> reply("subagents", "session" to s.id, "files" to JSONArray(), "more" to false)
            "rename" -> {
                live(s) {
                    s.meta.put("title", (if (msg.isNull("title")) "" else msg.optString("title")).trim().take(200).ifBlank { null } ?: JSONObject.NULL)
                    save(s)
                }
                changed()
                reply("renamed", "session" to listing(s))
            }
            "archive", "unarchive" -> {
                if (op == "archive") stopTurn(s)
                live(s) {
                    s.meta.put("archived", op == "archive")
                    save(s)
                }
                changed()
                reply(op + "d", "session" to s.id)
            }
            "delete" -> {
                stopTurn(s)
                val watching = synchronized(lock) { // (under the lock: nothing it says after is kept, or made again)
                    s.deleted = true
                    sessions.remove(s.id)
                    File(root, "sessions/${s.id}.json").delete()
                    transcript(s).delete()
                    s.folder.deleteRecursively()
                    (s.viewers + s.live).toSet()
                }
                for (h in watching) h.send(JSONObject().put("op", "deleted").put("session", s.id))
                changed()
                reply("deleted", "session" to s.id, "trashed" to JSONArray())
            }
            "always_allow" -> {
                synchronized(lock) {
                    fun given(a: JSONArray) = (0 until a.length()).map { a.optString(it) }.filter { it.isNotBlank() && it != ASK_TOOL }
                    msg.optJSONArray("add")?.let { s.alwaysAllow.addAll(given(it)) }
                        ?: msg.optJSONArray("tools")?.let {
                            s.alwaysAllow.clear()
                            s.alwaysAllow.addAll(given(it))
                        }
                }
                changed()
                reply("allowed", "session" to s.id, "tools" to JSONArray(s.alwaysAllow.sorted()))
            }
            "bot" -> {
                live(s) {
                    val bot = msg.optJSONObject("bot")
                    if (bot == null) s.meta.put("bot", JSONObject.NULL)
                    else s.meta.put("bot", JSONObject().put("name", bot.optString("name").trim().take(64).ifBlank { "Agent" })
                        .put("since", s.meta.optJSONObject("bot")?.optDouble("since") ?: now()))
                    save(s)
                }
                changed()
                reply("bot_set", "session" to listing(s))
            }
            "bot_picture" -> reply("bot_picture", "session" to s.id, "picture" to null)
            "memory" -> {
                val file = File(s.folder, MEMORY)
                if (msg.has("text")) live(s) { file.writeText(msg.optString("text")) }
                reply("memory", "session" to s.id, "text" to (if (file.exists()) file.readText() else ""),
                    "path" to MEMORY, "mtime" to if (file.exists()) file.lastModified() / 1000.0 else null)
            }
        }
    }

    /** A new agent: its folder made, a Bot if it's named, its prompt sent. Its model's one the mesh serves this phone,
     * (Inference's value; none given, the first of them, as a host runs its own default). A fork, a side question or a conversation adopted: not here. */
    private fun start(head: Head, msg: JSONObject, reply: (String, Array<out Pair<String, Any?>>) -> Unit) {
        val refused = listOf("fork_from", "ephemeral", "adopt").firstOrNull { msg.has(it) && !msg.isNull(it) && msg.opt(it) != false }
        if (refused != null) return reply("error", arrayOf("error" to "an agent on $name can't be started so ($refused)", "plain" to true))
        fun str(k: String) = if (msg.isNull(k)) "" else msg.optString(k) // (JSON's null: none given, not "null")
        val served = (if (str("model").isBlank()) Inference.found().firstOrNull() else Inference.of(str("model")))
            ?: return reply("error", arrayOf("error" to "$name can't run ${str("model").ifBlank { "a model" }}: its agents " +
                "run the models the mesh's machines serve over an API, and none does now", "plain" to true))
        val sid = UUID.randomUUID().toString()
        val folder = File(root, "agents/$sid").apply { mkdirs() }
        val botName = msg.optJSONObject("bot")?.optString("name")?.trim()?.take(64)?.ifBlank { null }
        val prompt = str("prompt")
        val meta = JSONObject().put("id", sid).put("cwd", folder.path).put("model", served.value)
            .put("infer", served.json()).put("title", str("title").trim().ifBlank { null } ?: JSONObject.NULL)
            .put("created", now()).put("said", now()).put("prompts", 0).put("archived", false)
            .put("draft", msg.optBoolean("draft") && prompt.isBlank())
            .put("mode", str("mode").takeIf { m -> PhoneTools.MODES.any { it.first == m } } ?: "default")
            .put("bot", if (botName != null) JSONObject().put("name", botName).put("since", now()) else JSONObject.NULL)
            .put("start_key", str("start_key").takeIf(::isId) ?: JSONObject.NULL)
        val s = PhoneSession(meta)
        if (botName != null) File(folder, MEMORY).writeText("")
        synchronized(lock) {
            sessions[sid] = s
            save(s)
            if (msg.optBoolean("attach")) {
                s.viewers.add(head)
                s.live.add(head)
            }
        }
        reply("started", arrayOf("session" to listing(s), "forked" to false))
        if (prompt.isNotBlank()) input(head, s, JSONObject().put("type", "user").put("message", textMessage(prompt)))
        changed()
    }

    private fun textMessage(text: String) = JSONObject().put("role", "user")
        .put("content", JSONArray().put(JSONObject().put("type", "text").put("text", text)))

    /** What a head sends an agent: a message of yours (a turn, or taken after the one going on), an answer to what it
     * asked, or one of claude's control requests (initialize: what it runs; interrupt: its turn stopped; the rest, as
     * a host answers them for an agent that isn't running: nothing to change). */
    private fun input(head: Head, s: PhoneSession, msg: JSONObject) {
        when (msg.optString("type")) {
            "user" -> {
                val message = msg.optJSONObject("message") ?: return
                synchronized(lock) {
                    if (s.deleted) return
                    if (s.busy) {
                        s.queued.put(message)
                        save(s)
                        return
                    }
                    begin(s, listOf(message), msg.optString("uuid").ifBlank { null })
                }
            }
            "control_response" -> {
                val r = msg.optJSONObject("response") ?: return
                val rid = r.optString("request_id")
                synchronized(lock) {
                    s.asks.remove(rid)
                    s.waits.remove(rid)
                }?.complete(if (r.optString("subtype") == "success") r.optJSONObject("response") ?: JSONObject()
                    else JSONObject().put("behavior", "deny").put("message", r.optString("error", "refused")))
                changed()
            }
            "control_request" -> {
                val rid = msg.optString("request_id")
                val request = msg.optJSONObject("request") ?: JSONObject()
                fun answer(response: JSONObject?, error: String? = null) = head.send(JSONObject().put("op", "claude")
                    .put("session", s.id).put("msg", JSONObject().put("type", "control_response").put("response",
                        if (error != null) JSONObject().put("subtype", "error").put("request_id", rid).put("error", error)
                        else JSONObject().put("subtype", "success").put("request_id", rid).put("response", response))))
                when (request.optString("subtype")) {
                    "initialize" -> answer(JSONObject().put("commands", JSONArray()).put("agents", JSONArray())
                        .put("models", JSONArray().put(JSONObject().put("value", s.meta.optString("model"))
                            .put("displayName", Inference.display(s))))
                        .put("current_permission_mode", mode(s)).put("output_style", "default")
                        .put("available_output_styles", JSONArray().put("default")))
                    "interrupt" -> {
                        val running = s.busy
                        stopTurn(s)
                        answer(JSONObject().put("running", running))
                    }
                    "set_model" -> if (request.optString("model").let { it.isEmpty() || it == s.meta.optString("model") }) answer(JSONObject())
                        else answer(null, "an agent on this phone keeps the model it was made with: make another for that one")
                    "get_settings" -> answer(JSONObject().put("applied", JSONObject().put("model", s.meta.optString("model"))))
                    "set_permission_mode" -> if (PhoneTools.MODES.any { it.first == request.optString("mode") }) {
                        setMode(s, request.optString("mode"))
                        answer(JSONObject())
                    } else answer(null, "no such permission mode: ${request.optString("mode")}")
                    "rewind_conversation" -> answer(null, "an agent on this phone can't be taken back to an earlier message yet")
                    else -> answer(JSONObject())
                }
            }
        }
    }

    /** (lock held) A turn begun: the messages kept in its transcript, and its agent run on a thread of its own. */
    private fun begin(s: PhoneSession, messages: List<JSONObject>, uuid: String?) {
        for ((i, m) in messages.withIndex()) {
            record(s, "user", m, if (i == 0) uuid else null)
            s.meta.put("prompts", s.meta.optInt("prompts") + 1)
        }
        if (s.meta.isNull("title")) firstWords(messages.first())?.let { s.meta.put("title", it) }
        s.meta.put("said", now()).put("turn", true).put("draft", false).remove("cut")
        save(s)
        s.busy = true
        s.cancel = false
        changed()
        kotlin.concurrent.thread(name = "agent-${s.id.take(8)}", isDaemon = true) { PhoneAgent.turn(s) }
    }

    private fun firstWords(message: JSONObject): String? {
        val content = message.opt("content")
        val text = if (content is String) content else (content as? JSONArray)?.let { a ->
            (0 until a.length()).mapNotNull { a.optJSONObject(it) }.firstOrNull { it.optString("type") == "text" }?.optString("text")
        }
        return text?.split(Regex("\\s+"))?.joinToString(" ")?.trim()?.take(90)?.ifBlank { null }
    }

    private fun stopTurn(s: PhoneSession) {
        val waiting = synchronized(lock) {
            if (!s.busy) return
            s.cancel = true
            s.meta.remove("queued")
            save(s)
            s.asks.clear()
            s.waits.values.toList().also { s.waits.clear() }
        }
        waiting.forEach { it.complete(null) }
        s.stopModel?.invoke()
        changed()
    }

    // ---- what an agent's turn does here (PhoneAgent's)

    /** block, under the host's lock, unless the agent's been deleted (then nothing, and false): a tool's write, a
     * record, so none of it's made again after a delete. */
    fun live(s: PhoneSession, block: () -> Unit): Boolean = synchronized(lock) {
        if (s.deleted) false else {
            block()
            true
        }
    }

    /** Its conversation as the model's API has it, for its next call: what's in its transcript, read the first time,
     * then kept as it's added to. */
    fun conversation(s: PhoneSession): JSONArray = synchronized(lock) {
        val c = s.convo ?: PhoneAgent.read(transcript(s)).also { s.convo = it }
        JSONArray(c.toString())
    }

    /** A message of the agent's (assistant) or of its tools' answers (user), kept and said to those attached live.
     * apiError: what it says is why its model couldn't answer (marked so, as claude marks one: shown as an error). */
    fun said(s: PhoneSession, kind: String, message: JSONObject, apiError: Boolean = false) {
        synchronized(lock) {
            val uuid = record(s, kind, message, null, apiError) ?: return
            if (kind == "assistant") {
                val content = message.optJSONArray("content")
                s.acting = (0 until (content?.length() ?: 0)).any { content!!.optJSONObject(it)?.optString("type") == "tool_use" }
                s.meta.put("said", now())
                save(s)
            }
            tell(s, JSONObject().put("type", kind).put("message", message).put("session_id", s.id)
                .put("parent_tool_use_id", JSONObject.NULL).put("uuid", uuid))
        }
        changed()
    }

    /** Said to those attached live, as claude says it (stream-json). */
    fun tell(s: PhoneSession, msg: JSONObject) {
        val to = synchronized(lock) { s.live.toList() }
        for (h in to) h.send(JSONObject().put("op", "claude").put("session", s.id).put("msg", msg))
    }

    /** May the agent use a tool: as its chat's permission mode has it (PhoneTools.need), or one always allowed in this
     * chat (never a question for you, nor a plan), or its permission asked (claude's can_use_tool) and waited for. The
     * answer (behavior: allow, with updatedInput; or deny, with a message), or null if the turn's stopped first. */
    fun permit(s: PhoneSession, toolUseId: String, tool: String, input: JSONObject): JSONObject? {
        val need = PhoneTools.need(mode(s), tool, !s.meta.isNull("bot"))
        val asked = tool == ASK_TOOL || tool == "ExitPlanMode"
        if (need == "allow" || !asked && tool in synchronized(lock) { s.alwaysAllow })
            return JSONObject().put("behavior", "allow").put("updatedInput", input)
        if (need != "ask") return JSONObject().put("behavior", "deny").put("message", need)
        val rid = UUID.randomUUID().toString()
        val wait = CompletableFuture<JSONObject?>()
        val ask = JSONObject().put("type", "control_request").put("request_id", rid).put("request", JSONObject()
            .put("subtype", "can_use_tool").put("tool_name", tool).put("input", input).put("tool_use_id", toolUseId)
            .put("permission_suggestions", JSONArray()))
        val to = synchronized(lock) {
            if (s.cancel || s.deleted) return null
            s.asks[rid] = ask
            s.waits[rid] = wait
            s.viewers.toList()
        }
        for (h in to) h.send(JSONObject().put("op", "claude").put("session", s.id).put("msg", ask))
        changed()
        return try {
            wait.get()
        } finally {
            synchronized(lock) {
                s.asks.remove(rid)
                s.waits.remove(rid)
            }
            changed()
        }
    }

    /** Its turn's over: its result said (to those attached live, and as turn_ended to every head subscribed), and what
     * was sent meanwhile taken now, in a turn of its own (in the same hold of the lock, so no other turn starts
     * between). */
    fun ended(s: PhoneSession, result: String, error: Boolean, turns: Int, ms: Long) {
        tell(s, JSONObject().put("type", "result").put("subtype", if (error) "error_during_execution" else "success")
            .put("is_error", error).put("result", result).put("num_turns", turns).put("duration_ms", ms)
            .put("total_cost_usd", 0).put("session_id", s.id).put("usage", JSONObject()))
        val subscribed = synchronized(lock) {
            s.acting = false
            s.asks.clear()
            s.stopModel = null
            val next = s.queued.list()
            if (next.isNotEmpty() && !s.deleted && sessions[s.id] === s) {
                s.meta.remove("queued")
                begin(s, next, null)
            } else {
                s.busy = false
                if (!s.deleted) {
                    s.meta.put("turn", false)
                    save(s)
                }
            }
            heads.filter { it.subscribed && (it.archived || !s.meta.optBoolean("archived")) }
        }
        val end = JSONObject().put("op", "turn_ended").put("session", s.id).put("result", result).put("is_error", error)
            .put("cost", 0).put("turns", turns).put("duration_ms", ms)
        for (h in subscribed) h.send(end)
        changed()
    }

    fun transcript(s: PhoneSession) = File(root, "transcripts/${s.id}.jsonl")

    /** (lock held) A record of its transcript, as Claude Code writes one (what heads read), added to the conversation
     * kept for its model too: its uuid; null for an agent deleted. */
    private fun record(s: PhoneSession, kind: String, message: JSONObject, uuid: String?, apiError: Boolean = false): String? {
        if (s.deleted) return null
        val id = uuid ?: UUID.randomUUID().toString()
        val rec = JSONObject().put("parentUuid", JSONObject.NULL).put("isSidechain", false).put("type", kind)
            .put("uuid", id).put("timestamp", java.time.Instant.now().toString()).put("sessionId", s.id)
            .put("cwd", shared()).put("entrypoint", "baude-android").put("message", message)
        if (apiError) rec.put("isApiErrorMessage", true)
        transcript(s).appendText(rec.toString() + "\n")
        s.convo?.let { c -> PhoneAgent.add(c, rec) }
        return id
    }

    private fun save(s: PhoneSession) {
        if (s.deleted) return
        val f = File(root, "sessions/${s.id}.json")
        val tmp = File(f.path + ".new")
        tmp.writeText(s.meta.toString())
        tmp.renameTo(f)
    }

    /** Whole lines of a file from offset, as a host serves a transcript: {offset, next, size, data}. */
    private fun lines(f: File, offset: Long, limit: Int): Array<Pair<String, Any?>> {
        if (!f.exists()) return arrayOf("offset" to 0, "next" to 0, "size" to 0, "data" to "")
        RandomAccessFile(f, "r").use { r ->
            val size = r.length()
            val at = if (offset > size || offset < 0) 0 else offset
            val room = (if (limit > 0) limit else 1 shl 20).coerceIn(4096, 16 shl 20)
            r.seek(at)
            val buf = java.io.ByteArrayOutputStream()
            val chunk = ByteArray(room)
            while (true) {
                val n = r.read(chunk)
                if (n <= 0) break
                buf.write(chunk, 0, n)
                if ((0 until n).any { chunk[it] == NL } || buf.size() > (64 shl 20)) break
            }
            val data = buf.toByteArray()
            val end = data.lastIndexOf(NL) + 1
            return arrayOf("offset" to at, "next" to at + end, "size" to size,
                "data" to Base64.getEncoder().encodeToString(data.copyOf(end)))
        }
    }

    // ---- what heads are told

    fun info(): JSONObject = JSONObject().put("id", id).put("name", name).put("group", name)
        .put("ops", JSONArray(OPS))
        .put("models", JSONArray(Inference.found().map { JSONObject().put("value", it.value).put("displayName", it.display) }))
        .put("host", name).put("home", shared())
        .put("pid", android.os.Process.myPid()).put("groups", JSONArray().put(name)).put("init", JSONObject.NULL)
        .put("commit", JSONObject.NULL).put("update", JSONObject.NULL).put("providers", JSONArray())
        .put("notes", JSONObject.NULL).put("phone", true)
        .put("mesh", JSONObject().put("members", JSONArray()).put("links", JSONArray()).put("proxy", JSONObject.NULL)
            .put("inactive", JSONArray()).put("found", JSONArray()).put("gone", JSONArray()))

    fun listing(s: PhoneSession): JSONObject = synchronized(lock) {
        val f = transcript(s)
        val size = if (f.exists()) f.length() else 0L
        val bot = s.meta.optJSONObject("bot")
        JSONObject().put("id", s.id).put("title", s.meta.opt("title") ?: JSONObject.NULL)
            .put("cwd", shared()).put("model", s.meta.optString("model")).put("effort", JSONObject.NULL)
            .put("mode", mode(s)).put("group", name).put("created", s.meta.optDouble("created"))
            .put("said", s.meta.optDouble("said")).put("updated", if (f.exists()) f.lastModified() / 1000.0 else s.meta.optDouble("created"))
            .put("size", size).put("state", if (s.asks.isNotEmpty()) "waiting" else if (s.busy) "working" else "idle") // (between turns: ready, nothing to resume)
            .put("prompts", s.meta.optInt("prompts")).put("cli_id", JSONObject.NULL).put("transcript", JSONObject.NULL)
            .put("archived", s.meta.optBoolean("archived")).put("draft", s.meta.optBoolean("draft")).put("written", size)
            .put("subagents", 0).put("commands", JSONArray()).put("cut", s.meta.opt("cut") ?: JSONObject.NULL)
            .put("bot", if (bot == null) JSONObject.NULL else JSONObject().put("name", bot.optString("name"))
                .put("since", bot.optDouble("since")).put("picture", JSONObject.NULL))
            .put("acting", s.acting && s.busy && s.asks.isEmpty()).put("fork_from", JSONObject.NULL)
            .put("spawned_by", JSONObject.NULL).put("start_key", s.meta.opt("start_key") ?: JSONObject.NULL)
            .put("mesh_tools", true).put("always_allow", JSONArray(s.alwaysAllow.sorted()))
            .put("asks", JSONArray(s.asks.entries.map { (rid, m) ->
                val r = m.optJSONObject("request") ?: JSONObject()
                JSONObject().put("request_id", rid).put("tool", r.optString("tool_name")).put("input", r.optJSONObject("input"))
            }))
    }

    private fun listing(archived: Boolean): JSONArray = synchronized(lock) {
        JSONArray(sessions.values.filter { archived || !it.meta.optBoolean("archived") }.map { listing(it) }
            .sortedByDescending { it.optDouble("said") })
    }

    /** Its agents' list changed: each head subscribed told, soon (one telling for changes close together). */
    fun changed() {
        synchronized(lock) {
            if (dirty) return
            dirty = true
        }
        work.schedule({
            val (to, info) = synchronized(lock) {
                dirty = false
                heads.filter { it.subscribed }.toList() to info()
            }
            for (h in to) h.send(JSONObject().put("op", "sessions").put("sessions", listing(h.archived)).put("server", info))
            main.post { Mesh.tick.value++ }
        }, 80, TimeUnit.MILLISECONDS)
    }

    fun session(sid: String): PhoneSession? = synchronized(lock) { sessions[sid] }

    /** The folder heads are told its agents are in (their folders' own, the phone's home): one for all, so Baude lists
     * them together, as it lists a folder's chats; each agent's tools use its own folder in it. */
    fun shared(): String = if (::root.isInitialized) File(root, "agents").path else ""

    fun mode(s: PhoneSession): String = s.meta.optString("mode").ifBlank { "default" }

    /** Its permission mode changed (a head's, or its plan agreed to): kept, and said as claude says it. */
    fun setMode(s: PhoneSession, mode: String) {
        live(s) {
            s.meta.put("mode", mode)
            save(s)
        }
        tell(s, JSONObject().put("type", "system").put("subtype", "status").put("permissionMode", mode).put("session_id", s.id))
        changed()
    }

    private fun now() = System.currentTimeMillis() / 1000.0

    private const val NL: Byte = 10 // (a newline)
    const val MEMORY = "memory.md" // (a Bot's memory, in its own folder: its file tools edit it)
}
