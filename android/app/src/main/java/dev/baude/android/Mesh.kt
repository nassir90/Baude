package dev.baude.android

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.graphics.BitmapFactory
import android.os.Build
import android.os.Handler
import android.os.Looper
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.jcraft.jsch.JSch
import org.json.JSONArray
import org.json.JSONObject
import java.util.Base64

/** A Bot, as a contact: its chat (session) on its host. */
data class Contact(val hostId: String, val session: JSONObject) {
    val id: String get() = session.getString("id")
    val name: String get() = session.optJSONObject("bot")?.optString("name")?.ifBlank { null } ?: "Bot"
    val said: Double get() = session.optDouble("said", session.optDouble("updated", 0.0))
    val state: String get() = session.optString("state", "stopped")
    val asking: Boolean get() = (session.optJSONArray("asks")?.length() ?: 0) > 0
    /** Whether "always allow" is offered for what it asks, as Baude offers it: for Claude Code's models only (another
     * program's asks each call: models.model_program). */
    val alwaysOffered: Boolean
        get() = hostId == "phone" || session.optString("model").let { m -> // (this phone's own agents: as claude's)
            !(m.startsWith("codex/") || m == "apple" || m.startsWith("ollama/") || m.startsWith("openai/") ||
                m.startsWith("agent/") || m.startsWith("mlx-"))
        }
    val pictureKey: String get() = "$hostId/$id/${session.optJSONObject("bot")?.opt("picture")}"
}

/** One line of a chat as it's shown: yours, the Bot's, what it did (a tool it used), or a note (a background task
 * ended, a turn stopped, the conversation compacted, an API error). Two that aren't shown: "record", the end of a
 * transcript record (uuid: its uuid, shown or not), and "rewind", a revert's mark (uuid: the record it keeps last;
 * none, none kept). */
data class Line(val kind: String, val text: String, val uuid: String? = null)

/** A permission its Bot asks, not answered yet: claude's control_request (can_use_tool) for request_id. seen: its
 * host has listed it (so a listing without it means it's been answered, or its claude's gone). */
class Ask(val rid: String, val tool: String, val input: JSONObject, val toolUseId: String?) {
    val at = android.os.SystemClock.elapsedRealtime()
    var seen = false

    companion object {
        fun of(msg: JSONObject): Ask? {
            if (msg.optString("type") != "control_request") return null
            val req = msg.optJSONObject("request") ?: return null
            if (req.optString("subtype") != "can_use_tool") return null
            val rid = msg.optString("request_id").ifBlank { return null }
            return Ask(rid, req.optString("tool_name"), req.optJSONObject("input") ?: JSONObject(),
                if (req.isNull("tool_use_id")) null else req.optString("tool_use_id"))
        }
    }
}

/** A chat open: its lines, read from its transcript on its host (more read as it grows), what's sent to it. hostId:
 * the connection it's on (a member's, till that machine's reached as a host of yours: then that host's). */
class Chat(hostId: String, val sid: String) {
    var hostId by mutableStateOf(hostId)
    val lines = mutableStateListOf<Line>()
    val files = mutableStateListOf<Picked>() // (picked to send: kept as the phone's turned)
    val preparing = mutableStateOf(0)         // (picked, still being read)
    val ends = HashMap<String, Int>()         // (a record's uuid: how many lines were shown up to its end)
    val busy = mutableStateOf(false)
    val said = mutableStateOf<String?>(null)
    val streaming = mutableStateOf("") // (what its agent's writing now, as it comes: till its message is whole)
    val asks = mutableStateListOf<Ask>()      // (what its Bot asks, answered here: Allow, Deny)
    val fetched = HashSet<String>()           // (asks its host listed that weren't here: fetched again, once)
    var offset = -1L
    var reading = false
    var again = false
}

/** The mesh as this app has it: your hosts, a connection to each, their Bots, the chat open, notifications as a Bot
 * says something. One for the app (the activity and MeshService share it); its state's Compose's, changed on the main
 * thread. */
object Mesh {
    lateinit var app: Context
    private val main = Handler(Looper.getMainLooper())
    val hosts = mutableStateListOf<Host>()
    val states = mutableStateMapOf<String, Connection>() // (host id: its connection, whose state is read)
    val tick = mutableStateOf(0) // (a connection's state changed: what reads it drawn again)
    val sessions = mutableStateMapOf<String, List<JSONObject>>() // (a connection's id: its host's sessions)
    val pictures = mutableStateMapOf<String, ImageBitmap?>()
    val key = mutableStateOf<Key?>(null)
    val keyError = mutableStateOf<String?>(null) // (why the Keystore's key couldn't be read, or made: none made then)
    val making = mutableStateOf(false) // (a key being made, off the main thread)
    val members = mutableStateMapOf<String, JSONObject>() // (a member of a host's mesh, by its id: its record, as the
    // host it's reached through lists it: id, name, host, hops, through)
    @Volatile private var routes = emptyMap<String, Connection>() // (a member: the host that's up and reaches it in
    // fewest hops)
    private val allowed = HashMap<String, MutableSet<String>>() // (a chat: the tools you've always allowed it here)
    private val listedBy = HashMap<String, MutableSet<String>>() // (a host of yours: the members it's listed)
    private val moved = HashMap<String, String>() // (a member's connection id: the host of yours its machine is now)
    private val askNotes = HashMap<String, MutableSet<String>>() // (a chat, "connection/session": the asks notified)
    var open: Chat? = null
    var shown = false // (the app in front: no notification for the chat open)
    private var visible = false // (the app on screen: the chat open answers what it asks)
    private val conns = HashMap<String, Connection>()
    private val catchUp = HashSet<String>() // (hosts back after a drop: Bots' turns that ended meanwhile, notified)
    private var started = false

    fun start(ctx: Context) {
        if (started) return
        started = true
        app = ctx.applicationContext
        readKey()
        hosts.addAll(HostStore.load(app))
        channel()
        hosts.forEach { connect(it) }
        start(PhoneHost.connection(app, ::heard) { conn -> main.post { changed(conn) } }) // (this phone's own agents)
        try { // (the network back: each host that's down tried again at once)
            app.getSystemService(android.net.ConnectivityManager::class.java).registerDefaultNetworkCallback(
                object : android.net.ConnectivityManager.NetworkCallback() {
                    override fun onAvailable(network: android.net.Network) {
                        main.post { conns.values.forEach { it.kick() } }
                    }
                })
        } catch (_: Exception) {
        }
    }

    // ---- hosts

    /** The Keystore's key read (again): none, or why it couldn't be read, which isn't the same (it's never made anew
     * for that). */
    fun readKey() {
        try {
            key.value = Keys.load()
            keyError.value = null
        } catch (e: Exception) {
            keyError.value = "This phone's key couldn't be read from the Keystore: ${e.message ?: e.javaClass.simpleName}"
        }
    }

    /** A new key made, off the main thread, and each host tried again with it. */
    fun makeKey() {
        if (making.value) return
        making.value = true
        kotlin.concurrent.thread(name = "make-key", isDaemon = true) {
            val made = runCatching { Keys.make() }
            main.post {
                making.value = false
                made.onSuccess {
                    key.value = it
                    keyError.value = null
                    hosts.forEach { h -> connect(h) }
                }.onFailure { keyError.value = "A key couldn't be made: ${it.message ?: it.javaClass.simpleName}" }
            }
        }
    }

    /** What this phone signs in with: its Keystore key. */
    private fun identify(jsch: JSch, failed: (String) -> Unit) {
        key.value?.let { jsch.addIdentity(it.identity(failed), null) }
    }

    fun addHost(host: Host) {
        hosts.add(host)
        HostStore.save(app, hosts)
        connect(host)
        MeshService.keep(app)
    }

    fun removeHost(host: Host) {
        hosts.removeAll { it.id == host.id }
        HostStore.save(app, hosts)
        if (hosts.none { it.address == host.address && it.port == host.port }) HostStore.forget(app, host)
        conns.remove(host.id)?.stop()
        states.remove(host.id)
        sessions.remove(host.id)
        listedBy.remove(host.id)
        moved.values.removeAll { it == host.id }
        meshed()
        if (hosts.isEmpty()) app.stopService(Intent(app, MeshService::class.java))
    }

    fun connect(host: Host) {
        conns.remove(host.id)?.stop()
        if (key.value == null) return
        start(Connection(app, Place.Direct(host), ::identify, { routes[it] }, ::heard) { conn -> main.post { changed(conn) } })
    }

    private fun start(c: Connection) {
        conns[c.id] = c
        states[c.id] = c
        c.start()
    }

    /** Where a connection's id is, as you'd say it: a host's name, or a member's. */
    fun label(id: String): String {
        conns[id]?.host?.let { return it.label }
        val m = members[id.removePrefix("mesh:")]
        return m?.optString("name")?.ifBlank { null } ?: conns[id]?.info?.optString("name")?.ifBlank { null } ?: "a host"
    }

    /** (a host's info changed, or whether it's up) Each member of its mesh reached through it, as Baude reaches each
     * member through its own machine's host process (client.Network.meshed): one that's a host of yours already
     * reached so, not through another; each through the host that's up and lists it in fewest hops; one that's no
     * longer listed by any kept, as it was last seen, till one lists it again, and let go of once no host of yours
     * has listed it. A machine whose key is waiting on you (not trusted yet, or changed) isn't reached through
     * another either, till you've said. */
    private fun meshed() {
        val known = HashMap<String, Host>() // (machines that are hosts of yours: reached so)
        val held = ArrayList<Host>()        // (hosts whose key is waiting on you)
        for (h in hosts) {
            val c = conns[h.id]
            (c?.info?.optString("id")?.takeIf(::isId) ?: HostStore.machine(app, h))?.let { known.putIfAbsent(it, h) }
            if (c?.state == "key") held.add(h)
        }
        fun heldBy(m: JSONObject?): Host? = if (m == null) null else held.firstOrNull { h -> // (its machine, by its id
            HostStore.machine(app, h) == m.optString("id") || h.address.lowercase() in listOf("host", "bonjour", "name")
                .mapNotNull { m.optString(it).lowercase().ifBlank { null } }.flatMap { listOf(it, "$it.local") }
        }                                                                    // or the names its record gives it)
        val best = HashMap<String, Pair<JSONObject, Connection>>()
        for (h in hosts) {
            val c = conns[h.id]?.takeIf { it.state == "up" } ?: continue
            val seen = listedBy.getOrPut(h.id) { HashSet() }
            for (m in c.info?.optJSONObject("mesh")?.optJSONArray("members").list()) {
                val id = m.optString("id").takeIf(::isId) ?: continue
                seen.add(id)
                if (id in known || heldBy(m) != null || id == PhoneHost.id) continue // (this phone, as the mesh has it)
                val now = best[id]
                if (now == null || m.optInt("hops", 99) < now.first.optInt("hops", 99)) best[id] = m to c
            }
        }
        routes = best.mapValues { it.value.second }
        for ((id, pair) in best) {
            members[id] = JSONObject(pair.first.toString()).put("route", pair.second.host?.label)
            val c = conns["mesh:$id"]
            if (c == null) {
                moved.remove("mesh:$id") // (reached through the mesh again: its chats here, not a host's)
                start(Connection(app, Place.Member(id), ::identify, { routes[it] }, ::heard) { conn ->
                    main.post { changed(conn) }
                })
                continue
            }
            val back = !c.listed
            c.listed = true
            if (back) c.kick()
        }
        val kept = hosts.flatMap { listedBy[it.id].orEmpty() }.toSet()
        for (c in conns.values.filter { it.place is Place.Member }.toList()) {
            val id = (c.place as Place.Member).member
            val mine = known[id] ?: heldBy(members[id]) // (a host of yours: reached so, or not till you've said of its
            when {                                     // key; the chat open on it moved there)
                mine != null -> {
                    moved[c.id] = mine.id
                    open?.let { chat ->
                        if (chat.hostId != c.id) return@let
                        chat.hostId = mine.id
                        attach(chat)
                        read(chat)
                    }
                    drop(c)
                }
                id !in kept -> drop(c) // (no host of yours lists it any more)
                id !in best -> c.listed = false
            }
        }
    }

    /** A member's connection let go of, and its Bots with it. */
    private fun drop(c: Connection) {
        c.stop()
        conns.remove(c.id)
        states.remove(c.id)
        sessions.remove(c.id)
        members.remove(c.id.removePrefix("mesh:"))
        tick.value++
    }

    /** Hosts whose key you've said not to trust ("host id fingerprint"), this run: not asked again till you tap one. */
    val declined = mutableStateListOf<String>()

    /** The key a host showed trusted (you've compared its fingerprint), and the host tried again. */
    fun trust(host: Host, shown: ShownKey) {
        HostStore.trust(app, host, shown.type, shown.blob)
        hosts.filter { it.address == host.address && it.port == host.port }.forEach { connect(it) } // (each that's
    }                                                                                              // there: its key)

    private fun changed(c: Connection) {
        if (conns[c.id] !== c) return
        tick.value++
        if (c.state == "up") {
            c.host?.let { h -> c.info?.optString("id")?.takeIf(::isId)?.let { HostStore.setMachine(app, h, it) } }
            if (sessions[c.id] != null) catchUp.add(c.id)
            open?.let { if (it.hostId == c.id) attach(it) } // (read once its sessions are listed: their sizes)
        }
        if (c.place is Place.Direct) {
            meshed()
            PhoneLink.look()
        }
    }

    fun conn(hostId: String): Connection? = conns[hostId]

    // ---- what the hosts say

    private fun heard(c: Connection, msg: JSONObject) {
        main.post {
            if (conns[c.id] !== c) return@post
            when (msg.optString("op")) {
                "sessions" -> {
                    val now = msg.optJSONArray("sessions").list().filter { !it.optBoolean("draft") }
                    val before = sessions[c.id]
                    sessions[c.id] = now
                    msg.optJSONObject("server")?.let { // (its mesh as it is now)
                        c.info = it
                        if (c.place is Place.Direct) meshed()
                    }
                    if (catchUp.remove(c.id) && before != null) missed(c, before, now)
                    asked(c, before, now)
                    open?.let { if (it.hostId == c.id) read(it) } // (its first read, or what it missed)
                }
                "turn_ended" -> ended(c, msg)
                "claude" -> open?.let {
                    if (it.hostId != c.id || it.sid != msg.optString("session")) return@let
                    streamed(it, msg.optJSONObject("msg"))
                    if (msg.optJSONObject("msg")?.optString("type") == "stream_event") return@let // (its text: no read)
                    val ask = msg.optJSONObject("msg")?.let(Ask::of)
                    if (ask != null && it.asks.none { a -> a.rid == ask.rid }) it.asks.add(ask)
                    read(it)
                }
            }
        }
    }

    /** What an agent's writing, shown as it comes (claude's stream_event: its text's deltas), and gone once its
     * message is whole (what its transcript has, read). */
    private fun streamed(chat: Chat, m: JSONObject?) {
        m ?: return
        if (m.optString("type") != "stream_event") {
            if (m.optString("type") in setOf("assistant", "result")) chat.streaming.value = ""
            return
        }
        if (!m.isNull("parent_tool_use_id")) return
        val e = m.optJSONObject("event") ?: return
        when (e.optString("type")) {
            "message_start" -> chat.streaming.value = ""
            "content_block_delta" -> e.optJSONObject("delta")?.takeIf { it.optString("type") == "text_delta" }?.let {
                chat.streaming.value += it.optString("text")
            }
        }
    }

    /** (a host listed its sessions) The open chat's asks kept as its host has them: gone at once once answered
     * (here, in Baude, or its claude's gone), and fetched again (attach) if it lists one that isn't here. A Bot that's
     * started asking, out of sight, notified once for each ask, and the notification taken back once it's answered,
     * wherever. */
    private fun asked(c: Connection, before: List<JSONObject>?, now: List<JSONObject>) {
        fun rids(s: JSONObject?) = s?.optJSONArray("asks").list().map { it.optString("request_id") }.toSet()
        open?.let { chat ->
            if (chat.hostId != c.id) return@let
            val listed = rids(now.firstOrNull { it.optString("id") == chat.sid })
            chat.asks.removeAll { it.rid !in listed }
            val missing = listed - chat.asks.map { it.rid }.toSet() - chat.fetched
            if (missing.isNotEmpty()) {
                chat.fetched.addAll(missing)
                attach(chat)
            }
        }
        val nm = NotificationManagerCompat.from(app)
        for (s in now) {
            val sid = s.optString("id")
            val key = "${c.id}/$sid"
            val listed = rids(s)
            val notified = askNotes.getOrPut(key) { HashSet() }
            for (rid in notified.filter { it !in listed }) { // (answered: its notification taken back)
                nm.cancel("ask:$key/$rid", ASK_NOTE)
                notified.remove(rid)
            }
            if (s.optJSONObject("bot") == null) continue
            val chat = open
            val inFront = shown && chat != null && chat.hostId == c.id && chat.sid == sid
            for (ask in s.optJSONArray("asks").list()) {
                val rid = ask.optString("request_id")
                if (rid in notified) continue
                notified.add(rid)
                if (inFront || (before == null && shown)) continue // (in front, or there as the app opened: seen)
                val contact = contact(c.id, sid) ?: continue
                val tool = ask.optString("tool")
                post(contact, if (tool == "AskUserQuestion") "Has a question for you"
                    else "Waiting for your answer: " + toolTitle(tool), "ask:$key/$rid")
            }
        }
    }

    /** The tools its host allows the chat whenever it asks (a session's always_allow, version 15), and those you've
     * allowed from here. */
    fun alwaysAllowed(chat: Chat): Set<String> {
        val listed = sessions[chat.hostId]?.firstOrNull { it.optString("id") == chat.sid }?.optJSONArray("always_allow")
        val host = (0 until (listed?.length() ?: 0)).map { listed!!.optString(it) }
        return host.toSet() + allowed["${chat.hostId}/${chat.sid}"].orEmpty()
    }

    /** An ask answered, as Baude answers it: claude's control_response for it, sent as the chat's input. allow: Allow
     * (always: and allowed whenever it asks for that tool, its host told so too: always_allow); answers: claude's
     * questions answered (AskUserQuestion's). */
    fun answer(chat: Chat, ask: Ask, allow: Boolean, always: Boolean = false, answers: JSONObject? = null) {
        val c = conns[chat.hostId] ?: return
        chat.asks.remove(ask)
        val response = when {
            answers != null -> JSONObject().put("behavior", "allow")
                .put("updatedInput", JSONObject(ask.input.toString()).put("answers", answers))
            allow -> JSONObject().put("behavior", "allow").put("updatedInput", ask.input)
            ask.tool == "AskUserQuestion" -> JSONObject().put("behavior", "deny").put("message",
                "The user chose not to answer. Carry on without their answers, or ask in your reply.")
            else -> JSONObject().put("behavior", "deny").put("message", "The user denied this tool use.")
        }
        val msg = JSONObject().put("type", "control_response").put("response", JSONObject().put("subtype", "success")
            .put("request_id", ask.rid).put("response", response))
        if (always) {
            val all = alwaysAllowed(chat) + ask.tool
            allowed.getOrPut("${chat.hostId}/${chat.sid}") { HashSet() }.add(ask.tool)
            val req = JSONObject().put("op", "always_allow").put("session", chat.sid)
            if (speaks(c.info, "always_allow", 15)) req.put("add", JSONArray(listOf(ask.tool))) // (added to
            else req.put("tools", JSONArray(all.sorted())) // its list; an older host's replaced: all that's known here)
            c.request(req) {}
        }
        c.request(JSONObject().put("op", "input").put("session", chat.sid).put("msg", msg)) { r ->
            if (r.optString("op") == "error") main.post {
                chat.said.value = "Couldn't answer: " + r.optString("error")
                if (open === chat && chat.asks.none { it.rid == ask.rid }) chat.asks.add(ask) // (still asked: again)
            }
        }
    }

    val contacts: List<Contact>
        get() = sessions.flatMap { (hostId, list) ->
            list.filter { it.optJSONObject("bot") != null && !it.optBoolean("archived") }.map { Contact(hostId, it) }
        }.sortedByDescending { it.said }

    fun contact(hostId: String, sid: String): Contact? = contacts.firstOrNull { it.hostId == hostId && it.id == sid }

    /** A Bot's picture, asked of its host once for each time it changes. */
    fun picture(c: Contact) {
        if (c.pictureKey in pictures || c.session.optJSONObject("bot")?.isNull("picture") != false) return
        pictures[c.pictureKey] = null
        conns[c.hostId]?.request(JSONObject().put("op", "bot_picture").put("session", c.id)) { r ->
            val data = r.optString("picture", "")
            if (data.isEmpty()) {
                main.post { pictures.remove(c.pictureKey) } // (asked again next time it's drawn)
                return@request
            }
            val bytes = Base64.getDecoder().decode(data)
            val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap() ?: return@request
            main.post { pictures[c.pictureKey] = bmp }
        }
    }

    // ---- a chat

    fun openChat(at: String, sid: String): Chat {
        val hostId = moved[at] ?: at // (a member's, its machine a host of yours since: that host's)
        open?.let { if (it.hostId == hostId && it.sid == sid) return it }
        close()
        val chat = Chat(hostId, sid)
        open = chat
        attach(chat)
        read(chat)
        NotificationManagerCompat.from(app).cancel(sid.hashCode())
        return chat
    }

    fun close() {
        val chat = open ?: return
        conns[chat.hostId]?.send(JSONObject().put("op", "detach").put("session", chat.sid))
        open = null
    }

    /** The app on screen, or not: the chat open attached again as one that answers what it asks, or doesn't (so a
     * Bot's ask_timeout runs out while the phone's put away, as if nobody were there; its notification still comes). */
    fun onScreen(now: Boolean) {
        if (visible == now) return
        visible = now
        open?.let { attach(it) }
    }

    /** The chat attached to live, as one that answers what it asks while the app's on screen (its asks, as they are,
     * shown to answer). */
    private fun attach(chat: Chat) {
        conns[chat.hostId]?.request(JSONObject().put("op", "attach").put("session", chat.sid).put("live", true)
            .put("answers", visible)) { r ->
            if (r.optString("op") != "attached") return@request
            val asks = r.optJSONArray("asks").list().mapNotNull(Ask::of)
            main.post {
                if (open !== chat) return@post
                chat.asks.clear()
                chat.asks.addAll(asks)
            }
        }
    }

    /** More of the chat's transcript read (its last part, the first time: a long one isn't all fetched). */
    fun read(chat: Chat) {
        if (chat.reading) {
            chat.again = true
            return
        }
        val c = conns[chat.hostId] ?: return
        val size = sessions[chat.hostId]?.firstOrNull { it.optString("id") == chat.sid }?.optLong("size", 0) ?: 0
        val from = if (chat.offset < 0) (size - TAIL).coerceAtLeast(0) else chat.offset
        if (chat.offset < 0 && sessions[chat.hostId] == null) return // (its size not known yet: read once it is)
        chat.reading = true
        c.request(JSONObject().put("op", "transcript").put("session", chat.sid).put("offset", from)) { r ->
            val data = r.optString("data", "")
            val text = if (data.isEmpty()) "" else String(Base64.getDecoder().decode(data), Charsets.UTF_8)
            val next = r.optLong("next", from)
            val more = next < r.optLong("size", next)
            val lines = parse(text, cutFirst = chat.offset < 0 && from > 0)
            main.post {
                if (r.optString("op") == "transcript") {
                    chat.offset = next
                    for (line in lines) {
                        when (line.kind) {
                            "record" -> line.uuid?.let { chat.ends[it] = chat.lines.size }
                            "rewind" -> { // (what's after the record it keeps last dropped: all of what's read, if
                                val keep = line.uuid?.let { chat.ends[it] } ?: 0 // that's before it)
                                while (chat.lines.size > keep) chat.lines.removeAt(chat.lines.size - 1)
                                chat.ends.values.removeAll { it > keep }
                            }
                            else -> chat.lines.add(line)
                        }
                    }
                }
                chat.reading = false
                if (more || chat.again) {
                    chat.again = false
                    read(chat)
                }
            }
        }
    }

    /** What you wrote (and the files you chose) sent to the chat, as Baude sends it: a user message for its agent. */
    fun send(chat: Chat, text: String, files: List<Picked>) {
        chat.busy.value = true
        chat.said.value = null
        val c = conns[chat.hostId]
        if (c == null) {
            chat.busy.value = false
            chat.said.value = "Can't connect to the host."
            return
        }
        kotlin.concurrent.thread(name = "send", isDaemon = true) { build(chat, c, text, files) }
    }

    /** The message made (base64 and all, off the main thread) and sent. */
    private fun build(chat: Chat, c: Connection, text: String, files: List<Picked>) {
        val blocks = JSONArray()
        val notes = StringBuilder()
        for (f in files) {
            when {
                f.mime in IMAGE_TYPES -> blocks.put(JSONObject().put("type", "image").put("source", JSONObject()
                    .put("type", "base64").put("media_type", f.mime).put("data", f.base64())))
                f.mime == "application/pdf" -> blocks.put(JSONObject().put("type", "document").put("title", f.name)
                    .put("source", JSONObject().put("type", "base64").put("media_type", "application/pdf")
                        .put("data", f.base64())))
                else -> notes.append("\n\n").append(f.name).append(":\n```\n")
                    .append(String(f.bytes, Charsets.UTF_8)).append("\n```")
            }
        }
        val all = (text + notes).trim()
        if (all.isNotEmpty()) blocks.put(JSONObject().put("type", "text").put("text", all))
        if (blocks.length() == 0) {
            main.post { chat.busy.value = false }
            return
        }
        val msg = JSONObject().put("type", "user").put("message", JSONObject().put("role", "user").put("content", blocks))
        c.request(JSONObject().put("op", "input").put("session", chat.sid).put("msg", msg)) { r ->
            main.post {
                chat.busy.value = false
                if (r.optString("op") == "error") chat.said.value = "Couldn't send: " + r.optString("error")
                read(chat)
            }
        }
    }

    // ---- notifications

    private fun channel() {
        if (Build.VERSION.SDK_INT >= 26) {
            val nm = app.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel(MESSAGES, "Messages", NotificationManager.IMPORTANCE_HIGH)
                .apply { lockscreenVisibility = android.app.Notification.VISIBILITY_PRIVATE }) // (locked: who, not what)
            nm.createNotificationChannel(NotificationChannel(LINKED, "Connected", NotificationManager.IMPORTANCE_MIN))
        }
    }

    /** A Bot's turn ended: what it said last (the turn's own result), as a notification (none for the chat that's
     * open in front). */
    private fun ended(c: Connection, msg: JSONObject) {
        val sid = msg.optString("session")
        val chat = open
        if (chat != null && chat.hostId == c.id && chat.sid == sid) {
            read(chat)
            if (shown) return
        }
        val contact = contact(c.id, sid) ?: return
        val result = if (msg.isNull("result")) "" else msg.optString("result").trim()
        when {
            msg.optBoolean("is_error") -> notify(contact, "Couldn't finish" + if (result.isNotEmpty()) ": $result" else "")
            result.isNotEmpty() -> notify(contact, result)
            else -> main.postDelayed({ // (no result said: its transcript's end, once its new size is listed)
                val size = sessions[c.id]?.firstOrNull { it.optString("id") == sid }?.optLong("size", 0) ?: 0
                last(c, sid, size)
            }, 1_200)
        }
    }

    /** Back after a drop: each Bot whose chat grew meanwhile, and is between turns now, notified with what it said
     * last (its transcript's end). */
    private fun missed(c: Connection, before: List<JSONObject>, now: List<JSONObject>) {
        for (s in now) {
            if (s.optJSONObject("bot") == null || s.optString("state") !in setOf("idle", "stopped")) continue
            val old = before.firstOrNull { it.optString("id") == s.optString("id") } ?: continue
            if (s.optLong("size", 0) <= old.optLong("size", 0)) continue
            val chat = open
            if (shown && chat != null && chat.hostId == c.id && chat.sid == s.optString("id")) continue
            last(c, s.optString("id"), s.optLong("size", 0))
        }
    }

    private fun last(c: Connection, sid: String, size: Long) {
        val contact = contact(c.id, sid) ?: return
        c.request(JSONObject().put("op", "transcript").put("session", sid)
            .put("offset", (size - 64_000).coerceAtLeast(0))) { r ->
            val data = r.optString("data", "")
            val text = if (data.isEmpty()) "" else String(Base64.getDecoder().decode(data), Charsets.UTF_8)
            val last = parse(text, cutFirst = true).lastOrNull { it.kind == "bot" || it.kind == "error" }?.text ?: return@request
            main.post { notify(contact, last) }
        }
    }

    private val notified = HashMap<String, Pair<String, Long>>() // (a Bot: what it was last notified for, and when)

    private fun notify(contact: Contact, text: String) {
        val now = android.os.SystemClock.elapsedRealtime()
        val before = notified[contact.id]
        if (before != null && before.first == text && now - before.second < 120_000) return // (the same turn, told
        notified[contact.id] = text to now                                                  // twice: once)
        post(contact, text, null)
    }

    /** A notification for a Bot that opens its chat: what it said (one a Bot, each in place of the last), or one of
     * its asks (tag: its own, taken back once it's answered). On the lock screen, only which Bot wrote or is asking:
     * what it said shows once the phone's unlocked. */
    private fun post(contact: Contact, text: String, tag: String?) {
        if (Build.VERSION.SDK_INT >= 33 &&
            app.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED
        ) return
        val intent = Intent(app, MainActivity::class.java).putExtra("host", contact.hostId).putExtra("session", contact.id)
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        val pending = PendingIntent.getActivity(app, (tag ?: contact.id).hashCode(), intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val n = NotificationCompat.Builder(app, MESSAGES)
            .setSmallIcon(R.drawable.ic_baude)
            .setContentTitle(contact.name)
            .setContentText(text.take(200))
            .setStyle(NotificationCompat.BigTextStyle().bigText(text.take(2000)))
            .setContentIntent(pending)
            .setAutoCancel(true)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setPublicVersion(NotificationCompat.Builder(app, MESSAGES)
                .setSmallIcon(R.drawable.ic_baude)
                .setContentTitle(contact.name)
                .setContentText(if (tag != null) "Waiting for your answer" else "New message")
                .build())
            .build()
        try {
            if (tag != null) NotificationManagerCompat.from(app).notify(tag, ASK_NOTE, n)
            else NotificationManagerCompat.from(app).notify(contact.id.hashCode(), n)
        } catch (_: SecurityException) {
        }
    }

    const val MESSAGES = "messages"
    private const val ASK_NOTE = 2 // (an ask's notification: its tag says which)
    const val LINKED = "linked"
    private const val TAIL = 400_000L // (a chat's transcript read from its last this much, the first time)
    val IMAGE_TYPES = setOf("image/png", "image/jpeg", "image/gif", "image/webp")
}

/** A file you picked to send. */
class Picked(val name: String, val mime: String, val bytes: ByteArray) {
    fun base64(): String = Base64.getEncoder().encodeToString(bytes)
}

fun JSONArray?.list(): List<JSONObject> = if (this == null) emptyList() else (0 until length()).map { getJSONObject(it) }

/** A transcript's lines (claude's JSONL) as a chat shows them, by Baude's rules (transcripts.history_item): what you
 * wrote (reminders a client put before it left out), what the agent said and did, notes (the conversation
 * compacted, a background task ended, a turn stopped), and reverts' marks. cutFirst: it starts partway into a line,
 * which is left out. */
fun parse(text: String, cutFirst: Boolean): List<Line> {
    val out = ArrayList<Line>()
    val rows = text.split("\n").let { if (cutFirst) it.drop(1) else it }
    for (row in rows) {
        if (row.isBlank()) continue
        val o = try {
            JSONObject(row)
        } catch (_: Exception) {
            continue
        }
        val type = o.optString("type")
        if (type == "last-prompt" && o.optBoolean("rewound")) {
            out.add(Line("rewind", "", if (o.isNull("leafUuid")) null else o.optString("leafUuid")))
            continue
        }
        val uuid = if (o.isNull("uuid")) null else o.optString("uuid").ifBlank { null }
        record(o, type, uuid, out)
        if (uuid != null) out.add(Line("record", "", uuid)) // (where it ends: a revert to it cuts there)
    }
    return out
}

/** The lines one record shows (none, for most). */
private fun record(o: JSONObject, type: String, uuid: String?, out: MutableList<Line>) {
    run {
        if (type != "user" && type != "assistant") return
        if (o.optBoolean("isMeta") || o.optBoolean("isSidechain")) return
        if (o.optBoolean("isCompactSummary")) {
            out.add(Line("note", "The conversation was compacted", uuid))
            return
        }
        val msg = o.optJSONObject("message") ?: return
        val blocks = when (val content = msg.opt("content")) {
            is JSONArray -> content.list()
            is String -> listOf(JSONObject().put("type", "text").put("text", content))
            else -> emptyList()
        }
        for (b in blocks) {
            when (b.optString("type")) {
                "text" -> {
                    var t = b.optString("text")
                    if (type == "user") {
                        t = LEADING_REMINDERS.replace(t, "").trim()
                        when {
                            t.startsWith("<task-notification") -> out.add(Line("note", "A background task ended", uuid))
                            t.startsWith(STOPPED) -> out.add(Line("note", "Stopped", uuid))
                            t.contains("<command-name>") -> out.add(Line("you", command(t), uuid))
                            t.isEmpty() || NOT_TYPED.any { t.startsWith(it) } -> {}
                            else -> out.add(Line("you", t, uuid))
                        }
                    } else if (t.isNotBlank()) {
                        out.add(Line(if (o.optBoolean("isApiErrorMessage")) "error" else "bot", t.trim(), uuid))
                    }
                }
                "image" -> if (type == "user") out.add(Line("you", "🖼 A picture", uuid))
                "document" -> if (type == "user") out.add(Line("you", "📄 " + b.optString("title", "A document"), uuid))
                "tool_use" -> if (type == "assistant") out.add(Line("tool", toolLine(b), uuid))
            }
        }
    }
}

/** A slash command as you typed it ("/name args"), from claude's record of it. */
private fun command(t: String): String {
    val name = Regex("<command-name>(.*?)</command-name>", RegexOption.DOT_MATCHES_ALL).find(t)?.groupValues?.get(1)
    val args = Regex("<command-args>(.*?)</command-args>", RegexOption.DOT_MATCHES_ALL).find(t)?.groupValues?.get(1)
    return listOfNotNull(name?.trim()?.let { if (it.startsWith("/")) it else "/$it" }, args?.trim()?.ifBlank { null })
        .joinToString(" ").ifBlank { t }
}

private val LEADING_REMINDERS = Regex("""\A(?:\s*<system-reminder>.*?</system-reminder>)+\s*""", RegexOption.DOT_MATCHES_ALL)
private const val STOPPED = "[Request interrupted by user"
private val NOT_TYPED = listOf("<system-reminder", "<local-command", "<command-", "Caveat:", "<ci-monitor-event")

private fun toolLine(b: JSONObject): String {
    val name = b.optString("name")
    val input = b.optJSONObject("input") ?: JSONObject()
    val what = input.optString("description").ifBlank { input.optString("command") }
        .ifBlank { input.optString("file_path") }.ifBlank { input.optString("url") }.ifBlank { input.optString("pattern") }
    return if (what.isBlank()) toolTitle(name) else "${toolTitle(name)} · ${what.lines().first().take(80)}"
}

/** A tool's name as Baude says it (tool_display): (its title, its source), an MCP tool's (mcp__<server>__<tool>)
 * made words, its server's name the source. */
fun toolDisplay(name: String): Pair<String, String?> {
    if (!name.startsWith("mcp__") || name.split("__").size < 3) return name to null
    val (_, rawServer, rawTool) = name.split("__", limit = 3)
    val server = rawServer.replace(Regex("^(claude_ai_|plugin_[^_]+_)"), "")
    val source = if (Regex("[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}").matches(server)) "MCP"
    else MCP_NAMES[server.lowercase()] ?: humanize(server).split(" ").joinToString(" ") { it.replaceFirstChar(Char::uppercase) }
    val tool = if (rawTool.lowercase().startsWith(server.lowercase() + "_")) rawTool.substring(server.length + 1) else rawTool
    val title = humanize(tool)
    return title.replaceFirstChar(Char::uppercase).ifBlank { name } to source
}

/** "Source: Title", or the title alone. */
fun toolTitle(name: String): String = toolDisplay(name).let { (title, source) -> if (source != null) "$source: $title" else title }

private fun humanize(id: String) =
    id.replace(Regex("([a-z0-9])([A-Z])"), "$1 $2").replace("_", " ").replace("-", " ").trim().split(Regex("\\s+"))
        .joinToString(" ").lowercase()

private val MCP_NAMES = mapOf("github" to "GitHub", "gitlab" to "GitLab", "gmail" to "Gmail", "gdrive" to "Google Drive",
    "google_drive" to "Google Drive", "gcal" to "Google Calendar", "slack" to "Slack", "notion" to "Notion",
    "linear" to "Linear", "jira" to "Jira", "figma" to "Figma", "postgres" to "Postgres", "sqlite" to "SQLite",
    "playwright" to "Playwright", "sentry" to "Sentry")
