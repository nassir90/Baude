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

/** Where what this phone's host says goes, and who it is: the app's own head (who null), or a tunnel from the mesh
 * (who: the member it came from, as the mesh said). */
abstract class Head(val who: String?) {
    @Volatile var subscribed = false
    @Volatile var archived = false
    @Volatile var answers = true // (what its sessions ask: answered by it, if it's attached)
    abstract fun send(msg: JSONObject)
}

/** One of this phone's agents: what's kept of it (meta), and what's going on in it now. */
class PhoneSession(val meta: JSONObject) {
    val id: String get() = meta.getString("id")
    val viewers = HashSet<Head>()  // (attached: told what it asks)
    val live = HashSet<Head>()     // (attached live: told everything it says, as it says it)
    val asks = LinkedHashMap<String, JSONObject>() // (a permission it asks, not answered yet: its control_request)
    val waits = HashMap<String, CompletableFuture<JSONObject?>>() // (each ask's answer, waited for at its turn)
    val queued = ArrayList<JSONObject>() // (messages sent at a turn: taken after it)
    val alwaysAllow = HashSet<String>()
    @Volatile var busy = false
    @Volatile var acting = false
    @Volatile var cancel = false
    val folder: File get() = File(meta.getString("cwd"))
}

/** Who may do what with this phone's agents, as you've set it here: nothing's allowed till you allow it. talk: who in
 * the mesh may start agents here, or talk to them (messages, answers, stopping, renaming, deleting): "phone", this
 * phone only; "hosts", the machines of the hosts you've added here too; "mesh", every member. Anyone in the mesh sees
 * their chats, as it sees any host's. inference: the models in the mesh that may serve them, "member/provider" each. */
object Trust {
    val talk = mutableStateOf("phone")
    val inference = mutableStateOf<Set<String>>(emptySet())
    private fun prefs(ctx: Context) = ctx.getSharedPreferences("phone", Context.MODE_PRIVATE)

    fun load(ctx: Context) {
        talk.value = prefs(ctx).getString("talk", "phone") ?: "phone"
        inference.value = prefs(ctx).getStringSet("inference", emptySet())?.toSet() ?: emptySet()
    }

    fun setTalk(ctx: Context, to: String) {
        talk.value = to
        prefs(ctx).edit().putString("talk", to).apply()
        PhoneHost.changed()
    }

    fun setServes(ctx: Context, key: String, on: Boolean) {
        inference.value = if (on) inference.value + key else inference.value - key
        prefs(ctx).edit().putStringSet("inference", inference.value).apply()
        PhoneHost.changed()
    }

    /** May a head (who: its member, null for this phone) start agents here, or talk to them? */
    fun mayTalk(who: String?): Boolean = when {
        who == null -> true
        talk.value == "mesh" -> true
        talk.value == "hosts" -> Mesh.hosts.any { HostStore.machine(PhoneHost.ctx, it) == who }
        else -> false
    }

    fun mayServe(member: String, provider: String): Boolean = "$member/$provider" in inference.value
}

/** This phone as a host of the mesh: agents, each a Bot (a name, and a memory in its folder), whose loop and tools run
 * here (PhoneAgent, PhoneTools) and whose model calls go to a member of the mesh that has the model (the API proxy:
 * its host process's infer). Heads talk to it in the wire's own lines (wire.py), as to any host process: this app's
 * own (connection, in the app), and the mesh's, through the phone's link (PhoneLink), so Baude and the other members
 * list its agents and read them as any host's. What it doesn't do, it says so (error), as a host process does for an
 * op it doesn't know: no shell, no catalogue, no files read for a head, no models of its own to serve. Kept in the
 * app's own folder: phone/sessions (each one's meta), phone/transcripts (each one's, as Claude Code writes one, which
 * is what the heads read) and phone/agents (each one's folder, the only files its tools reach). */
object PhoneHost {
    const val VERSION = 15 // (wire.VERSION: the lines it speaks, as far as it speaks them)
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
            Trust.load(ctx)
            root = File(ctx.filesDir, "phone")
            for (d in listOf("sessions", "transcripts", "agents")) File(root, d).mkdirs()
            for (f in File(root, "sessions").listFiles().orEmpty().filter { it.name.endsWith(".json") }) {
                try {
                    val meta = JSONObject(f.readText())
                    if (isId(meta.optString("id"))) sessions[meta.getString("id")] = PhoneSession(meta)
                } catch (_: Exception) {
                }
            }
        }
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
        Connection(ctx, Place.Phone, { _, _ -> }, { null }, hear, told) {
        private val waiting = ConcurrentHashMap<Int, (JSONObject) -> Unit>()
        private val streaming = ConcurrentHashMap<Int, (JSONObject) -> Unit>()
        private val next = AtomicInteger(1)
        private val head = object : Head(null) {
            override fun send(msg: JSONObject) {
                val req = msg.optInt("req", -1)
                if (msg.optString("op") == "sessions") msg.optJSONObject("server")?.let { info = it }
                val call = if (req < 0) null else streaming[req]?.also {
                    if (msg.optString("op") in setOf("infer_end", "error")) streaming.remove(req)
                } ?: waiting.remove(req)
                if (call != null) call(msg) else hear(this@Local, msg)
            }
        }

        override fun start() {
            info = PhoneHost.info()
            state = "up"
            told(this)
            send(JSONObject().put("op", "subscribe"))
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

        override fun stream(msg: JSONObject, then: (JSONObject) -> Unit) {
            val req = next.getAndIncrement()
            streaming[req] = then
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

    /** Ops that change something, or say something to an agent: only from a head Trust lets talk. */
    private val TALK = setOf("start", "input", "stop", "rename", "archive", "unarchive", "delete", "always_allow", "bot")
    private val LOOK = setOf("attach", "detach", "transcript", "subagents", "bot_picture", "memory") // (anyone's)
    private val ASKING = setOf("initialize", "get_settings", "get_context_usage") // (an input that only asks)

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
        if (op !in TALK && op !in LOOK) return reply("error", "error" to "unknown op '$op' (this is a phone's host: its agents' own ops only)")
        val asking = msg.optJSONObject("msg")?.takeIf { op == "input" && it.optString("type") == "control_request" }
            ?.optJSONObject("request")?.optString("subtype") in ASKING // (what a head asks of an agent as it opens it)
        if (op in TALK && !asking && !Trust.mayTalk(head.who)) {
            return reply("error", "error" to "Agents on $name take this only from the phone itself (its This phone page " +
                "says who else may)", "plain" to true)
        }
        if (op == "start") return start(head, msg, ::reply)
        val s = synchronized(lock) { sessions[msg.optString("session")] }
            ?: return reply("error", "error" to "no session ${msg.optString("session")}")
        when (op) {
            "attach" -> synchronized(lock) {
                head.answers = msg.optBoolean("answers", true) && Trust.mayTalk(head.who)
                s.viewers.add(head)
                if (msg.optBoolean("live")) s.live.add(head)
                msg.optJSONArray("always_allow")?.let { a ->
                    if (Trust.mayTalk(head.who)) for (i in 0 until a.length()) s.alwaysAllow.add(a.optString(i))
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
                synchronized(lock) {
                    s.meta.put("title", msg.optString("title").trim().take(200).ifBlank { null } ?: JSONObject.NULL)
                    save(s)
                }
                changed()
                reply("renamed", "session" to listing(s))
            }
            "archive", "unarchive" -> {
                if (op == "archive") stopTurn(s)
                synchronized(lock) {
                    s.meta.put("archived", op == "archive")
                    save(s)
                }
                changed()
                reply(op + "d", "session" to s.id)
            }
            "delete" -> {
                stopTurn(s)
                val watching = synchronized(lock) {
                    sessions.remove(s.id)
                    (s.viewers + s.live).toSet()
                }
                File(root, "sessions/${s.id}.json").delete()
                File(root, "transcripts/${s.id}.jsonl").delete()
                s.folder.deleteRecursively()
                for (h in watching) h.send(JSONObject().put("op", "deleted").put("session", s.id))
                changed()
                reply("deleted", "session" to s.id, "trashed" to JSONArray())
            }
            "always_allow" -> {
                synchronized(lock) {
                    msg.optJSONArray("add")?.let { a -> for (i in 0 until a.length()) s.alwaysAllow.add(a.optString(i)) }
                        ?: msg.optJSONArray("tools")?.let { a ->
                            s.alwaysAllow.clear()
                            for (i in 0 until a.length()) s.alwaysAllow.add(a.optString(i))
                        }
                }
                changed()
                reply("allowed", "session" to s.id, "tools" to JSONArray(s.alwaysAllow.sorted()))
            }
            "bot" -> {
                synchronized(lock) {
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
                if (msg.has("text")) {
                    if (!Trust.mayTalk(head.who)) return reply("error", "error" to "only the phone itself writes its agents' memory")
                    file.writeText(msg.optString("text"))
                }
                reply("memory", "session" to s.id, "text" to (if (file.exists()) file.readText() else ""),
                    "path" to file.path, "mtime" to if (file.exists()) file.lastModified() / 1000.0 else null)
            }
        }
    }

    /** A new agent: its folder made, a Bot if it's named, its prompt sent. Its model's one the mesh serves this phone,
     * as Trust allows (PhoneAgent.Served's value). */
    private fun start(head: Head, msg: JSONObject, reply: (String, Array<out Pair<String, Any?>>) -> Unit) {
        val served = Inference.of(msg.optString("model"))
            ?: return reply("error", arrayOf("error" to "no model the mesh serves this phone: ${msg.optString("model")}", "plain" to true))
        val sid = UUID.randomUUID().toString()
        val folder = File(root, "agents/$sid").apply { mkdirs() }
        val botName = msg.optJSONObject("bot")?.optString("name")?.trim()?.take(64)?.ifBlank { null }
        val meta = JSONObject().put("id", sid).put("cwd", folder.path).put("model", served.value)
            .put("infer", served.json()).put("title", msg.optString("title").trim().ifBlank { null } ?: JSONObject.NULL)
            .put("created", now()).put("said", now()).put("prompts", 0).put("archived", false)
            .put("bot", if (botName != null) JSONObject().put("name", botName).put("since", now()) else JSONObject.NULL)
            .put("start_key", msg.optString("start_key").takeIf(::isId) ?: JSONObject.NULL)
        val s = PhoneSession(meta)
        if (botName != null) File(folder, MEMORY).writeText("")
        synchronized(lock) {
            sessions[sid] = s
            save(s)
            if (msg.optBoolean("attach")) {
                head.answers = msg.optBoolean("answers", true)
                s.viewers.add(head)
                s.live.add(head)
            }
        }
        reply("started", arrayOf("session" to sid, "forked" to false))
        val prompt = msg.optString("prompt")
        if (prompt.isNotBlank()) input(head, s, JSONObject().put("type", "user").put("message",
            JSONObject().put("role", "user").put("content", JSONArray().put(JSONObject().put("type", "text").put("text", prompt)))))
        changed()
    }

    /** What a head sends an agent: a message of yours (a turn, or taken after the one going on), an answer to what it
     * asked, or one of claude's control requests (initialize: what it runs; interrupt: its turn stopped; the rest, as
     * a host answers them for an agent that isn't running: nothing to change). */
    private fun input(head: Head, s: PhoneSession, msg: JSONObject) {
        when (msg.optString("type")) {
            "user" -> {
                val message = msg.optJSONObject("message") ?: return
                synchronized(lock) {
                    if (s.busy) {
                        s.queued.add(message)
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
                        .put("current_permission_mode", "default").put("output_style", "default")
                        .put("available_output_styles", JSONArray().put("default")))
                    "interrupt" -> {
                        val running = s.busy
                        stopTurn(s)
                        answer(JSONObject().put("running", running))
                    }
                    "set_model" -> if (request.optString("model").let { it.isEmpty() || it == s.meta.optString("model") }) answer(JSONObject())
                        else answer(null, "an agent on this phone keeps the model it was made with: make another for that one")
                    "get_settings" -> answer(JSONObject().put("applied", JSONObject().put("model", s.meta.optString("model"))))
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
        s.meta.put("said", now())
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
            s.queued.clear()
            s.asks.clear()
            s.waits.values.toList().also { s.waits.clear() }
        }
        waiting.forEach { it.complete(null) }
        changed()
    }

    // ---- what an agent's turn does here (PhoneAgent's)

    /** A message of the agent's (assistant) or of its tools' answers (user), kept and said to those attached live.
     * apiError: what it says is why its model couldn't answer (marked so, as claude marks one: shown as an error). */
    fun said(s: PhoneSession, kind: String, message: JSONObject, apiError: Boolean = false) {
        synchronized(lock) {
            val uuid = record(s, kind, message, null, apiError)
            if (kind == "assistant") {
                val content = message.optJSONArray("content")
                val calls = (0 until (content?.length() ?: 0)).any { content!!.optJSONObject(it)?.optString("type") == "tool_use" }
                if (calls != s.acting) s.acting = calls
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

    /** May the agent use a tool: its permission asked (claude's can_use_tool), and waited for, unless it's one always
     * allowed in this chat. The answer (behavior: allow, with updatedInput; or deny, with a message), or null if the
     * turn's stopped first. */
    fun permit(s: PhoneSession, toolUseId: String, tool: String, input: JSONObject): JSONObject? {
        val rid = UUID.randomUUID().toString()
        val wait = CompletableFuture<JSONObject?>()
        val ask = JSONObject().put("type", "control_request").put("request_id", rid).put("request", JSONObject()
            .put("subtype", "can_use_tool").put("tool_name", tool).put("input", input).put("tool_use_id", toolUseId)
            .put("permission_suggestions", JSONArray()))
        val to = synchronized(lock) {
            if (s.cancel) return null
            if (tool in s.alwaysAllow) return JSONObject().put("behavior", "allow").put("updatedInput", input)
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
     * was sent meanwhile taken now, in a turn of its own. */
    fun ended(s: PhoneSession, result: String, error: Boolean, turns: Int, ms: Long) {
        val msg = JSONObject().put("type", "result").put("subtype", if (error) "error_during_execution" else "success")
            .put("is_error", error).put("result", result).put("num_turns", turns).put("duration_ms", ms)
            .put("total_cost_usd", 0).put("session_id", s.id).put("usage", JSONObject())
        tell(s, msg)
        val subscribed = synchronized(lock) {
            s.busy = false
            s.acting = false
            s.asks.clear()
            heads.filter { it.subscribed && (it.archived || !s.meta.optBoolean("archived")) }
        }
        val end = JSONObject().put("op", "turn_ended").put("session", s.id).put("result", result).put("is_error", error)
            .put("cost", 0).put("turns", turns).put("duration_ms", ms)
        for (h in subscribed) h.send(end)
        synchronized(lock) {
            if (s.queued.isNotEmpty() && sessions[s.id] === s) {
                val next = s.queued.toList()
                s.queued.clear()
                begin(s, next, null)
            }
        }
        changed()
    }

    fun transcript(s: PhoneSession) = File(root, "transcripts/${s.id}.jsonl")

    /** (lock held) A record of its transcript, as Claude Code writes one (what heads read, and the agent's next turn's
     * conversation): its uuid. */
    private fun record(s: PhoneSession, kind: String, message: JSONObject, uuid: String?, apiError: Boolean = false): String {
        val id = uuid ?: UUID.randomUUID().toString()
        val rec = JSONObject().put("parentUuid", JSONObject.NULL).put("isSidechain", false).put("type", kind)
            .put("uuid", id).put("timestamp", java.time.Instant.now().toString()).put("sessionId", s.id)
            .put("cwd", s.meta.optString("cwd")).put("entrypoint", "baude-android").put("message", message)
        if (apiError) rec.put("isApiErrorMessage", true)
        transcript(s).appendText(rec.toString() + "\n")
        return id
    }

    private fun save(s: PhoneSession) {
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

    fun info(): JSONObject = JSONObject().put("id", id).put("name", name).put("group", name).put("version", VERSION)
        .put("models", JSONArray(if (Trust.talk.value == "phone") emptyList<JSONObject>() else Inference.allowed().map {
            JSONObject().put("value", it.value).put("displayName", it.display)
        }))
        .put("host", name).put("home", if (::root.isInitialized) root.path else "").put("pid", android.os.Process.myPid())
        .put("groups", JSONArray().put(name)).put("init", JSONObject.NULL).put("commit", JSONObject.NULL)
        .put("update", JSONObject.NULL).put("providers", JSONArray()).put("notes", JSONObject.NULL).put("phone", true)
        .put("mesh", JSONObject().put("members", JSONArray()).put("links", JSONArray()).put("proxy", JSONObject.NULL)
            .put("inactive", JSONArray()).put("found", JSONArray()).put("gone", JSONArray()))

    fun listing(s: PhoneSession): JSONObject = synchronized(lock) {
        val f = transcript(s)
        val size = if (f.exists()) f.length() else 0L
        val bot = s.meta.optJSONObject("bot")
        JSONObject().put("id", s.id).put("title", s.meta.opt("title") ?: JSONObject.NULL)
            .put("cwd", s.meta.optString("cwd")).put("model", s.meta.optString("model")).put("effort", JSONObject.NULL)
            .put("mode", "default").put("group", name).put("created", s.meta.optDouble("created"))
            .put("said", s.meta.optDouble("said")).put("updated", if (f.exists()) f.lastModified() / 1000.0 else s.meta.optDouble("created"))
            .put("size", size).put("state", if (s.asks.isNotEmpty()) "waiting" else if (s.busy) "working" else "stopped")
            .put("prompts", s.meta.optInt("prompts")).put("cli_id", JSONObject.NULL).put("transcript", JSONObject.NULL)
            .put("archived", s.meta.optBoolean("archived")).put("draft", false).put("written", size).put("subagents", 0)
            .put("commands", JSONArray()).put("cut", JSONObject.NULL)
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

    private fun now() = System.currentTimeMillis() / 1000.0

    private const val NL: Byte = 10 // (a newline)
    const val MEMORY = "memory.md" // (a Bot's memory, in its own folder: its file tools edit it)
}
