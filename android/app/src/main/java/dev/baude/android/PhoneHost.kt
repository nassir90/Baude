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
    open val origin: String? get() = null // (a tunnel's: the member it came from, as the mesh said)
    open val sender: JSONObject? get() = null // (a tunnel's: who what's sent through it is from, as its relay named)
    @Volatile var subscribed = false
    @Volatile var archived = false
    @Volatile var answers = true // (what its agents ask, answered by it, as attach says: ask_timeout's wait runs on without)
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
    /** Messages sent at a turn, taken after it: {message, uuid} each. */
    val queued: JSONArray get() = meta.optJSONArray("queued") ?: JSONArray().also { meta.put("queued", it) }
    var senders: JSONObject? = null // (its sender store, {uuid: sender}, once it's read: PhoneHost.senders)
    val queuedBy = HashMap<String, Head>() // (who sent each one queued, by its uuid: not told it as it's taken, ended)
}

/** This phone as a host of the mesh: agents, each a Bot (a name, and a memory in its folder), whose loop and tools run
 * here (PhoneAgent, PhoneTools) and whose model calls go to a member of the mesh that has the model (the API proxy:
 * its host process's infer). Heads talk to it in the wire's own lines (wire.py), as to any host process: this app's
 * own (connection, in the app), and the mesh's, through the phone's link (PhoneLink), so Baude and the other members
 * list its agents and read them as any host's. Its hello names the ops it takes (ops), and claims no version. Kept in
 * the app's own folder: phone/sessions (each one's meta), phone/transcripts (each one's, as Claude Code writes one,
 * which is what the heads read) and phone/agents (each one's folder, the only files its tools reach). */
object PhoneHost {
    /** The ops it takes, and the control requests its agents take that a program's mightn't (hello's ops:
     * wire.takes). */
    val OPS = listOf("hello", "ping", "subscribe", "start", "attach", "detach", "input", "stop", "transcript",
        "subagents", "rename", "archive", "unarchive", "delete", "always_allow", "bot", "bot_picture", "memory",
        "read_file", "catalogue", "list_dir", "senders", "ephemeral", "control", "set_permission_mode", "set_model")
    private val CONTROLS = setOf("control", "set_permission_mode", "set_model", "ephemeral") // (OPS' that aren't ops: the
    // control requests its agents take (control: any a claude takes, answered as a host answers them between turns), and
    // start's ephemeral)
    private val TAKES = OPS.toSet() - CONTROLS
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
            name = prefs.getString("name", null)?.ifBlank { null } ?: deviceName()
            root = File(ctx.filesDir, "phone")
            for (d in listOf("sessions", "transcripts", "agents", "pictures", "trash", "senders")) File(root, d).mkdirs()
            File(root, "ephemeral").deleteRecursively() // (side questions from before: gone with the app, as with their heads)
            File(root, "ephemeral").mkdirs()
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
        for (q in s.queued.list()) record(s, "user", q.optJSONObject("message") ?: continue, q.optString("uuid").ifBlank { null })
        s.meta.remove("queued")
        s.meta.put("turn", false).put("cut", now())
        save(s)
    }

    /** Its name in the mesh changed (This phone's Name; empty: the phone's own again): kept, and told to its heads and
     * to the mesh, whose members list it by the new one. */
    fun rename(to: String) {
        val n = tidy(to).take(64)
        ctx.getSharedPreferences("phone", Context.MODE_PRIVATE).edit()
            .apply { if (n.isEmpty()) remove("name") else putString("name", n) }.apply()
        name = n.ifEmpty { deviceName() }
        changed()
        PhoneLink.renamed()
    }

    /** What the phone's called by Android: its name in Android's settings, else its model's. */
    fun deviceName(): String {
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
            override fun send(sent: JSONObject) {
                val msg = checked(sent, answer = true) ?: return
                val req = (msg.opt("req") as? Number)?.toInt() ?: -1
                if (msg.optString("op") == Op.SESSIONS) Msg.Sessions.from(msg).server?.let { info = it }
                val call = if (req < 0) null else waiting.remove(req)
                if (call != null) call(msg) else hear(this@Local, msg)
            }
        }

        override fun start() {
            info = Msg.Info.from(PhoneHost.info())
            state = "up"
            told(this)
            send(Msg.Subscribe(archived = true).json())
        }

        override fun stop() {
            take(head, null)
        }

        override fun send(msg: JSONObject): Boolean {
            checked(msg)?.let { take(head, it) }
            return true
        }

        override fun request(msg: JSONObject, then: (JSONObject) -> Unit) {
            val req = next.getAndIncrement()
            ownRefusal(msg.put("req", req))?.let { return then(it) }
            waiting[req] = then
            send(msg)
        }
    }

    /** A head's line, handled in turn on the host's own thread (msg null: the head's gone). */
    fun take(head: Head, msg: JSONObject?) {
        work.execute {
            try {
                if (msg == null) gone(head) else handle(head, msg)
            } catch (e: Exception) {
                msg?.let { head.send(Msg.Error(req = it.opt("req") as? Number,
                    error = "${e.javaClass.simpleName}: ${e.message}").json()) }
            }
        }
    }

    private fun gone(head: Head) {
        val left = synchronized(lock) {
            heads.remove(head)
            for (s in sessions.values) {
                s.viewers.remove(head)
                s.live.remove(head)
            }
            sessions.values.filter { it.meta.optBoolean("ephemeral") && it.viewers.isEmpty() }
        }
        left.forEach { drop(it) }
    }

    /** A side question (ephemeral) whose last head's gone: stopped, and gone with what it kept, as a host drops one. */
    private fun drop(s: PhoneSession) {
        stopTurn(s)
        synchronized(lock) {
            if (sessions[s.id] !== s) return
            s.deleted = true
            sessions.remove(s.id)
            transcript(s).delete()
            sendersFile(s).delete()
            if (s.meta.optBoolean("own_folder")) s.folder.deleteRecursively()
        }
    }

    private const val ASK_TOOL = "AskUserQuestion" // (never allowed without asking: its ask is the question)

    private fun handle(head: Head, msg: JSONObject) {
        val op = msg.optString("op")
        val req = msg.opt("req")
        fun reply(kind: String, vararg kv: Pair<String, Any?>) {
            val o = JSONObject().put("op", kind)
            if (req != null) o.put("req", req)
            for ((k, v) in kv) if (v != null) o.put(k, v) // (one that may be null: given as JSONObject.NULL)
            head.send(o)
        }
        synchronized(lock) { heads.add(head) }
        if (op == Op.PING) return
        // (an op it doesn't take, or fields not as the wire's schema has them: its two refusals, as any host's; looked
        // at as it is on the wire, this app's own head's too)
        WireCheck.refusal(JSONObject(msg.toString()), TAKES)?.let { return head.send(it) }
        when (op) {
            Op.HELLO -> return reply(Op.HELLO, "server" to info())
            Op.SUBSCRIBE -> {
                head.subscribed = true
                head.archived = Msg.Subscribe.from(msg).archived == true
                return reply(Op.SESSIONS, "sessions" to listing(head.archived), "server" to info())
            }
        }
        if (op == Op.START) return start(head, msg, ::reply)
        if (op == Op.READ_FILE) return reply(Op.FILE, *readFile(msg))
        if (op == Op.LIST_DIR) return reply(Op.DIR, *listDir(Msg.ListDir.from(msg).path.orEmpty()))
        if (op == Op.CATALOGUE) return reply(Op.CATALOGUE, *catalogue(Msg.Catalogue.from(msg).cwd.orEmpty()))
        val sid = when (op) { // (the session each of the rest is about)
            Op.ATTACH -> Msg.Attach.from(msg).session
            Op.DETACH -> Msg.Detach.from(msg).session
            Op.INPUT -> Msg.Input.from(msg).session
            Op.STOP -> Msg.Stop.from(msg).session
            Op.TRANSCRIPT -> Msg.Transcript.from(msg).session
            Op.SUBAGENTS -> Msg.Subagents.from(msg).session
            Op.SENDERS -> Msg.SendersOp.from(msg).session
            Op.RENAME -> Msg.Rename.from(msg).session
            Op.ARCHIVE -> Msg.Archive.from(msg).session
            Op.UNARCHIVE -> Msg.Unarchive.from(msg).session
            Op.DELETE -> Msg.Delete.from(msg).session
            Op.ALWAYS_ALLOW -> Msg.AlwaysAllow.from(msg).session
            Op.BOT -> Msg.Bot.from(msg).session
            Op.BOT_PICTURE -> Msg.BotPicture.from(msg).session
            Op.MEMORY -> Msg.Memory.from(msg).session
            else -> null
        }.orEmpty()
        val s = synchronized(lock) { sessions[sid] } ?: return reply(Op.ERROR, "error" to "no session $sid")
        when (op) {
            Op.ATTACH -> synchronized(lock) {
                val a = Msg.Attach.from(msg)
                head.answers = a.answers ?: true
                s.viewers.add(head)
                if (a.live == true) s.live.add(head)
                for (t in a.alwaysAllow.orEmpty()) if (t != ASK_TOOL) s.alwaysAllow.add(t)
                reply(Op.ATTACHED, "session" to listing(s), "asks" to JSONArray(s.asks.values.toList()))
            }
            Op.DETACH -> {
                val last = synchronized(lock) {
                    s.viewers.remove(head)
                    s.live.remove(head)
                    s.meta.optBoolean("ephemeral") && s.viewers.isEmpty()
                }
                if (last) drop(s)
            }
            Op.INPUT -> {
                val i = Msg.Input.from(msg)
                input(head, s, i.msg ?: JSONObject(), sentBy(head, i.`as`))
                if (req != null) reply(Op.ACCEPTED, "session" to s.id)
            }
            Op.STOP -> {
                stopTurn(s)
                reply(Op.STOPPED, "session" to s.id)
            }
            Op.TRANSCRIPT -> Msg.Transcript.from(msg).let { t ->
                reply(Op.TRANSCRIPT, "session" to s.id, *lines(transcript(s), t.offset?.toLong() ?: 0, t.limit?.toInt() ?: 0))
            }
            Op.SUBAGENTS -> reply(Op.SUBAGENTS, "session" to s.id, "files" to JSONArray(), "more" to false)
            Op.SENDERS -> senders(s)?.let { reply(Op.SENDERS, "session" to s.id, "senders" to JSONObject(it.toString()),
                "rev" to s.meta.optInt("senders")) } ?: reply(Op.ERROR, "error" to "its sender store couldn't be read")
            Op.RENAME -> {
                live(s) {
                    s.meta.put("title", Msg.Rename.from(msg).title.orEmpty().trim().take(200).ifBlank { null } ?: JSONObject.NULL)
                    save(s)
                }
                changed()
                reply(Op.RENAMED, "session" to listing(s))
            }
            Op.ARCHIVE, Op.UNARCHIVE -> {
                if (op == Op.ARCHIVE) stopTurn(s)
                live(s) {
                    s.meta.put("archived", op == "archive")
                    save(s)
                }
                changed()
                reply(op + "d", "session" to s.id)
            }
            Op.DELETE -> {
                stopTurn(s)
                // (under the lock: nothing it says after is kept, or made again)
                val (watching, went) = synchronized(lock) {
                    s.deleted = true
                    sessions.remove(s.id)
                    val went = trash(s)
                    (s.viewers + s.live).toSet() to went
                }
                for (h in watching) h.send(Msg.Deleted(session = s.id).json())
                changed()
                reply(Op.DELETED, "session" to s.id, "trashed" to JSONArray(went))
            }
            Op.ALWAYS_ALLOW -> {
                synchronized(lock) {
                    val r = Msg.AlwaysAllow.from(msg)
                    fun given(a: List<String>) = a.filter { it.isNotBlank() && it != ASK_TOOL }
                    r.add?.let { s.alwaysAllow.addAll(given(it)) }
                        ?: r.tools?.let {
                            s.alwaysAllow.clear()
                            s.alwaysAllow.addAll(given(it))
                        }
                }
                changed()
                reply(Op.ALLOWED, "session" to s.id, "tools" to JSONArray(s.alwaysAllow.sorted()))
            }
            Op.BOT -> {
                live(s) {
                    val bot = Msg.Bot.from(msg).bot
                    if (bot == null) s.meta.put("bot", JSONObject.NULL)
                    else {
                        s.meta.put("bot", JSONObject().put("name", bot.name.orEmpty().trim().take(64).ifBlank { "Bot" })
                            .put("since", s.meta.optJSONObject("bot")?.optDouble("since") ?: now()))
                        if (msg.optJSONObject("bot")?.has("picture") == true) { // (a PNG, base64; "" or null to drop it;
                            val pic = picture(s)                                 // left out, kept)
                            val data = bot.picture.orEmpty()
                            if (data.isEmpty()) pic.delete()
                            else Base64.getDecoder().decode(data).takeIf { it.size <= WireConstants.BOT_PICTURE_MAX }?.let { pic.writeBytes(it) }
                        }
                    }
                    save(s)
                }
                changed()
                reply(Op.BOT_SET, "session" to listing(s))
            }
            Op.BOT_PICTURE -> reply(Op.BOT_PICTURE, "session" to s.id, "picture" to
                (picture(s).takeIf { it.exists() }?.let { Base64.getEncoder().encodeToString(it.readBytes()) } ?: JSONObject.NULL))
            Op.MEMORY -> {
                val file = File(s.folder, MEMORY)
                Msg.Memory.from(msg).text?.let { t -> live(s) { file.writeText(t) } }
                reply(Op.MEMORY, "session" to s.id, "text" to (if (file.exists()) file.readText() else ""),
                    "path" to MEMORY, "mtime" to if (file.exists()) file.lastModified() / 1000.0 else JSONObject.NULL)
            }
        }
    }

    /** A new agent: its folder made, a Bot if it's named, its prompt sent. Its model's one the mesh serves this phone
     * (Inference's value; none given, the first of them, as a host runs its own default). ask_timeout, ask_default and
     * spawned_by as a host's start has them (a subagent: Auto at most, meshtools.SUBAGENT_MODES). */
    private fun start(head: Head, msg: JSONObject, reply: (String, Array<out Pair<String, Any?>>) -> Unit) {
        val r = Msg.Start.from(msg)
        if (r.adopt != null)
            return reply(Op.ERROR, arrayOf("error" to "a chat on $name can't take up a conversation it doesn't have", "plain" to true))
        if (r.askDefault != null && r.askDefault !in setOf("deny", "allow"))
            return reply(Op.ERROR, arrayOf("error" to "ask_default is deny or allow"))
        // (a fork: a copy of one of its agents' conversations, under an id of its own; ephemeral, a side question's,
        // in Plan, kept nowhere and gone with its last head)
        val ephemeral = r.ephemeral == true
        val model = r.model.orEmpty()
        val src = r.forkFrom.orEmpty().takeIf(::isId)?.let { f ->
            session(f) ?: return reply(Op.ERROR, arrayOf("error" to Wording.HostErrors.noForkSource(session = f)))
        }
        val served = (if (model.isNotBlank()) Inference.of(model)
            else src?.meta?.optJSONObject("infer")?.let { Served.of(it) } ?: Inference.found().firstOrNull())
            ?: return reply(Op.ERROR, arrayOf("error" to "$name can't run ${model.ifBlank { "a model" }}: its chats " +
                "run the models the mesh's machines serve over an API, and none does now", "plain" to true))
        val sid = UUID.randomUUID().toString()
        val botName = r.bot?.let { it.name.orEmpty() }?.trim()?.take(64)?.ifBlank { null }?.takeIf { !ephemeral }
        val slug = (botName ?: if (src != null) "fork" else "chat").lowercase().replace(Regex("[^a-z0-9]+"), "-")
            .trim('-').take(24).ifBlank { "chat" }
        val folder = when { // (named as you'd say it: Baude lists a folder's chats under its name)
            ephemeral && src != null -> src.folder // (a side question looks where its chat is)
            ephemeral -> File(root, "ephemeral/$sid-files").apply { mkdirs() }
            else -> File(root, "agents/$slug-${sid.take(8)}").apply {
                if (src != null) src.folder.copyRecursively(this, overwrite = true) else mkdirs() // (a fork: its own copy)
            }
        }
        val spawnedBy = r.spawnedBy.orEmpty().takeIf(::isId)
        val asked = if (ephemeral) "plan" else r.mode.orEmpty().takeIf { m -> MODES.any { it.first == m } } ?: "default"
        val prompt = r.prompt.orEmpty()
        val meta = JSONObject().put("id", sid).put("cwd", folder.path).put("model", served.value)
            .put("infer", served.json()).put("title", r.title.orEmpty().trim().ifBlank { null } ?: JSONObject.NULL)
            .put("created", now()).put("said", now()).put("prompts", 0).put("archived", false)
            .put("draft", r.draft == true && prompt.isBlank())
            .put("mode", if (spawnedBy != null && asked !in SUBAGENT_MODES) "auto" else asked)
            .put("spawned_by", spawnedBy ?: JSONObject.NULL)
            .put("ask_timeout", r.askTimeout?.toDouble()?.takeIf { it > 0 } ?: JSONObject.NULL)
            .put("ask_default", if (r.askDefault == "allow") "allow" else "deny")
            .put("bot", if (botName != null) JSONObject().put("name", botName).put("since", now()) else JSONObject.NULL)
            .put("start_key", r.startKey.orEmpty().takeIf(::isId) ?: JSONObject.NULL)
            .put("fork_from", src?.id ?: JSONObject.NULL).put("ephemeral", ephemeral)
            .put("own_folder", !(ephemeral && src != null))
        if (src != null && !ephemeral) meta.put("title", r.title.orEmpty().trim().ifBlank { null }
            ?: src.meta.opt("title") ?: JSONObject.NULL)
        val s = PhoneSession(meta)
        if (botName != null && !File(folder, MEMORY).exists()) File(folder, MEMORY).writeText("")
        synchronized(lock) {
            if (src != null) { // (its conversation, and who its messages from others are from, copied)
                val f = transcript(src)
                if (f.exists()) transcript(s).writeText(f.readLines().joinToString("") { row ->
                    (try { JSONObject(row).put("sessionId", sid).toString() } catch (_: Exception) { row }) + "\n"
                })
                senders(src)?.let { have ->
                    s.senders = JSONObject(have.toString())
                    if (have.length() > 0) sendersFile(s).writeText(have.toString())
                }
                s.meta.put("senders", src.meta.optInt("senders")).put("prompts", src.meta.optInt("prompts"))
            }
            sessions[sid] = s
            save(s)
            if (r.attach == true) {
                s.viewers.add(head)
                s.live.add(head)
            }
        }
        reply(Op.STARTED, arrayOf("session" to listing(s), "forked" to (src != null)))
        if (prompt.isNotBlank()) input(head, s, JSONObject().put("type", "user").put("message", textMessage(prompt)),
            sentBy(head, r.`as`))
        changed()
    }

    private fun textMessage(text: String) = JSONObject().put("role", "user")
        .put("content", JSONArray().put(JSONObject().put("type", "text").put("text", text)))

    /** What a head sends an agent: a message of yours (a turn, or taken after the one going on), an answer to what it
     * asked, or one of claude's control requests (initialize: what it runs; interrupt: its turn stopped; the rest, as
     * a host answers them for an agent that isn't running: nothing to change). */
    private fun input(head: Head, s: PhoneSession, msg: JSONObject, sender: JSONObject? = null) {
        when (msg.optString("type")) {
            "user" -> {
                var message = msg.optJSONObject("message") ?: return
                // (a uuid each, as a host gives one: who it's from kept by it, if it's not from its user: Senders)
                val given = msg.optString("uuid").takeIf(::isId)
                val uuid = given ?: UUID.randomUUID().toString()
                if (sender != null) message = JSONObject(message.toString()).put("content", saidBy(sender, message.opt("content")))
                synchronized(lock) {
                    if (s.deleted) return
                    if (given != null && repeated(s, given)) return
                    if (sender != null) stamp(s, uuid, sender)
                    if (s.busy) {
                        s.queued.put(JSONObject().put("message", message).put("uuid", uuid))
                        s.queuedBy[uuid] = head
                        save(s)
                        return
                    }
                    tellSaid(s, message, uuid, head)
                    begin(s, listOf(message to uuid))
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
                fun answer(response: JSONObject?, error: String? = null) = head.send(Msg.Claude(session = s.id,
                    msg = JSONObject().put("type", "control_response").put("response",
                        if (error != null) JSONObject().put("subtype", "error").put("request_id", rid).put("error", error)
                        else JSONObject().put("subtype", "success").put("request_id", rid).put("response", response))).json())
                when (request.optString("subtype")) {
                    "initialize" -> answer(JSONObject().put("commands", JSONArray()).put("agents", JSONArray())
                        .put("models", JSONArray((listOf(s.meta.optString("model") to Inference.display(s)) +
                            Inference.found().filter { it.value != s.meta.optString("model") }.map { it.value to "${it.display} · ${it.memberName}" })
                            .map { (v, d) -> JSONObject().put("value", v).put("displayName", d) }))
                        .put("current_permission_mode", mode(s)).put("output_style", "default")
                        .put("available_output_styles", JSONArray().put("default")))
                    "interrupt" -> {
                        val running = s.busy
                        stopTurn(s)
                        answer(JSONObject().put("running", running))
                    }
                    "set_model" -> {
                        val want = if (request.isNull("model")) "" else request.optString("model")
                        val served = Inference.of(want)
                        when {
                            want.isEmpty() || want == s.meta.optString("model") -> answer(JSONObject())
                            served == null -> answer(null, "the mesh doesn't serve $want now")
                            else -> {
                                live(s) { // (its next call, the one after a turn's call going on, goes there)
                                    s.meta.put("model", served.value).put("infer", served.json())
                                    save(s)
                                }
                                changed()
                                answer(JSONObject())
                            }
                        }
                    }
                    "get_settings" -> answer(JSONObject().put("applied", JSONObject().put("model", s.meta.optString("model"))))
                    "set_permission_mode" -> if (MODES.any { it.first == request.optString("mode") }) {
                        setMode(s, request.optString("mode"))
                        answer(JSONObject())
                    } else answer(null, "no such permission mode: ${request.optString("mode")}")
                    "rewind_conversation" -> answer(null, "a chat on this phone can't be taken back to an earlier message yet")
                    else -> answer(JSONObject())
                }
            }
        }
    }

    /** (lock held) A turn begun: the messages kept in its transcript, and its agent run on a thread of its own. */
    private fun begin(s: PhoneSession, messages: List<Pair<JSONObject, String>>) {
        for ((m, uuid) in messages) {
            record(s, "user", m, uuid)
            s.meta.put("prompts", s.meta.optInt("prompts") + 1)
        }
        if (s.meta.isNull("title")) firstWords(messages.first().first)?.let { s.meta.put("title", it) }
        s.meta.put("said", now()).put("turn", true).put("draft", false).remove("cut")
        save(s)
        s.busy = true
        s.cancel = false
        changed()
        kotlin.concurrent.thread(name = "agent-${s.id.take(8)}", isDaemon = true) { PhoneAgent.turn(s) }
    }

    /** (lock held) A message given its agent, as it's kept (input; one sent at a turn, as the turn ends: ended), told
     * those attached live but [by], who sent it ({type: "_said"}), as its agent never says it back; with who it's from
     * if its sender store says, as a host tells it. */
    private fun tellSaid(s: PhoneSession, message: JSONObject, uuid: String, by: Head?) {
        val told = JSONObject().put("type", "_said").put("uuid", uuid).put("message", message)
        senders(s)?.optJSONObject(uuid)?.let { told.put("sender", it) }
        val said = Msg.Claude(session = s.id, msg = told).json()
        for (h in s.live) if (h !== by) h.send(said)
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
            s.cancel = true // (what was sent meanwhile: kept as said, as the turn ends: ended)
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
        val to = synchronized(lock) { if (s.deleted) return else s.live.toList() }
        for (h in to) h.send(Msg.Claude(session = s.id, msg = msg).json())
    }

    /** May the agent use a tool: as its chat's permission mode has it (PhoneTools.need), or one always allowed in this
     * chat (never a question for you, nor a plan), or its permission asked (claude's can_use_tool) and waited for. The
     * answer (behavior: allow, with updatedInput; or deny, with a message), or null if the turn's stopped first. */
    fun permit(s: PhoneSession, toolUseId: String, tool: String, input: JSONObject): JSONObject? {
        val need = if (editsMemory(s, tool, input)) "allow" else PhoneTools.need(mode(s), tool)
        val asked = tool == ASK_TOOL || tool == "ExitPlanMode"
        if (need == "allow" || !asked && mode(s) != "plan" && tool in synchronized(lock) { s.alwaysAllow })
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
        for (h in to) h.send(Msg.Claude(session = s.id, msg = ask).json())
        changed()
        s.meta.optDouble("ask_timeout").takeIf { it > 0 }?.let { timeout(s, rid, wait, input, it) }
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

    /** (an agent started with ask_timeout: a subagent's, say) An ask answered as its ask_default says once it's waited
     * that long with nobody attached who answers (someone's there: given as long again), as a host's ask_timer. */
    private fun timeout(s: PhoneSession, rid: String, wait: CompletableFuture<JSONObject?>, input: JSONObject, seconds: Double) {
        work.schedule({
            val due = synchronized(lock) {
                when {
                    rid !in s.asks -> null
                    s.viewers.any { it.answers } -> false
                    else -> true
                }
            }
            when (due) {
                false -> timeout(s, rid, wait, input, seconds)
                true -> wait.complete(if (s.meta.optString("ask_default") == "allow")
                    JSONObject().put("behavior", "allow").put("updatedInput", input)
                    else JSONObject().put("behavior", "deny").put("message", "Nobody answered within ${if (seconds % 1.0 == 0.0) seconds.toLong() else seconds} s."))
                null -> {}
            }
        }, (seconds * 1000).toLong(), TimeUnit.MILLISECONDS)
    }

    /** Is a call a Bot's edit of its own memory (memory.md, in its folder): never asked, as a host's Bot's isn't
     * (edits_memory)? */
    private fun editsMemory(s: PhoneSession, tool: String, input: JSONObject): Boolean {
        if (s.meta.isNull("bot") || tool !in setOf("Write", "Edit")) return false
        val path = input.optString("file_path").ifBlank { return false }
        val f = (if (path.startsWith("/")) File(path) else File(s.folder, path)).canonicalFile
        return f == File(s.folder, MEMORY).canonicalFile
    }

    /** Its turn's over: its result said (to those attached live, and as turn_ended to every head subscribed), and what
     * was sent meanwhile taken now, in a turn of its own (in the same hold of the lock, so no other turn starts
     * between). */
    fun ended(s: PhoneSession, result: String, error: Boolean, turns: Int, ms: Long) {
        val stopped = s.cancel
        tell(s, JSONObject().put("type", "result").put("subtype", if (error) "error_during_execution" else "success")
            .put("is_error", error).put("result", result).put("num_turns", turns).put("duration_ms", ms)
            .put("total_cost_usd", 0).put("session_id", s.id).put("usage", JSONObject()))
        val subscribed = synchronized(lock) {
            s.acting = false
            s.asks.clear()
            s.stopModel = null
            val next = s.queued.list().mapNotNull { q -> q.optJSONObject("message")?.let { it to q.optString("uuid") } }
            s.meta.remove("queued")
            if (!s.deleted) for ((m, uuid) in next) tellSaid(s, m, uuid, s.queuedBy[uuid])
            s.queuedBy.clear()
            if (stopped) for ((m, uuid) in next) record(s, "user", m, uuid) // (sent as it was stopped: said, not taken)
            if (next.isNotEmpty() && !stopped && !s.deleted && sessions[s.id] === s) {
                begin(s, next)
            } else {
                s.busy = false
                if (!s.deleted) {
                    s.meta.put("turn", false)
                    save(s)
                }
            }
            if (s.deleted) emptyList() else heads.filter { it.subscribed && (it.archived || !s.meta.optBoolean("archived")) }
        }
        val end = Msg.TurnEnded(session = s.id, result = result, isError = error, cost = 0, turns = turns,
            durationMs = ms).json()
        for (h in subscribed) h.send(end)
        changed()
    }

    fun transcript(s: PhoneSession) = File(root, (if (s.meta.optBoolean("ephemeral")) "ephemeral/" else "transcripts/") + "${s.id}.jsonl")

    /** (lock held) A record of its transcript, as Claude Code writes one (what heads read), added to the conversation
     * kept for its model too: its uuid; null for an agent deleted. */
    private fun record(s: PhoneSession, kind: String, message: JSONObject, uuid: String?, apiError: Boolean = false): String? {
        if (s.deleted) return null
        val id = uuid ?: UUID.randomUUID().toString()
        val rec = JSONObject().put("parentUuid", JSONObject.NULL).put("isSidechain", false).put("type", kind)
            .put("uuid", id).put("timestamp", java.time.Instant.now().toString()).put("sessionId", s.id)
            .put("cwd", s.folder.path).put("entrypoint", "baude-android").put("message", message)
        if (apiError) rec.put("isApiErrorMessage", true)
        return keep(s, rec)
    }

    /** (lock held) A record kept, as it is, and added to the conversation: its uuid; null for an agent deleted. */
    private fun keep(s: PhoneSession, rec: JSONObject): String? {
        if (s.deleted) return null
        transcript(s).appendText(rec.toString() + "\n")
        s.convo?.let { c -> PhoneAgent.add(c, rec) }
        return rec.optString("uuid")
    }

    /** Its conversation compacted, as claude compacts one: a compact_boundary, then the summary it wrote (isCompactSummary),
     * kept, and said to those attached live as claude says them (its status, the boundary, the summary). The
     * conversation its model's given from now on starts from the summary. */
    fun compacted(s: PhoneSession, summary: String, preTokens: Int) {
        val at = java.time.Instant.now().toString()
        synchronized(lock) {
            if (s.deleted) return
            keep(s, JSONObject().put("parentUuid", JSONObject.NULL).put("isSidechain", false).put("type", "system")
                .put("subtype", "compact_boundary").put("content", "Conversation compacted").put("isMeta", false)
                .put("uuid", UUID.randomUUID().toString()).put("timestamp", at).put("sessionId", s.id)
                .put("cwd", s.folder.path).put("level", "info")
                .put("compactMetadata", JSONObject().put("trigger", "auto").put("preTokens", preTokens)))
            keep(s, JSONObject().put("parentUuid", JSONObject.NULL).put("isSidechain", false).put("type", "user")
                .put("isCompactSummary", true).put("isVisibleInTranscriptOnly", true)
                .put("uuid", UUID.randomUUID().toString()).put("timestamp", at).put("sessionId", s.id)
                .put("cwd", s.folder.path).put("message", JSONObject().put("role", "user").put("content", summary)))
        }
        tell(s, JSONObject().put("type", "system").put("subtype", "status").put("status", JSONObject.NULL)
            .put("compact_result", "success").put("session_id", s.id))
        tell(s, JSONObject().put("type", "system").put("subtype", "compact_boundary").put("session_id", s.id)
            .put("compact_metadata", JSONObject().put("trigger", "auto").put("pre_tokens", preTokens)))
        tell(s, JSONObject().put("type", "user").put("isSynthetic", true).put("session_id", s.id)
            .put("message", JSONObject().put("role", "user").put("content", summary)))
        changed()
    }

    /** Whether a message by uuid was taken already (a head sending it again, its connection gone as it first sent
     * it), as a host has it: if not, noted, the last TAKEN kept in its meta. */
    private fun repeated(s: PhoneSession, uuid: String): Boolean {
        val taken = s.meta.optJSONArray("taken") ?: JSONArray().also { s.meta.put("taken", it) }
        if ((0 until taken.length()).any { taken.optString(it) == uuid }) return true
        taken.put(uuid)
        while (taken.length() > TAKEN) taken.remove(0)
        save(s)
        return false
    }

    private fun save(s: PhoneSession) {
        if (s.deleted || s.meta.optBoolean("ephemeral")) return // (a side question's: kept nowhere)
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
        .put("host", name).put("home", if (::root.isInitialized) File(root, "agents").path else "")
        .put("pid", android.os.Process.myPid()).put("groups", JSONArray().put(name)).put("init", JSONObject.NULL)
        .put("commit", JSONObject.NULL).put("update", JSONObject.NULL).put("providers", JSONArray())
        .put("phone", true)
        .put("mesh", JSONObject().put("members", JSONArray()).put("links", JSONArray()).put("proxy", JSONObject.NULL)
            .put("inactive", JSONArray()).put("found", JSONArray()).put("gone", JSONArray()))

    fun listing(s: PhoneSession): JSONObject = synchronized(lock) {
        val f = transcript(s)
        val size = if (f.exists()) f.length() else 0L
        val bot = s.meta.optJSONObject("bot")
        JSONObject().put("id", s.id).put("title", s.meta.opt("title") ?: JSONObject.NULL)
            .put("cwd", s.folder.path).put("model", s.meta.optString("model")).put("effort", JSONObject.NULL)
            .put("mode", mode(s)).put("group", name).put("created", s.meta.optDouble("created"))
            .put("said", s.meta.optDouble("said")).put("updated", if (f.exists()) f.lastModified() / 1000.0 else s.meta.optDouble("created"))
            .put("size", size).put("state", if (s.asks.isNotEmpty()) "waiting" else if (s.busy) "working" else "idle") // (between turns: ready, nothing to resume)
            .put("prompts", s.meta.optInt("prompts")).put("cli_id", JSONObject.NULL).put("transcript", JSONObject.NULL)
            .put("archived", s.meta.optBoolean("archived")).put("draft", s.meta.optBoolean("draft")).put("written", size)
            .put("subagents", 0).put("commands", JSONArray()).put("cut", s.meta.opt("cut") ?: JSONObject.NULL)
            .put("bot", if (bot == null) JSONObject.NULL else JSONObject().put("name", bot.optString("name"))
                .put("since", bot.optDouble("since"))
                .put("picture", picture(s).takeIf { it.exists() }?.let { it.lastModified() / 1000.0 } ?: JSONObject.NULL))
            .put("acting", s.acting && s.busy && s.asks.isEmpty()).put("fork_from", JSONObject.NULL)
            .put("spawned_by", s.meta.opt("spawned_by") ?: JSONObject.NULL).put("start_key", s.meta.opt("start_key") ?: JSONObject.NULL)
            .put("always_allow", JSONArray(s.alwaysAllow.sorted()))
            .put("held", JSONArray()).put("senders", s.meta.optInt("senders"))
            .put("asks", JSONArray(s.asks.entries.map { (rid, m) ->
                val r = m.optJSONObject("request") ?: JSONObject()
                JSONObject().put("request_id", rid).put("tool", r.optString("tool_name")).put("input", r.optJSONObject("input"))
            }))
    }

    private fun listing(archived: Boolean): JSONArray = synchronized(lock) {
        JSONArray(sessions.values.filter { (archived || !it.meta.optBoolean("archived")) && !it.meta.optBoolean("ephemeral") }
            .map { listing(it) }
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
            for (h in to) h.send(JSONObject().put("op", Op.SESSIONS).put("sessions", listing(h.archived)).put("server", info))
            main.post { Mesh.tick.value++ }
        }, 80, TimeUnit.MILLISECONDS)
    }

    fun session(sid: String): PhoneSession? = synchronized(lock) { sessions[sid] }

    private fun picture(s: PhoneSession) = File(root, "pictures/${s.id}")

    // ---- who a message is from, if not its user (wire.py's Senders)

    private fun sendersFile(s: PhoneSession) = if (s.meta.optBoolean("ephemeral")) File(root, "ephemeral/${s.id}.senders.json")
        else File(root, "senders/${s.id}.json")

    /** Its sender store, {uuid: sender} (read the first time it's wanted); null if it's there and can't be read (read
     * again the next time, and never written over meanwhile), as server.senders has it. */
    private fun senders(s: PhoneSession): JSONObject? = synchronized(lock) {
        s.senders ?: run {
            val f = sendersFile(s)
            if (!f.exists()) return@run JSONObject().also { s.senders = it }
            try {
                JSONObject(f.readText()).also { s.senders = it }
            } catch (_: Exception) {
                android.util.Log.w("baude", "${s.id}: its sender store couldn't be read")
                null
            }
        }
    }

    /** (lock held) A message passed to an agent from another than its user: who it's from kept by its uuid, the
     * store's revision moved on. */
    private fun stamp(s: PhoneSession, uuid: String, sender: JSONObject) {
        val have = senders(s)?.put(uuid, sender) ?: return // (unreadable: never written over)
        sendersFile(s).writeText(have.toString())
        s.meta.put("senders", s.meta.optInt("senders") + 1)
        save(s)
        changed()
    }

    /** Who a request passing a message in (input, start) is from, as server.sent_by has it: through a tunnel, the
     * chat its relay named (else the one its as names) on the member it came from; here, the agent of this phone's its
     * as names; null for the agent's user. */
    private fun sentBy(head: Head, asked: String?): JSONObject? {
        val asId = asked.orEmpty().takeIf(::isId)
        val origin = head.origin ?: return asId?.let { chatSender(it) }
        val said = head.sender ?: asId?.let { JSONObject().put("kind", "chat").put("session", it) } ?: return null
        val name = PhoneLink.nameOf(origin)
        return cleanSender(JSONObject(said.toString()).put("host", origin)
            .put("host_name", if (name == origin.take(8)) said.optString("host_name") else name))
    }

    /** One of this phone's agents as a message from it is from: a Bot by its name, else by its title. */
    fun chatSender(sid: String): JSONObject? {
        val s = session(sid) ?: return null
        val bot = s.meta.optJSONObject("bot")?.optString("name")
        return cleanSender(JSONObject().put("kind", if (bot != null) "bot" else "chat")
            .put("name", bot ?: s.meta.opt("title")?.toString()).put("host", id).put("host_name", name).put("session", sid))
    }

    /** wire.clean_sender: {kind, name, host, host_name, session}, words, cut short; null if it isn't one. */
    fun cleanSender(o: JSONObject?): JSONObject? {
        o ?: return null
        val out = JSONObject()
        for (k in listOf("name", "host", "host_name", "session")) {
            val v = if (o.isNull(k)) "" else o.optString(k)
            if (v.isNotBlank()) out.put(k, v.trim().split(Regex("\\s+")).joinToString(" ").take(120))
        }
        return out.put("kind", o.optString("kind").takeIf { it in setOf("bot", "chat", "subagent", "host") } ?: "host")
    }

    /** wire.sender_name: who a sender is, as a message from it's headed. */
    private fun senderName(s: JSONObject): String {
        val n = s.optString("name")
        val who = when (s.optString("kind")) {
            "bot" -> "${n.ifBlank { "a Bot" }}, a Bot"
            "chat" -> if (n.isNotBlank()) "“$n”" else "a chat"
            "subagent" -> if (n.isNotBlank()) "subagent “$n”" else "a subagent"
            else -> n.ifBlank { "a host" }
        }
        val where = s.optString("host_name")
        return who + if (where.isNotBlank() && s.optString("kind") != "host") " on $where" else ""
    }

    /** wire.said_by: a message's content as from sender, for its agent: a tag at its head says so. (Heads draw who
     * it's from from the store, never from this.) */
    private fun saidBy(sender: JSONObject, content: Any?): Any {
        fun esc(v: String, quote: Boolean) = v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .let { if (quote) it.replace("\"", "&quot;").replace("'", "&#x27;") else it }
        val attrs = sender.keys().asSequence().joinToString("") { k -> " $k=\"${esc(sender.optString(k), true)}\"" }
        val head = "<baude-sender$attrs>A message from ${esc(senderName(sender), false)}, not from your user</baude-sender>\n\n"
        if (content is String) return head + content
        val blocks = (content as? JSONArray)?.let { JSONArray(it.toString()) } ?: JSONArray()
        val at = (0 until blocks.length()).firstOrNull { blocks.optJSONObject(it)?.optString("type") == "text" }
            ?: return JSONArray().put(JSONObject().put("type", "text").put("text", head.trimEnd())).apply {
                for (i in 0 until blocks.length()) put(blocks.get(i))
            }
        blocks.getJSONObject(at).put("text", head + blocks.getJSONObject(at).optString("text"))
        return blocks
    }

    /** A file in one of its agents' folders, as a host's read_file serves one in a session's folder:
     * {path, size, data (base64), error?}. */
    private fun readFile(msg: JSONObject): Array<Pair<String, Any?>> {
        val r = Msg.ReadFile.from(msg)
        val path = r.path.orEmpty()
        val f = File(path).takeIf { path.startsWith("/") }?.canonicalFile ?: return arrayOf("path" to path, "error" to Wording.HostErrors.notAbsolute)
        val inside = synchronized(lock) { sessions.values.any { f.path.startsWith(it.folder.canonicalPath + File.separator) } }
        if (!inside) return arrayOf("path" to path, "error" to Wording.HostErrors.notInSessionFolder)
        if (!f.isFile) return arrayOf("path" to path, "error" to "No such file")
        val limit = (r.limit?.toInt() ?: FILE_LIMIT).coerceIn(1, FILE_LIMIT)
        val data = f.inputStream().use { it.readUpTo(limit + 1) }
        return arrayOf("path" to path, "size" to f.length(), "data" to Base64.getEncoder().encodeToString(data))
    }

    /** A folder in one of its agents' folders, as a host's list_dir has it: {path, entries: [{name, dir, size?,
     * ignored}], more}, folders first. */
    private fun listDir(path: String): Array<Pair<String, Any?>> {
        val dir = File(path).takeIf { path.startsWith("/") }?.canonicalFile
        val ok = dir != null && synchronized(lock) {
            sessions.values.any { dir == it.folder.canonicalFile || dir.path.startsWith(it.folder.canonicalPath + File.separator) }
        }
        if (!ok) return arrayOf("path" to path, "entries" to JSONArray(), "error" to Wording.HostErrors.notInSessionFolder)
        val all = dir!!.listFiles().orEmpty().filter { it.name != ".git" }
            .sortedWith(compareBy({ !it.isDirectory }, { it.name.lowercase() }))
        val entries = JSONArray(all.take(DIR_LIMIT).map { f ->
            JSONObject().put("name", f.name).put("dir", f.isDirectory).put("ignored", false)
                .apply { if (!f.isDirectory) put("size", f.length()) }
        })
        return arrayOf("path" to path, "entries" to entries, "more" to (all.size > DIR_LIMIT))
    }

    /** What @ lists for one of its agents' folders, as a host's catalogue (lookup.items' shape): its files. */
    private fun catalogue(cwd: String): Array<Pair<String, Any?>> {
        val dir = File(cwd).takeIf { cwd.startsWith("/") }?.canonicalFile
        val ok = dir != null && synchronized(lock) { sessions.values.any { it.folder.canonicalFile == dir } }
        if (!ok) return arrayOf("cwd" to cwd, "items" to JSONArray(), "error" to Wording.HostErrors.notSessionFolder)
        val items = JSONArray()
        for (f in dir!!.walkTopDown().filter { it.isFile }.take(CATALOGUE_LIMIT)) {
            val rel = f.relativeTo(dir).path
            items.put(JSONObject().put("surface", rel).put("canonical", "").put("faint", false).put("icon", "file")
                .put("what", Wording.Lookup.file).put("summary", rel).put("kind", "file").put("line", JSONObject.NULL).put("path", f.path)
                .put("entry", JSONObject().put("kind", "file").put("sources", JSONArray().put(JSONArray().put("File").put(rel)))))
        }
        return arrayOf("cwd" to cwd, "items" to items, "busy" to false)
    }

    /** (lock held) An agent moved to the phone's trash (phone/trash/<id>: its meta, transcript, folder and picture),
     * where it can be put back (putBack), as a host moves a deleted chat's files to the trash: where they went. */
    private fun trash(s: PhoneSession): List<String> {
        val bin = File(root, "trash/${s.id}").apply { mkdirs() }
        val went = ArrayList<String>()
        s.meta.put("trashed", now()).remove("queued")
        File(bin, "meta.json").writeText(s.meta.toString())
        File(root, "sessions/${s.id}.json").delete()
        for ((from, name) in listOf(transcript(s) to "transcript.jsonl", s.folder to "folder", picture(s) to "picture",
                sendersFile(s) to "senders.json")) {
            if (from.exists() && from.renameTo(File(bin, name))) went.add(File(bin, name).path)
        }
        return went
    }

    /** The agents in the phone's trash: their meta, newest first. */
    fun trashed(): List<JSONObject> = File(root, "trash").listFiles().orEmpty().mapNotNull { d ->
        try {
            JSONObject(File(d, "meta.json").readText())
        } catch (_: Exception) {
            null
        }
    }.sortedByDescending { it.optDouble("trashed") }

    /** One put back from the trash: its files where they were, and it listed again. */
    fun putBack(sid: String) {
        work.execute {
            val bin = File(root, "trash/$sid")
            val meta = try {
                JSONObject(File(bin, "meta.json").readText())
            } catch (_: Exception) {
                return@execute
            }
            meta.remove("trashed")
            val s = PhoneSession(meta)
            File(bin, "transcript.jsonl").renameTo(transcript(s))
            File(bin, "folder").renameTo(s.folder)
            File(bin, "picture").renameTo(picture(s))
            File(bin, "senders.json").renameTo(sendersFile(s))
            synchronized(lock) {
                sessions[s.id] = s
                save(s)
            }
            bin.deleteRecursively()
            changed()
        }
    }

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
    private const val FILE_LIMIT = 4 shl 20 // (serverstate.FILE_LIMIT)
    private const val TAKEN = 64 // (serverstate.TAKEN)
    private const val CATALOGUE_LIMIT = 5000
    private const val DIR_LIMIT = 2000 // (hostfiles.DIR_LIMIT)
    private val SUBAGENT_MODES = setOf("auto", "acceptEdits", "default", "plan") // (meshtools.SUBAGENT_MODES)
    const val MEMORY = "memory.md" // (a Bot's memory, in its own folder: its file tools edit it)
}
