package dev.baude.android

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.graphics.BitmapFactory
import android.os.Build
import android.os.Handler
import android.widget.Toast
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
data class Contact(val hostId: String, val session: Msg.Session) {
    val id: String get() = session.id.orEmpty()
    val name: String get() = session.bot?.name?.ifBlank { null } ?: Wording.Sidebar.bot
    val said: Double get() = (session.said ?: session.updated)?.toDouble() ?: 0.0
    val state: String get() = session.state ?: "stopped"
    val asking: Boolean get() = !session.asks.isNullOrEmpty()
    val alwaysOffered: Boolean get() = alwaysOffered(session.model, hostId)
    val pictureKey: String get() = "$hostId/$id/${session.bot?.picture}"
}

/** Whether "always allow" is offered for what a chat asks, as Baude offers it: for Claude Code's models (another
 * program's asks each call: models.model_program); or, whatever its program, where its host lists the ops it takes
 * and always_allow's one of them (a phone's agents, which keep it as claude's). */
fun alwaysOffered(m: String?, hostId: String? = null): Boolean = m == null ||
    !(m.startsWith("codex/") || m == "apple" || m.startsWith("ollama/") || m.startsWith("openai/") ||
        m.startsWith("agent/") || m.startsWith("mlx-")) ||
    hostId != null && lists(hostId, "always_allow")

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
class Chat(hostId: String, sid: String) {
    var sid by mutableStateOf(sid)
    var hostId by mutableStateOf(hostId)
    val lines = mutableStateListOf<Line>()
    val files = mutableStateListOf<Picked>() // (picked to send: kept as the phone's turned)
    val draft = mutableStateOf<String?>(null) // (text for the box: shared from another app, or kept as the app went)
    val preparing = mutableStateOf(0)         // (picked, still being read)
    val ends = HashMap<String, Int>()         // (a record's uuid: how many lines were shown up to its end)
    val busy = mutableStateOf(false)
    val thinking = mutableStateOf(false)     // (at a turn, where its host doesn't list it: Meta's)
    val said = mutableStateOf<String?>(null)
    val streaming = mutableStateOf("") // (what its agent's writing now, as it comes: till its message is whole)
    val asks = mutableStateListOf<Ask>()      // (what its Bot asks, answered here: Allow, Deny)
    val fetched = HashSet<String>()           // (asks its host listed that weren't here: fetched again, once)
    val senders = mutableStateMapOf<String, Msg.StoredSender>() // (a message's uuid: who it's from, if not you, as its
    // host's sender store has it: wire's Senders)
    var sendersRev = 0L                       // (the store's revision fetched: what its host lists is checked against)
    val holding = mutableStateListOf<String>() // (held messages being sent or dropped: not shown meanwhile)
    var fetchingSenders = false
    val artifacts = mutableStateListOf<Artifact>() // (what it published, newest first: the Artifacts page)
    val agents = mutableStateListOf<Subagent>()    // (the subagents it started, newest first: the Subagents page)
    val btw = mutableStateListOf<SideQuestion>()   // (its side questions, newest first, while it's open)
    val uses = HashMap<String, JSONObject>()       // (an Artifact or Agent call's id: the call, till its result's read)
    var offset = -1L
    var reading = false
    var again = false
    val sent = mutableStateListOf<Sent>()     // (what you sent that its transcript doesn't show yet: drawn as sent)
    val queued = mutableStateListOf<Queued>() // (written while a turn ran: sent when it ends)
    var queueUpto: Queued? = null             // (↑ on a queued message: the queue is sent up to it at the turn's end)
    var stopAsked = false                     // (the turn was stopped: its queue stays)
    val edit = mutableStateOf<String?>(null)  // (a queued message's revert: text for the box, before what's written)
    var typed = ""                            // (what's in the box, kept as it's written)
    var sender: ((String, List<Picked>) -> Unit)? = null // (one that's sent its own way: Meta's)
    val chrome = Chrome() // (what its status bar, banner and stop button keep)

    fun send(text: String, files: List<Picked>) = sender?.invoke(text, files) ?: Mesh.send(this, text, files)
}

/** The mesh as this app has it: your hosts, a connection to each, their Bots, the chat open, notifications as a Bot
 * says something. One for the app (the activity and the browser's service share it); its state's Compose's, changed on the main
 * thread. */
object Mesh {
    lateinit var app: Context
    private val main = Handler(Looper.getMainLooper())
    val hosts = mutableStateListOf<Host>()
    val states = mutableStateMapOf<String, Connection>() // (host id: its connection, whose state is read)
    val tick = mutableStateOf(0) // (a connection's state changed: what reads it drawn again)
    val sessions = mutableStateMapOf<String, List<Msg.Session>>() // (a connection's id: its host's sessions, drafts
    // and archived ones too, which the home screen leaves out or folds)
    val listeners = ArrayList<(String, JSONObject) -> Unit>() // (what a host says, as it's heard, on the main thread:
    // the pages' own, by the connection's id)
    val pictures = mutableStateMapOf<String, ImageBitmap?>()
    val key = mutableStateOf<Key?>(null)
    val keyError = mutableStateOf<String?>(null) // (why the Keystore's key couldn't be read, or made: none made then)
    val making = mutableStateOf(false) // (a key being made, off the main thread)
    val infos = mutableStateMapOf<String, Msg.Info>() // (a host of yours: what its host process last said it is,
    // its mesh too: Mesh Versions)
    val members = mutableStateMapOf<String, Pair<Msg.Member, String?>>() // (a member of a host's mesh, by its id: its
    // record, as the host it's reached through lists it (id, name, host, hops, through), and that host's label)
    @Volatile private var routes = emptyMap<String, Connection>() // (a member: the host that's up and reaches it in
    // fewest hops)
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
        WireStrict.look(app)
        readKey()
        hosts.addAll(HostStore.load(app))
        Queue.loadDrafts(app.filesDir)
        Queue.migrateUnsent(app.filesDir) { machine, host -> // (its chat's host: the one of yours that's its machine, or the machine as a member)
            hosts.firstOrNull { machine != null && HostStore.machine(app, it) == machine }?.id
                ?: machine?.let { "mesh:$it" } ?: moved[host] ?: host
        }
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
            keyError.value = "This phone's key couldn't be read: ${e.message ?: e.javaClass.simpleName}"
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
        Shares.look()
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
        val m = members[id.removePrefix("mesh:")]?.first
        return m?.name?.ifBlank { null } ?: conns[id]?.info?.name?.ifBlank { null } ?: "a host"
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
            (c?.info?.id?.takeIf(::isId) ?: HostStore.machine(app, h))?.let { known.putIfAbsent(it, h) }
            if (c?.state == "key") held.add(h)
        }
        fun heldBy(m: Msg.Member?): Host? = if (m == null) null else held.firstOrNull { h -> // (its machine, by its id
            HostStore.machine(app, h) == m.id.orEmpty() || h.address.lowercase() in listOf(m.host, m.bonjour, m.name)
                .mapNotNull { it.orEmpty().lowercase().ifBlank { null } }.flatMap { listOf(it, "$it.local") }
        }                                                                    // or the names its record gives it)
        val best = HashMap<String, Pair<Msg.Member, Connection>>()
        for (h in hosts) {
            val c = conns[h.id]?.takeIf { it.state == "up" } ?: continue
            val seen = listedBy.getOrPut(h.id) { HashSet() }
            for (m in c.info?.mesh?.members.orEmpty()) {
                val id = m.id?.takeIf(::isId) ?: continue
                seen.add(id)
                if (id in known || heldBy(m) != null || id == PhoneHost.id) continue // (this phone, as the mesh has it)
                val now = best[id]
                if (now == null || (m.hops?.toInt() ?: 99) < (now.first.hops?.toInt() ?: 99)) best[id] = m to c
            }
        }
        routes = best.mapValues { it.value.second }
        for ((id, pair) in best) {
            members[id] = pair.first to pair.second.host?.label
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
            val mine = known[id] ?: heldBy(members[id]?.first) // (a host of yours: reached so, or not till you've said of its
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
        Shares.look()
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
        PhoneBrowser.changed(c)
        if (c.state == "up") {
            c.host?.let { h -> c.info?.id?.takeIf(::isId)?.let { HostStore.setMachine(app, h, it) } }
            if (sessions[c.id] != null) catchUp.add(c.id)
            open?.let { if (it.hostId == c.id) attach(it) } // (read once its sessions are listed: their sizes)
        }
        if (c.place is Place.Direct) {
            meshed()
            PhoneLink.look()
        }
        if (c.place !is Place.Phone) PhoneHost.changed() // (the models the mesh serves the phone's agents: heads told)
        if (c.state == "up") for (l in listeners.toList()) l(c.id, JSONObject().put("op", "connected")) // (its pages:
    }                                                                                                    // attached again)

    fun conn(hostId: String): Connection? = conns[hostId]

    /** The mesh as one of your hosts that's up says it is (the first in your list), for Mesh Versions: what its host
     * process is, and its mesh's members, each with the apps connected to it; null with none up. */
    fun versions(): Msg.Info? = versionsHost()?.let { infos[it] }

    /** The host versions() is from: your first that's up and has said its mesh. */
    fun versionsHost(): String? = hosts.firstOrNull { h ->
        infos[h.id]?.mesh != null && states[h.id]?.state == "up"
    }?.id

    /** A request to a host, its reply on the main thread (an error one if it isn't connected). */
    fun ask(hostId: String, msg: JSONObject, then: (JSONObject) -> Unit) {
        val c = conns[hostId]
        if (c == null) {
            then(Msg.Error(error = "the server isn't there").json())
            return
        }
        c.request(msg) { r -> main.post { then(r) } }
    }

    fun post(block: () -> Unit) {
        main.post(block)
    }

    fun later(ms: Long, block: () -> Unit) {
        main.postDelayed(block, ms)
    }

    /** A session as its host lists it, or null. */
    fun session(hostId: String, sid: String): Msg.Session? = sessions[hostId]?.firstOrNull { it.id == sid }

    /** Every connection that's up, as a host or a member of the mesh, that takes op if it's given (takes): (id,
     * label), your hosts first. */
    fun reached(op: String? = null): List<Pair<String, String>> = conns.values
        .filter { it.state == "up" && (op == null || takes(it.id, op)) }
        .sortedWith(compareBy({ it.place is Place.Member }, { label(it.id).lowercase() })).map { it.id to label(it.id) }

    /** Another connection to where hostId is, for a watch of its own (it doesn't subscribe): started; stop it when
     * it's done with. */
    fun watch(hostId: String, heard: (Connection, JSONObject) -> Unit, changed: (Connection) -> Unit): Connection? {
        // (this phone's own host is in the app: no watch of its own)
        val c = conns[hostId]?.takeIf { it.place !is Place.Phone } ?: return null
        return Connection(app, c.place, ::identify, { routes[it] }, heard, subscribes = false) { w ->
            main.post { changed(w) }
        }.also { it.start() }
    }

    // ---- what the hosts say

    private fun heard(c: Connection, msg: JSONObject) {
        main.post {
            if (conns[c.id] !== c) return@post
            for (l in listeners.toList()) l(c.id, msg)
            when (msg.optString("op")) {
                Op.SESSIONS -> {
                    val listing = Msg.Sessions.from(msg)
                    val now = listing.sessions.orEmpty()
                    val before = sessions[c.id]
                    val was = open?.takeIf { it.hostId == c.id }?.let { isWorking(it) } == true
                    sessions[c.id] = now
                    open?.takeIf { was && it.hostId == c.id && !isWorking(it) }?.let { Queue.flush(it) } // (a turn ended: what's queued sent)
                    Shares.look() // (its Bots, in the share sheet)
                    listing.server?.let { // (its mesh as it is now)
                        c.info = it
                        if (c.place is Place.Direct) {
                            infos[c.id] = it
                            meshed()
                        }
                    }
                    if (catchUp.remove(c.id) && before != null) missed(c, before, now)
                    asked(c, before, now)
                    open?.let {
                        if (it.hostId != c.id) return@let
                        senders(it) // (who its messages from others are from, if its host's store is newer)
                        read(it) // (its first read, or what it missed)
                    }
                }
                Op.TURN_ENDED -> ended(c, msg)
                Op.EMISSION_CALL -> PhoneBrowser.called(c, msg) // (a Bot's browser tools, for its browser here)
                Op.EMISSION_UNSERVED -> PhoneBrowser.unserved(c, msg)
                Op.CLAUDE -> open?.let {
                    val said = Msg.Claude.from(msg)
                    if (it.hostId != c.id || it.sid != said.session.orEmpty()) return@let
                    said.msg?.let { m -> if (m.optString("type") == "system") task(it, m) }
                    streamed(it, said.msg)
                    if (said.msg?.optString("type") == "stream_event") return@let // (its text: no read)
                    val ask = said.msg?.let(Ask::of)
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
    private fun asked(c: Connection, before: List<Msg.Session>?, now: List<Msg.Session>) {
        fun rids(s: Msg.Session?) = s?.asks.orEmpty().map { it.requestId.orEmpty() }.toSet()
        open?.let { chat ->
            if (chat.hostId != c.id) return@let
            val listed = rids(now.firstOrNull { it.id == chat.sid })
            chat.asks.removeAll { it.rid !in listed }
            val missing = listed - chat.asks.map { it.rid }.toSet() - chat.fetched
            if (missing.isNotEmpty()) {
                chat.fetched.addAll(missing)
                attach(chat)
            }
        }
        val nm = NotificationManagerCompat.from(app)
        for (s in now) {
            val sid = s.id.orEmpty()
            val key = "${c.id}/$sid"
            val listed = rids(s)
            val notified = askNotes.getOrPut(key) { HashSet() }
            for (rid in notified.filter { it !in listed }) { // (answered: its notification taken back)
                nm.cancel("ask:$key/$rid", ASK_NOTE)
                notified.remove(rid)
            }
            if (s.bot == null) continue
            val chat = open
            val inFront = shown && chat != null && chat.hostId == c.id && chat.sid == sid
            for (ask in s.asks.orEmpty()) {
                val rid = ask.requestId.orEmpty()
                if (rid in notified) continue
                notified.add(rid)
                if (inFront || (before == null && shown)) continue // (in front, or there as the app opened: seen)
                val contact = contact(c.id, sid) ?: continue
                val tool = ask.tool.orEmpty()
                post(contact, if (tool == "AskUserQuestion") Wording.Asks.hasQuestionForYou(name = contact.name)
                    else Wording.Asks.waitingForTool(tool = toolTitle(tool).ifBlank { Wording.Asks.aTool }), "ask:$key/$rid")
            }
        }
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
            c.request(Msg.AlwaysAllow(session = chat.sid, add = listOf(ask.tool)).json()) { r -> // (refused: not here either)
                if (r.optString("op") == Op.ERROR) main.post {
                    chat.said.value = Wording.Asks.notAlwaysAllowed(tool = toolDisplay(ask.tool).first, why = Msg.Error.from(r).said.orEmpty())
                }
            }
        }
        c.request(Msg.Input(session = chat.sid, msg = msg).json()) { r ->
            if (r.optString("op") == Op.ERROR) main.post {
                chat.said.value = "Couldn't answer: " + Msg.Error.from(r).said.orEmpty()
                if (open === chat && chat.asks.none { it.rid == ask.rid }) chat.asks.add(ask) // (still asked: again)
            }
        }
    }

    val contacts: List<Contact>
        get() = sessions.flatMap { (hostId, list) ->
            list.filter { it.bot != null && it.archived != true }.map { Contact(hostId, it) }
        }.sortedByDescending { it.said }

    fun contact(hostId: String, sid: String): Contact? = contacts.firstOrNull { it.hostId == hostId && it.id == sid }

    /** A Bot's picture, asked of its host once for each time it changes. */
    fun picture(c: Contact) {
        if (c.pictureKey in pictures || c.session.bot?.picture == null) return
        pictures[c.pictureKey] = null
        conns[c.hostId]?.request(Msg.BotPicture(session = c.id).json()) { r ->
            val data = Msg.BotPictureReply.from(r).picture.orEmpty()
            if (data.isEmpty()) {
                main.post { pictures.remove(c.pictureKey) } // (asked again next time it's drawn)
                return@request
            }
            val bytes = Base64.getDecoder().decode(data)
            val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap() ?: return@request
            main.post {
                pictures[c.pictureKey] = bmp
                Shares.look()
            }
        }
    }

    // ---- a chat

    fun openChat(at: String, sid: String): Chat {
        val hostId = moved[at] ?: at // (a member's, its machine a host of yours since: that host's)
        open?.let {
            if (it.hostId == hostId && it.sid == sid) {
                senders(it) // (who its messages from others are from, if its host's store is newer)
                return it
            }
        }
        close()
        val chat = Chat(hostId, sid)
        Queue.takeDraft(hostId, sid, machine(hostId))?.let { d ->
            chat.draft.value = d.text.ifBlank { null }
            chat.files.addAll(d.files)
        }
        open = chat
        attach(chat)
        senders(chat)
        read(chat)
        NotificationManagerCompat.from(app).cancel(sid.hashCode())
        return chat
    }

    fun close() {
        val chat = open ?: return
        Queue.keepDraft(chat)
        conns[chat.hostId]?.send(Msg.Detach(session = chat.sid).json())
        for (q in chat.btw) if (q.sid.isNotEmpty()) // (its side questions let go of with it, as Baude's window's are)
            conns[chat.hostId]?.send(Msg.Detach(session = q.sid).json())
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
        conns[chat.hostId]?.request(Msg.Attach(session = chat.sid, live = true, answers = visible).json()) { r ->
            if (r.optString("op") == Op.ERROR && Msg.Error.from(r).said.orEmpty().startsWith("no session"))
                main.post { chat.chrome.missing.value = true } // (its host there, but with no such session)
            if (r.optString("op") != Op.ATTACHED) return@request
            main.post { chat.chrome.missing.value = false }
            val asks = Msg.Attached.from(r).asks.orEmpty().mapNotNull(Ask::of)
            main.post {
                if (open !== chat) return@post
                chat.asks.clear()
                chat.asks.addAll(asks)
            }
        }
    }

    /** The chat's sender store fetched from its host, if it lists a newer one than is here: who its messages from
     * others than you are from, by their uuids. A message is drawn as another's only if it's there, never by what it
     * says. */
    fun senders(chat: Chat) {
        val c = conns[chat.hostId] ?: return
        val listed = session(chat.hostId, chat.sid)?.senders?.toLong() ?: 0
        if (listed == chat.sendersRev || chat.fetchingSenders) return
        chat.fetchingSenders = true
        c.request(Msg.SendersOp(session = chat.sid).json()) { r ->
            val reply = Msg.SendersReply.from(r)
            main.post {
                chat.fetchingSenders = false
                val now = reply.senders
                if (r.optString("op") != Op.SENDERS || now == null) return@post
                chat.sendersRev = reply.rev?.toLong() ?: listed
                chat.senders.keys.retainAll(now.keys)
                chat.senders.putAll(now)
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
        val size = session(chat.hostId, chat.sid)?.size?.toLong() ?: 0
        val from = if (chat.offset < 0) (size - TAIL).coerceAtLeast(0) else chat.offset
        if (chat.offset < 0 && sessions[chat.hostId] == null) return // (its size not known yet: read once it is)
        chat.reading = true
        c.request(Msg.Transcript(session = chat.sid, offset = from).json()) { r ->
            val got = Msg.TranscriptReply.from(r)
            val data = got.data.orEmpty()
            val text = if (data.isEmpty()) "" else String(Base64.getDecoder().decode(data), Charsets.UTF_8)
            val next = got.next?.toLong() ?: from
            val more = next < (got.size?.toLong() ?: next)
            val lines = parse(text, cutFirst = chat.offset < 0 && from > 0)
            main.post {
                if (r.optString("op") == Op.TRANSCRIPT) {
                    chat.offset = next
                    for (line in lines) {
                        when (line.kind) {
                            "use", "result", "task", "link" -> noted(chat, line)
                            "record" -> line.uuid?.let { chat.ends[it] = chat.lines.size }
                            "rewind" -> { // (what's after the record it keeps last dropped: all of what's read, if
                                val keep = line.uuid?.let { chat.ends[it] } ?: 0 // that's before it)
                                while (chat.lines.size > keep) chat.lines.removeAt(chat.lines.size - 1)
                                chat.ends.values.removeAll { it > keep }
                            }
                            else -> {
                                if (line.kind == "you") chat.sent.removeAll { it.id == line.uuid }
                                chat.lines.add(line)
                            }
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

    /** A line the pages note, not the chat (parse's use, result, task and link): an artifact published, a subagent
     * started or ended. */
    private fun noted(chat: Chat, line: Line) {
        val o = try {
            JSONObject(line.text)
        } catch (_: Exception) {
            return
        }
        when (line.kind) {
            "use" -> {
                chat.uses[o.optString("id")] = o
                if (isAgent(o.optString("name"))) {
                    val input = o.optJSONObject("input") ?: JSONObject()
                    val a = Subagent(o.optString("id"), input.optString("description").ifBlank { Wording.SidePanel.subagent },
                        input.optString("subagent_type"), input.optString("prompt"))
                    chat.agents.removeAll { it.toolUseId == a.toolUseId }
                    chat.agents.add(0, a)
                }
            }
            "result" -> {
                val use = chat.uses.remove(o.optString("id")) ?: return
                val text = o.optString("text")
                if (isAgent(use.optString("name"))) {
                    val a = chat.agents.firstOrNull { it.toolUseId == use.optString("id") } ?: return
                    a.agentId.value = Regex("agentId: ([A-Za-z0-9_-]+)").find(text)?.groupValues?.get(1)
                    if (!text.startsWith("Async agent launched"))
                        a.status.value = if (o.optBoolean("is_error")) "failed" else "completed"
                } else artifact(use, text)?.let { added(chat, it) }
            }
            "task" -> {
                val a = chat.agents.firstOrNull { it.agentId.value == o.optString("task_id") ||
                    it.toolUseId == o.optString("tool_use_id") } ?: return
                o.optString("status").ifBlank { null }?.let { a.status.value = it }
            }
            "link" -> for (url in o.optJSONArray("urls").let { a -> (0 until (a?.length() ?: 0)).map { a!!.optString(it) } })
                if (chat.artifacts.none { it.url == url }) chat.artifacts.add(0, Artifact(url, Wording.References.claudeArtifact))
        }
    }

    /** A subagent's task as claude tells of it live (task_started, task_progress, task_updated, task_notification):
     * listed as it starts, its status, what it's doing and what it's used kept. */
    private fun task(chat: Chat, m: JSONObject) {
        if (!m.optString("subtype").startsWith("task_")) return
        val tool = m.optString("tool_use_id")
        var a = chat.agents.firstOrNull { it.toolUseId == tool || it.agentId.value == m.optString("task_id") }
        if (a == null) {
            if (m.optString("subtype") != "task_started" || m.optString("task_type") != "local_agent") return
            a = Subagent(tool, m.optString("description").ifBlank { Wording.SidePanel.subagent }, m.optString("subagent_type"),
                m.optString("prompt"))
            chat.agents.add(0, a)
        }
        m.optString("task_id").ifBlank { null }?.let { if (a.agentId.value == null) a.agentId.value = it }
        m.optJSONObject("usage")?.let { a.usage.value = it }
        val status = m.optString("status").ifBlank { m.optJSONObject("patch")?.optString("status") }
        when (m.optString("subtype")) {
            "task_progress" -> m.optString("description").ifBlank { null }?.let { a.activity.value = it }
            "task_notification", "task_updated" -> if (!status.isNullOrBlank()) a.status.value = status
        }
    }

    /** An artifact added, or what's known of one updated in its place (a known title never made "Claude artifact"). */
    private fun added(chat: Chat, a: Artifact) {
        val i = chat.artifacts.indexOfFirst { it.url == a.url }
        if (i < 0) chat.artifacts.add(0, a)
        else {
            val was = chat.artifacts[i]
            chat.artifacts[i] = Artifact(a.url, if (a.title == Wording.References.claudeArtifact) was.title else a.title,
                a.version ?: was.version, a.icon ?: was.icon, a.description ?: was.description, a.path ?: was.path)
        }
    }

    /** The machine a connection's to (its id, as the mesh knows it), if it's known: this phone's agents, none. */
    private fun machine(connId: String): String? = when {
        connId == "phone" -> null
        connId.startsWith("mesh:") -> connId.removePrefix("mesh:")
        else -> conns[connId]?.info?.id?.takeIf(::isId) ?: hosts.firstOrNull { it.id == connId }?.let { HostStore.machine(app, it) }
    }

    class Draft(val text: String, val files: List<Picked>)
    val drafts = HashMap<String, Draft>() // (a chat left, "host/session": what was written and queued there, as the box has it again)

    private val io = java.util.concurrent.Executors.newSingleThreadExecutor()

    /** What you wrote (and the files you chose) sent to the chat at once, as Baude sends it: a user message for its
     * agent, drawn as yours meanwhile (Sent). If its host says no, it's taken out and put back in the box, and why
     * said. */
    fun send(chat: Chat, text: String, files: List<Picked>) {
        val c = conns[chat.hostId]?.takeIf { it.state == "up" } ?: return
        chat.said.value = null
        Shares.used(chat)
        chat.busy.value = true
        val id = java.util.UUID.randomUUID().toString()
        io.execute {
            val msg = try {
                build(id, text, files)
            } catch (e: Exception) {
                null
            }
            if (msg == null) {
                main.post { chat.busy.value = false }
                return@execute
            }
            val lines = parse(JSONObject().put("type", "user").put("uuid", id).put("message", msg.getJSONObject("message"))
                .toString(), cutFirst = false).filter { it.kind == "you" }
            main.post {
                val sent = Sent(id, lines, text, files)
                chat.sent.add(sent)
                c.request(Msg.Input(session = chat.sid, msg = msg).json()) { r ->
                    main.post {
                        chat.busy.value = false
                        if (r.optString("op") == Op.ERROR) {
                            val e = Msg.Error.from(r)
                            chat.sent.remove(sent)
                            Queue.addDraft(chat.hostId, chat.sid, text, files)
                            chat.said.value = Wording.HeldSend.notSent(why = when (e.error) {
                                Connection.NOT_CONNECTED, Connection.WENT -> Wording.HostStatus.connectionDropped
                                else -> e.said?.ifBlank { null } ?: Wording.BotBrowser.hostSaidNo
                            })
                        }
                        if (open === chat) read(chat)
                    }
                }
            }
        }
    }

    /** A message its host holds for the chat (its held: a subagent's answer that came after its host restarted) sent to
     * its agent (send: ↑) or dropped (✕), as Baude's held_send and held_drop do: by its host, at once, kept if it can't
     * be, and why said. */
    fun held(chat: Chat, id: String, send: Boolean) {
        val c = conns[chat.hostId]
        if (c == null || id in chat.holding) return
        chat.holding.add(id)
        c.request(Msg.HeldOp(session = chat.sid, id = id, send = send).json()) { r ->
            main.post {
                chat.holding.remove(id)
                if (r.optString("op") == Op.ERROR)
                    Toast.makeText(app, Msg.Error.from(r).said.orEmpty().ifBlank { Wording.BotBrowser.hostSaidNo }
                        .let { if (send) Wording.HeldSend.notSent(why = it) else Wording.HeldSend.notDeleted(why = it) },
                        Toast.LENGTH_SHORT).show()
                else read(chat)
            }
        }
    }

    /** The message made (base64 and all, off the main thread), with the uuid it's sent by: null if there's nothing
     * in it. */
    private fun build(id: String, text: String, files: List<Picked>): JSONObject? {
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
        if (blocks.length() == 0) return null
        return JSONObject().put("type", "user").put("uuid", id)
            .put("message", JSONObject().put("role", "user").put("content", blocks))
    }

    // ---- notifications

    private fun channel() {
        if (Build.VERSION.SDK_INT >= 26) {
            val nm = app.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel(MESSAGES, Wording.SharedSettings.notifications, NotificationManager.IMPORTANCE_HIGH)
                .apply { lockscreenVisibility = android.app.Notification.VISIBILITY_PRIVATE }) // (locked: who, not what)
            nm.createNotificationChannel(NotificationChannel(LINKED, Wording.BotBrowser.title, NotificationManager.IMPORTANCE_MIN))
        }
    }

    /** A Bot's turn ended: what it said last (the turn's own result), as a notification (none for the chat that's
     * open in front). */
    private fun ended(c: Connection, msg: JSONObject) {
        val turn = Msg.TurnEnded.from(msg)
        val sid = turn.session.orEmpty()
        val chat = open
        if (chat != null && chat.hostId == c.id && chat.sid == sid) {
            read(chat)
            if (shown) return
        }
        val contact = contact(c.id, sid) ?: return
        val result = turn.result.orEmpty().trim()
        when {
            turn.isError == true -> notify(contact, Wording.Meta.endedError(error = result.ifEmpty { Wording.Meta.errorUnknown }))
            result.isNotEmpty() -> notify(contact, result)
            else -> main.postDelayed({ // (no result said: its transcript's end, once its new size is listed)
                val size = session(c.id, sid)?.size?.toLong() ?: 0
                last(c, sid, size)
            }, 1_200)
        }
    }

    /** Back after a drop: each Bot whose chat grew meanwhile, and is between turns now, notified with what it said
     * last (its transcript's end). */
    private fun missed(c: Connection, before: List<Msg.Session>, now: List<Msg.Session>) {
        for (s in now) {
            if (s.bot == null || s.state.orEmpty() !in setOf("idle", "stopped")) continue
            val old = before.firstOrNull { it.id.orEmpty() == s.id.orEmpty() } ?: continue
            if ((s.size?.toLong() ?: 0) <= (old.size?.toLong() ?: 0)) continue
            val chat = open
            if (shown && chat != null && chat.hostId == c.id && chat.sid == s.id.orEmpty()) continue
            last(c, s.id.orEmpty(), s.size?.toLong() ?: 0)
        }
    }

    private fun last(c: Connection, sid: String, size: Long) {
        val contact = contact(c.id, sid) ?: return
        c.request(Msg.Transcript(session = sid, offset = (size - 64_000).coerceAtLeast(0)).json()) { r ->
            val data = Msg.TranscriptReply.from(r).data.orEmpty()
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
        Settings.load(app)
        if (!Settings.notify.value) return // (Settings → Notifications off)
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
                .setContentText(if (tag != null) Wording.Asks.waiting else Wording.Meta.finished)
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
 * compacted, a background task ended, a turn stopped), and reverts' marks; and those the side pages note, not shown
 * (use: an Artifact or Agent call, result: a call's result, task: a background task's end, link: artifacts' links
 * said). cutFirst: it starts partway into a line, which is left out. sidechain: a subagent's own (its records are). */
fun parse(text: String, cutFirst: Boolean, sidechain: Boolean = false): List<Line> {
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
        if (!sidechain) queued(o, type)?.let { out.add(it) } // (a message sent while a turn ran: transcripts.queued_item)
        record(o, type, uuid, out, sidechain)
        if (uuid != null) out.add(Line("record", "", uuid)) // (where it ends: a revert to it cuts there)
    }
    return out
}

/** A message sent while a turn ran, as Claude Code keeps it (an attachment, queued_command), by its own uuid
 * (source_uuid: what it was sent with, what its sender's kept by); null for any other record. */
private fun queued(o: JSONObject, type: String): Line? {
    if (type != "attachment" || o.optBoolean("isSidechain")) return null
    val a = o.optJSONObject("attachment") ?: return null
    if (a.optString("type") != "queued_command" || a.optBoolean("isMeta") ||
        a.optString("commandMode", "prompt") != "prompt" ||
        a.optJSONObject("origin")?.optString("kind") == "task-notification") return null
    val text = when (val p = a.opt("prompt")) {
        is String -> p
        is JSONArray -> p.list().filter { it.optString("type") == "text" }.joinToString("\n\n") { it.optString("text") }
        else -> ""
    }.let { LEADING_REMINDERS.replace(it, "").trim() }
    if (text.isEmpty()) return null
    return Line("you", text, a.optString("source_uuid").ifBlank { o.optString("uuid") }.ifBlank { null })
}

/** The lines one record shows (none, for most). */
private fun record(o: JSONObject, type: String, uuid: String?, out: MutableList<Line>, sidechain: Boolean) {
    run {
        if (type != "user" && type != "assistant") return
        if (o.optBoolean("isMeta") || (o.optBoolean("isSidechain") && !sidechain)) return
        if (o.optBoolean("isCompactSummary")) {
            out.add(Line("note", Wording.Compaction.compacted, uuid))
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
                            t.startsWith("<task-notification") -> {
                                out.add(Line("note", taskLine(t), uuid))
                                out.add(Line("task", taskNote(t).toString(), uuid))
                            }
                            t.startsWith(STOPPED) -> out.add(Line("note", Wording.Meta.interrupted, uuid))
                            t.contains("<command-name>") -> out.add(Line("you", command(t), uuid))
                            t.isEmpty() || NOT_TYPED.any { t.startsWith(it) } -> {}
                            else -> out.add(Line("you", t, uuid))
                        }
                    } else if (t.isNotBlank()) {
                        out.add(Line(if (o.optBoolean("isApiErrorMessage")) "error" else "bot", t.trim(), uuid))
                        val urls = ARTIFACT_URL.findAll(t).map { it.value }.toList()
                        if (urls.isNotEmpty()) out.add(Line("link", JSONObject().put("urls", JSONArray(urls)).toString(), uuid))
                    }
                }
                "image" -> if (type == "user") out.add(Line("you", "🖼 A picture", uuid))
                "document" -> if (type == "user") out.add(Line("you", "📄 " + b.optString("title", "A document"), uuid))
                "tool_use" -> if (type == "assistant") {
                    out.add(Line("tool", toolLine(b), uuid))
                    val name = b.optString("name")
                    if (isAgent(name) || name.substringAfterLast("__") == "Artifact") out.add(Line("use",
                        JSONObject().put("id", b.optString("id")).put("name", name)
                            .put("input", b.optJSONObject("input") ?: JSONObject()).toString(), uuid))
                }
                "tool_result" -> if (type == "user") out.add(Line("result", JSONObject()
                    .put("id", b.optString("tool_use_id")).put("is_error", b.optBoolean("is_error"))
                    .put("text", resultText(b.opt("content"))).toString(), uuid))
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

fun toolLine(b: JSONObject): String {
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
