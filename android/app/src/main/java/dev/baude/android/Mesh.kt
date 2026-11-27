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
import androidx.compose.ui.graphics.asAndroidBitmap
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

/** One line of a chat as it's shown: yours, the Bot's, what it did (a tool it used: "call", by its id in the chat's
 * Calls), or a note (a background task ended, "tasknote": its call's not drawn; the conversation compacted, an API
 * error). Some aren't shown: "record", the end of a transcript record (uuid: its uuid, shown or not), "rewind", a
 * revert's mark (uuid: the record it keeps last; none, none kept), and "stopped", a turn stopped (a note, unless a call
 * of it says so). */
data class Line(val kind: String, val text: String, val uuid: String? = null, val files: List<Sent> = emptyList())

/** A file or picture sent with a message of yours, as its record has it (kind: "image" or "file"; data: a picture's
 * base64). */
class Sent(val kind: String, val name: String, val mime: String, val data: String?)

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
    var inView = true // (its chat page on screen: not another page swiped to; Mesh.tellSeen)
    var atEnd = false // (its list scrolled to its end, its last line drawn: ChatPage; Mesh.tellSeen)
    val lines = mutableStateListOf<Line>()
    val files = mutableStateListOf<Picked>() // (picked to send: kept as the phone's turned)
    val draft = mutableStateOf<String?>(null) // (text for the box: shared from another app, or kept as the app went)
    val preparing = mutableStateOf(0)         // (picked, still being read)
    val ends = HashMap<String, Int>()         // (a record's uuid: how many lines were shown up to its end)
    val busy = mutableStateOf(false)
    val thinking = mutableStateOf(false)     // (at a turn, where its host doesn't list it: Meta's)
    val said = mutableStateOf<String?>(null)
    val jump = mutableStateOf<String?>(null) // (a call to scroll to: Made by this chat, on the Git page)
    var reverting by mutableStateOf<Reverting?>(null) // (a revert asked, not made: the page for it may be left and come back to)
    val clearBox = mutableStateOf(false) // (a revert made: the box is emptied when next composed)
    val streamingThought = mutableStateOf("") // (and what it's thinking, as it comes)
    val streaming = mutableStateOf("") // (what its agent's writing now, as it comes: till its message is whole)
    val asks = mutableStateListOf<Ask>()      // (what its Bot asks, answered here: Allow, Deny)
    val calls = ChatCalls()                     // (its tool calls, as their cards have them)
    val showAgent = mutableStateOf<String?>(null) // (a call whose subagent's page is asked for: View)
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
    var rotated: Double? = null              // (when its Bot last rotated, as listed as it was first read: one since, and
    var rotatedKnown = false                 // it's read anew, whether the phone was connected then or not)
    var offset = -1L
    val raw = java.io.ByteArrayOutputStream() // (its transcript as read, from rawFrom (a line's start) to offset: kept,
    var rawFrom = -1L                         // LinkCache, to show at once next time)
    var start = -1L                          // (where what's read of its transcript starts: what's before is read as
    val earlier = mutableStateOf(false)      // it's scrolled up to, readEarlier; earlier: there's some)
    var readingEarlier by mutableStateOf(false) // (asked for, or asked again soon: the spinner's up meanwhile)
    val rewound = HashSet<String>()          // (records a rewind went back to, before what's read: cut there once read)
    val orphans = ArrayList<Line>()          // (results whose calls are before what's read: given them once read)
    var prepended: ((Int, Boolean) -> Unit)? = null // (items drawn before the first, and whether earlier was: its list kept where it was)
    var reading = false
    var restoring = false // (what LinkCache kept being read in, off the main thread: read from its host once it's in)
    var era = 0 // (read again from nothing this many times (Mesh.restart): what was asked before it, let go of)
    val loaded = mutableStateOf(false) // (its transcript read to its end once: where it was left can be found)
    var again = false
    val sent = mutableStateListOf<Outgoing>()     // (what you sent that its transcript doesn't show yet: drawn as sent)
    val queued = mutableStateListOf<Queued>() // (written while a turn ran: sent when it ends)
    var queueUpto: Queued? = null             // (↑ on a queued message: the queue is sent up to it at the turn's end)
    var queueRev = -1L                        // (its host's queue: the last revision it's said, Mesh.queueIn)
    var stopAsked = false                     // (the turn was stopped: its queue stays)
    val edit = mutableStateOf<String?>(null)  // (a queued message's revert: text for the box, before what's written)
    var typed = ""                            // (what's in the box, kept as it's written)
    val boxText = mutableStateOf<String?>(null) // (text for the box in place of what's there: its host's draft)
    val head = java.util.UUID.randomUUID().toString() // (its box, as its draft's saves name it: draft_save's head)
    var draftOpen = false   // (its host's draft taken as it's attached: saved from then on, Mesh.saveDraft)
    var draftRev = 0L       // (the last revision of it its host's said)
    var draftText: String? = null // (the markdown its host has, as far as is known here)
    var draftFlight = 0     // (saves on their way)
    var draftTyping = false // (typed, its save not sent yet)
    var draftHeld: Msg.ChatDraft? = null // (another head's, come while this one's own was waiting: Mesh.draftIn)
    var draftDirty = false  // (written here, not taken by its host yet: Mesh's drafts)
    var draftFailed = false // (its last save not given to its host, nothing typed since: saved again, Mesh.saveDraft)
    var draftRetrying = false // (a save to be made again is waiting: Mesh.retryDraft)
    var draftTries = 0      // (saves made again since one was last taken)
    var draftRestoring = false // (what was kept here going back in the box: not written now)
    var putText = ""        // (the box's text as it was last put there, not written: markdown)
    var sender: ((String, List<Picked>) -> Unit)? = null // (one that's sent its own way: Meta's)
    val chrome = Chrome() // (what its status bar, banner and stop button keep)

    fun send(text: String, files: List<Picked>, uuid: String? = null, taken: (() -> Unit)? = null) =
        sender?.invoke(text, files) ?: Mesh.send(this, text, files, uuid = uuid, taken = taken)
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
    val deactivated = mutableStateListOf<String>() // (connection ids switched off: a host's id, or "mesh:<id>")
    val infos = mutableStateMapOf<String, Msg.Info>() // (a host of yours: what its host process last said it is,
    // its mesh too: Mesh Versions)
    val members = mutableStateMapOf<String, Pair<Msg.Member, String?>>() // (a member of a host's mesh, by its id: its
    // record, as the host it's reached through lists it (id, name, host, hops, through), and that host's label)
    @Volatile private var routes = emptyMap<String, Connection>() // (a member: the host that's up and reaches it in
    // fewest hops)
    private val listedBy = HashMap<String, MutableSet<String>>() // (a host of yours: the members it's listed)
    private val moved = HashMap<String, String>() // (a member's connection id: the host of yours its machine is now)
    private val askNotes = HashMap<String, MutableSet<String>>() // (a chat, "connection/session": the asks notified)
    private val toldSeen = HashMap<String, String>() // (a chat, "connection/session": the turn's end its host was told
    // was seen here, by its uuid)
    private val listedAt = HashMap<String, Long>() // (a connection: when it last listed its sessions, elapsedRealtime)
    var open: Chat? = null
    var shown = false // (the app in front: no notification for the chat open)
    private var visible = false // (the app on screen: the chat open answers what it asks)
    private val conns = HashMap<String, Connection>()
    private val live = HashSet<String>() // (hosts whose sessions are as they listed them on this link, not kept from before)
    private val heardHere = HashSet<String>() // (hosts that have listed their sessions since the app started)
    private val keptStates = HashMap<String, List<Msg.Session>>() // (a host's sessions as LinkCache kept them, turns and
    // all, till it lists them: what a queue left at a turn is sent by, Queue.sendLeft)
    private val catchUp = HashSet<String>() // (hosts back after a drop: Bots' turns that ended meanwhile, notified)
    private var started = false

    fun start(ctx: Context) {
        if (started) return
        started = true
        app = ctx.applicationContext
        WireStrict.look(app)
        readKey()
        hosts.addAll(HostStore.load(app))
        deactivated.addAll(HostStore.deactivated(app))
        Queue.loadDrafts(app.filesDir)
        LinkCache.start(app.filesDir)
        loadCache()
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
                        main.post { conns.values.forEach { it.resume(fresh = false) } }
                    }
                    override fun onBlockedStatusChanged(network: android.net.Network, blocked: Boolean) { // (Doze over,
                        if (!blocked) main.post { conns.values.forEach { it.resume(fresh = false) }; PhoneLink.resume() } // or Data Saver)
                    }
                })
        } catch (_: Exception) {
        }
    }

    /** (the app started) The sessions your hosts last listed, as LinkCache kept them: listed at once, as they were
     * last seen, till each host's reached; between turns, since what was at a turn then may not be now. */
    private fun loadCache() {
        for ((h, l) in LinkCache.sessions(hosts.map { it.id })) if (sessions[h] == null) {
            keptStates[h] = l
            sessions[h] = l.map { s ->
                if (s.state != "working" && s.state != "waiting" && s.asks.isNullOrEmpty()) s
                else Msg.Session.from(s.json().put("state", "idle").put("asks", JSONArray()))
            }
        }
    }

    /** Whether a host's reached: "up"; "away" while it's been gone less than LEEWAY_MS (since it first went, or since
     * the app was brought back), or is being reached again, a link Android closed (Doze, a network change, the app put away)
     * being made again; "gone" past that, or for a reason a retry won't fix (its key, its sign-in, no Baude there).
     * Only gone is said plainly. */
    fun reach(id: String): String {
        val c = conns[id] ?: return "gone"
        if (c.state == "up") return "up"
        if (c.state == "key" || !passing(c.why)) return "gone"
        val since = c.awaySince.takeIf { it > 0 } ?: return "away"
        return if (android.os.SystemClock.elapsedRealtime() - since < LEEWAY_MS) "away" else "gone"
    }

    /** Is why a reason a link goes and comes back (the network, the host's machine asleep or restarting), not one that
     * needs you (null: none said yet)? */
    private fun passing(why: String?): Boolean = why == null || listOf("authorized_keys", "couldn't sign", "Baude commands",
        "key").none { why.contains(it) }

    // ---- hosts

    /** The Keystore's key read (again): none, or why it couldn't be read, which isn't the same (it's never made anew
     * for that). */
    fun readKey() {
        try {
            key.value = Keys.load()
            keyError.value = null
        } catch (e: Exception) {
            keyError.value = Wording.PhoneKeys.unreadable(why = e.message ?: e.javaClass.simpleName)
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
                }.onFailure { keyError.value = Wording.PhoneKeys.notMade(why = it.message ?: it.javaClass.simpleName) }
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
        val machine = conns[host.id]?.info?.id?.takeIf(::isId) ?: HostStore.machine(app, host)
        if (machine != null) HostStore.setRemoved(app, machine, true) // (nor reached again as a member another host lists)
        hosts.removeAll { it.id == host.id }
        HostStore.save(app, hosts)
        if (hosts.none { it.address == host.address && it.port == host.port }) HostStore.forget(app, host)
        conns.remove(host.id)?.stop()
        states.remove(host.id)
        sessions.remove(host.id)
        keptStates.remove(host.id)
        LinkCache.forget(host.id)
        listedBy.remove(host.id)
        moved.values.removeAll { it == host.id }
        meshed()
        Shares.look()
    }

    /** A host (or a member, "mesh:<id>") switched off, or on again: not reached while off, its chats listed as last seen
     * (Baude's Network.deactivate). */
    fun deactivate(id: String, off: Boolean) {
        if (off) { if (id !in deactivated) deactivated.add(id) } else deactivated.remove(id)
        HostStore.setDeactivated(app, deactivated)
        if (off) {
            conns.remove(id)?.stop()
            states.remove(id)
        } else if (id.startsWith("mesh:")) meshed()
        else hosts.firstOrNull { it.id == id }?.let { declined.removeAll { d -> d.startsWith("${it.id} ") }; connect(it) }
        tick.value++
    }

    /** A host's or a member's name here (Mesh's Rename): a host's, as it was added; a member's, kept by its id. Empty:
     * its own again. */
    fun rename(id: String, name: String) {
        val i = hosts.indexOfFirst { it.id == id }
        if (i >= 0) {
            hosts[i] = hosts[i].copy(name = name.trim())
            HostStore.save(app, hosts)
        } else HostStore.setAlias(app, id.removePrefix("mesh:"), name)
        tick.value++
    }

    /** A host or a member removed here (Mesh's Remove): a host of yours, as Hosts' Remove does; a member, not reached
     * or listed here from now on, even while a host of yours lists it, till its machine's added as a host. Nothing on
     * it changes. */
    fun remove(id: String) {
        val h = hosts.firstOrNull { it.id == id }
        if (h != null) return removeHost(h)
        val member = id.removePrefix("mesh:")
        HostStore.setRemoved(app, member, true)
        conns["mesh:$member"]?.let { drop(it) }
        members.remove(member)
        tick.value++
    }

    fun connect(host: Host) {
        conns.remove(host.id)?.stop()
        if (key.value == null || host.id in deactivated) return
        start(Connection(app, Place.Direct(host), ::identify, { routes[it] }, ::heard) { conn -> main.post { changed(conn) } })
    }

    private fun start(c: Connection) {
        conns[c.id] = c
        states[c.id] = c
        c.start()
    }

    /** Where a connection's id is, as you'd say it: this phone's own host, local (as Baude names its own machine's); a
     * host's name, or a member's (the one it's given here, if it's been renamed). */
    fun label(id: String): String {
        if (id == "phone") return Wording.Senders.local
        (hosts.firstOrNull { it.id == id } ?: conns[id]?.host)?.let { h ->
            return h.name.ifBlank { null } ?: infos[id]?.name?.ifBlank { null }
                ?: conns[id]?.info?.name?.ifBlank { null } ?: h.address
        }
        HostStore.alias(app, id.removePrefix("mesh:"))?.let { return it }
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
        val removed = HostStore.removed(app)
        val best = HashMap<String, Pair<Msg.Member, Connection>>()
        for (h in hosts) {
            val c = conns[h.id]?.takeIf { it.state == "up" } ?: continue
            val seen = listedBy.getOrPut(h.id) { HashSet() }
            for (m in c.info?.mesh?.members.orEmpty()) {
                val id = m.id?.takeIf(::isId) ?: continue
                seen.add(id)
                if (id in known || heldBy(m) != null || id == PhoneHost.id || id in removed) continue // (this phone, as
                // the mesh has it; one removed here)
                val now = best[id]
                if (now == null || (m.hops?.toInt() ?: 99) < (now.first.hops?.toInt() ?: 99)) best[id] = m to c
            }
        }
        routes = best.mapValues { it.value.second }
        for ((id, pair) in best) {
            members[id] = pair.first to pair.second.host?.label
            if ("mesh:$id" in deactivated) continue
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
        if (c.state != "up") live.remove(c.id)
        if (c.state == "down") Inbox.down(c)
        if (c.state != "up") main.postDelayed({ tick.value++ }, LEEWAY_MS + 100) // (said plainly once it's been gone
                                                                                 // that long)
        PhoneBrowser.changed(c)
        if (c.state == "up") {
            c.host?.let { h ->
                c.info?.id?.takeIf(::isId)?.let {
                    HostStore.setMachine(app, h, it)
                    if (it in HostStore.removed(app)) HostStore.setRemoved(app, it, false) // (added as a host again)
                }
            }
            if (c.id in heardHere) catchUp.add(c.id) // (not on a cold start: what grew before it isn't news)
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

    /** Every host and member of the mesh known here, up or not (as Baude's New session dropdown lists them), that takes
     * op if it's given and it's up (takes): (id, label, whether it's up), your hosts first. */
    fun known(op: String? = null): List<Triple<String, String, Boolean>> = (hosts.map { it.id } + conns.keys).distinct()
        .map { it to (conns[it]?.state == "up") }
        .filter { (id, up) -> !up || op == null || takes(id, op) }
        .sortedWith(compareBy({ conns[it.first]?.place is Place.Member }, { label(it.first).lowercase() }))
        .map { (id, up) -> Triple(id, label(id), up) }

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
            if (!started) { // (a link SeenJob made, the app not started: only what's been seen heard)
                if (msg.optString("op") == Op.SESSIONS) Msg.Sessions.from(msg).sessions.orEmpty().let { now ->
                    sessions[c.id] = now
                    withdrawSeen(c.id, now)
                    listedAt[c.id] = android.os.SystemClock.elapsedRealtime()
                }
                return@post
            }
            for (l in listeners.toList()) l(c.id, msg)
            when (msg.optString("op")) {
                Op.SESSIONS -> {
                    val listing = Msg.Sessions.from(msg)
                    val now = listing.sessions.orEmpty()
                    val before = sessions[c.id]
                    val was = open?.takeIf { it.hostId == c.id }?.let { isWorking(it) } == true
                    val left = keptStates.remove(c.id) ?: before
                    sessions[c.id] = now
                    live.add(c.id)
                    heardHere.add(c.id)
                    if (c.place is Place.Direct) LinkCache.keepSessions(hosts.mapNotNull { h -> sessions[h.id]?.let { h.id to it } }.toMap())
                    open?.takeIf { was && it.hostId == c.id && !isWorking(it) }?.let { Queue.flush(it) } // (a turn ended: what's queued sent)
                    Queue.sendLeft(c.id, left, now) // (and the queues of chats left at a turn that's ended)
                    Shares.look() // (its Bots, in the share sheet)
                    listing.server?.let { // (its mesh as it is now)
                        val restart = c.info?.restart?.json()?.toString()
                        c.info = it
                        if (it.restart?.json()?.toString() != restart) tick.value++ // (a pending restart's waits, drawn as they are now)
                        if (c.place is Place.Direct) {
                            infos[c.id] = it
                            meshed()
                        }
                    }
                    if (catchUp.remove(c.id) && before != null) missed(c, before, now)
                    asked(c, before, now)
                    Inbox.listed(c) // (its asks of a person, once each link: then kept as inbox_changed says)
                    withdrawSeen(c.id, now)
                    listedAt[c.id] = android.os.SystemClock.elapsedRealtime()
                    open?.let {
                        if (it.hostId != c.id) return@let
                        val listed = now.firstOrNull { s -> s.id == it.sid }
                        val rotated = listed?.rotated?.toDouble()
                        if (listed == null) Unit // (not listed now: nothing to go by)
                        else if (!it.rotatedKnown) {
                            it.rotated = rotated
                            it.rotatedKnown = true
                        } else if (rotated != it.rotated) { // (a Bot rotated, since it was first read: its new
                            it.rotated = rotated            // conversation, read from nothing)
                            restart(it)
                            it.sent.clear()
                            it.asks.clear()
                            it.sendersRev = -1
                            it.senders.clear()
                            it.again = it.reading
                        }
                        senders(it) // (who its messages from others are from, if its host's store is newer)
                        read(it) // (its first read, or what it missed)
                        tellSeen(it)
                    }
                }
                Op.TURN_ENDED -> ended(c, msg)
                Op.INBOX_CHANGED -> Inbox.changed(c.id, Msg.InboxChanged.from(msg))
                Op.LOGIN_STATE -> open?.let { // (the open chat's sign-in, as its host has it: its card, as on every head)
                    val said = Msg.LoginState.from(msg)
                    if (it.hostId == c.id && it.sid == said.session.orEmpty()) main.post { if (open === it) Logins.hostSaid(it, said.login) }
                }
                Op.DRAFT_CHANGED -> open?.let { // (the open chat's draft, another head's write)
                    val said = Msg.DraftChanged.from(msg)
                    if (it.hostId == c.id && it.sid == said.session.orEmpty()) main.post { if (open === it) draftIn(it, said.draft) }
                }
                Op.QUEUE_CHANGED -> open?.let { // (the open chat's queue, another head's change or its host sending it)
                    if (it.hostId == c.id && it.sid == msg.optString("session"))
                        main.post { if (open === it) queueIn(it, msg.optJSONObject("queue")) }
                }
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
            if (m.optString("type") in setOf("assistant", "result")) {
                chat.streaming.value = ""
                chat.streamingThought.value = ""
            }
            return
        }
        if (!m.isNull("parent_tool_use_id")) return
        val e = m.optJSONObject("event") ?: return
        when (e.optString("type")) {
            "message_start" -> {
                chat.streaming.value = ""
                chat.streamingThought.value = ""
            }
            "content_block_delta" -> e.optJSONObject("delta")?.let {
                when (it.optString("type")) {
                    "text_delta" -> chat.streaming.value += it.optString("text")
                    "thinking_delta" -> chat.streamingThought.value += it.optString("thinking")
                }
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
        if (!allow && answers == null) {
            val call = ask.toolUseId?.let { chat.calls.byId[it] }
            val questions = ask.tool == "AskUserQuestion" && ask.input.optJSONArray("questions") != null
            if (call != null) {
                if (!questions) call.decision = "denied"
            } else chat.lines.add(Line("note", if (questions) Wording.ToolCalls.notAnsweredTool(tool = toolTitle(ask.tool))
                else Wording.ToolCalls.deniedTool(tool = toolTitle(ask.tool))))
        }
        if (answers != null) ask.toolUseId?.let { chat.calls.byId[it] }?.input = JSONObject(ask.input.toString()).put("answers", answers)
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
                    chat.said.value = Wording.Asks.notAlwaysAllowed(tool = toolTitle(ask.tool), why = Msg.Error.from(r).said.orEmpty())
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

    /** A Bot's picture, asked of its host once for each time it changes; then(), once it's come or couldn't be got
     * (not if it was already asked for). */
    fun picture(c: Contact, then: (() -> Unit)? = null) {
        if (c.pictureKey in pictures || c.session.bot?.picture == null) return
        pictures[c.pictureKey] = null
        val conn = conns[c.hostId] ?: return run { pictures.remove(c.pictureKey); then?.invoke() }
        conn.request(Msg.BotPicture(session = c.id).json()) { r ->
            val data = Msg.BotPictureReply.from(r).picture.orEmpty()
            val bytes = if (data.isEmpty()) null else Base64.getDecoder().decode(data)
            val bmp = bytes?.let { BitmapFactory.decodeByteArray(it, 0, it.size)?.asImageBitmap() }
            main.post {
                if (bmp == null) pictures.remove(c.pictureKey) // (asked again next time it's drawn)
                else {
                    pictures[c.pictureKey] = bmp
                    Shares.look()
                }
                then?.invoke()
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
        session(hostId, sid)?.let { // (when its Bot last rotated, as it's listed now: what's read is of that)
            chat.rotated = it.rotated?.toDouble()
            chat.rotatedKnown = true
        }
        Queue.takeDraft(hostId, sid, machine(hostId))?.let { d ->
            chat.draft.value = d.text.ifBlank { null }
            chat.draftDirty = d.dirty && d.text.isNotBlank()
            chat.draftRestoring = d.text.isNotBlank()
            chat.files.addAll(d.files)
            chat.queued.addAll(d.queue) // (its queue under it again; its ↑ and stop for the turn going on, if one is)
            if (isWorking(chat)) {
                chat.queueUpto = d.upto?.let { chat.queued.getOrNull(it) }
                chat.stopAsked = d.hold
            }
        }
        open = chat
        chat.restoring = LinkCache.has(hostId, sid)
        if (chat.restoring) LinkCache.chat(hostId, sid, TAIL) { kept -> // (as it was last read, its last TAIL of it,
            val lines = kept?.let { parse(String(it.second, Charsets.UTF_8), cutFirst = false) } // off the main thread:
            main.post {                                                                          // shown once it's in,
                chat.restoring = false                                                           // read on from its end;
                if (kept != null && open === chat && chat.offset < 0) {                          // what's before it, as
                    val (from, bytes) = kept                                                     // it's scrolled up to)
                    chat.rawFrom = from
                    chat.raw.write(bytes)
                    chat.start = from
                    chat.earlier.value = from > 0
                    chat.offset = from + bytes.size
                    take(chat, lines!!)
                    settle(chat.calls, session(hostId, sid)?.state)
                    settleTasks(chat)
                    chat.loaded.value = true
                }
                if (open === chat) read(chat)
            }
        }
        attach(chat)
        senders(chat)
        read(chat)
        NotificationManagerCompat.from(app).cancel(turnTag(hostId, sid), TURN_NOTE)
        return chat
    }

    fun close() {
        val chat = open ?: return
        flushDraft(chat)
        Queue.keepDraft(chat)
        LinkCache.keepChat(chat)
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
        if (now) { // (back: each link made again now, or checked, and given its leeway again)
            conns.values.forEach { it.resume(fresh = true) }
            PhoneLink.resume()
            tick.value++
            main.postDelayed({ tick.value++ }, LEEWAY_MS + 100)
        } else open?.let { LinkCache.keepChat(it) } // (what's on screen kept: Android may end the app while it's away)
        open?.let { attach(it) }
    }

    /** The chat attached to live, as one that answers what it asks while the app's on screen (its asks, as they are,
     * shown to answer). */
    private fun attach(chat: Chat) {
        conns[chat.hostId]?.request(Msg.Attach(session = chat.sid, live = true, answers = visible).json()) { r ->
            if (r.optString("op") == Op.ERROR && Msg.Error.from(r).said.orEmpty().startsWith("no session"))
                main.post { chat.chrome.missing.value = true } // (its host there, but with no such session)
            if (r.optString("op") != Op.ATTACHED) return@request
            val attached = Msg.Attached.from(r)
            main.post {
                chat.chrome.missing.value = false
                chat.chrome.writes.value = attached.writes != false
                chat.chrome.keepsLogin = r.has("login") // (an older host's doesn't: the card worked out here)
                if (open === chat && attached.writes != false) draftAttached(chat, attached.draft)
                if (open === chat && attached.writes != false) queueIn(chat, r.optJSONObject("queue"))
            }
            val asks = attached.asks.orEmpty().mapNotNull(Ask::of)
            main.post {
                if (open !== chat) return@post
                chat.asks.clear()
                chat.asks.addAll(asks)
                attached.login?.let { Logins.hostSaid(chat, it) } // (its sign-in, as its host has it: its card)
            }
        }
    }

    // ---- a chat's draft: its text kept by its host (draft_save), the same on every head that opens it, on any
    // machine. What's written here that its host hasn't taken yet is dirty: typed, pasted or put in by hand, till its
    // host answers a save of that very text. Saved DRAFT_SAVE_MS after typing stops, at once as the box is emptied
    // (sent, queued) and as the chat's left; another head's save comes as draft_changed. The last write wins: one
    // that comes while this box is dirty or has a save on its way is held, and put in once it's neither, if its host
    // had it after this box's. Typing its host never took is never replaced by another's write: a save it refused as
    // too long (DRAFT_CHARS_MAX) stays this box's, not saved again, another's write held as before; one it couldn't
    // be given is saved again DRAFT_RETRY_MS later, twice as long each time, DRAFT_RETRIES times (WindowDrafts' too),
    // another's write held meanwhile, and as the chat's attached again. Only a dirty copy kept here is ever saved
    // over its host's as the chat's opened. What's attached stays this phone's (Queue's drafts)

    const val DRAFT_SAVE_MS = 400L
    const val DRAFT_RETRY_MS = 1000L
    const val DRAFT_RETRIES = 3

    /** Does chat's host keep its draft for it (a host's chat, not a new one nor a Claude Desktop chat, this phone let
     * write it, its host's draft taken)? */
    fun keepsDraft(chat: Chat) = chat.sender == null && chat.draftOpen && chat.chrome.writes.value &&
        session(chat.hostId, chat.sid)?.let { it.draft != true && it.desktop != true } != false && conns[chat.hostId] != null

    /** What chat's box has, as its host keeps it (markdown, as it's sent). */
    fun boxMarkdown(chat: Chat) = toMarkdown(chat.boxText.value // (its host's, on its way into the box)
        ?: listOf(chat.typed, chat.draft.value.orEmpty()).filter { it.isNotBlank() }.joinToString("\n"))

    /** (attached) Its host's draft in the box as the chat's opened, unless what was kept here is dirty (saved to it
     * instead), or its host has none (what's here saved to it); attached again (its host back), what's dirty saved,
     * else what its host has had since. */
    private fun draftAttached(chat: Chat, d: Msg.ChatDraft?) {
        val listed = session(chat.hostId, chat.sid)
        if (chat.sender != null || listed?.draft == true || listed?.desktop == true) return
        val box = boxMarkdown(chat)
        if (chat.draftOpen) {
            if (chat.draftDirty) saveDraft(chat, box) else draftIn(chat, d)
            return
        }
        chat.draftOpen = true
        if (d == null) {
            if (box.isNotBlank()) {
                chat.draftDirty = true
                saveDraft(chat, box)
            }
            return
        }
        chat.draftRev = d.rev?.toLong() ?: 0L
        chat.draftText = d.text.orEmpty()
        if (chat.draftDirty && box != chat.draftText) return saveDraft(chat, box)
        chat.draftDirty = false
        if (box != chat.draftText) {
            chat.draft.value = null
            chat.boxText.value = chat.draftText
        }
    }

    /** Where offset at in old is in new, the change between them the part neither's start nor end shares. */
    fun movedOffset(old: String, new: String, at: Int): Int {
        val most = minOf(old.length, new.length)
        var start = 0
        while (start < most && old[start] == new[start]) start++
        var end = 0
        while (end < most - start && old[old.length - 1 - end] == new[new.length - 1 - end]) end++
        return when {
            at <= start -> at
            at >= old.length - end -> (at + new.length - old.length).coerceIn(0, new.length)
            else -> new.length - end
        }
    }

    /** What's in chat's box (markdown) saved to its host: clean once its host's taken that very text and nothing's
     * been typed since. */
    fun saveDraft(chat: Chat, markdown: String) {
        val c = conns[chat.hostId] ?: return
        if (!keepsDraft(chat)) return
        val key = "${chat.hostId}/${chat.sid}"
        chat.draftText = markdown
        chat.draftFailed = false
        chat.draftFlight++
        c.request(Msg.DraftSave(session = chat.sid, text = markdown, head = chat.head).json()) { r ->
            main.post {
                chat.draftFlight--
                val took = r.optString("op") == Op.DRAFT_SAVED
                if (took) chat.draftRev = maxOf(chat.draftRev, Msg.DraftSaved.from(r).draft?.rev?.toLong() ?: 0L)
                if (open !== chat) { // (left: what was kept of it clean, if this was what it had)
                    val kept = drafts[key]
                    if (took && kept != null && toMarkdown(kept.text) == markdown)
                        Queue.setDraft(key, Draft(kept.text, kept.files, kept.queue, kept.upto, kept.hold, dirty = false))
                    return@post
                }
                if (!took) { // (not kept there: still dirty, this box's, and held against others'. Too long: stays so, as
                    chat.draftText = null // its host will never take it. Else saved again soon, and as it's opened or attached)
                    if (chat.draftFlight == 0 && !chat.draftTyping && boxMarkdown(chat) == markdown &&
                        r.optString("code") != WireCheck.INVALID) {
                        chat.draftFailed = true
                        retryDraft(chat, markdown)
                    }
                    return@post
                }
                chat.draftTries = 0
                if (chat.draftFlight == 0 && !chat.draftTyping && boxMarkdown(chat) == markdown) {
                    chat.draftDirty = false
                    releaseHeld(chat)
                }
            }
        }
    }

    /** (a save of markdown its host didn't take) Saved again in a while, unless it's been tried DRAFT_RETRIES times,
     * or the box has changed, another's write come or the chat's been left meanwhile. */
    private fun retryDraft(chat: Chat, markdown: String) {
        if (chat.draftTries >= DRAFT_RETRIES || chat.draftRetrying) return
        val wait = DRAFT_RETRY_MS shl chat.draftTries++
        chat.draftRetrying = true
        main.postDelayed({
            chat.draftRetrying = false
            if (open === chat && chat.draftFailed && chat.draftFlight == 0 && !chat.draftTyping &&
                boxMarkdown(chat) == markdown) saveDraft(chat, markdown)
        }, wait)
    }

    /** Another head's write held while this box was dirty or saving, put in now it's neither. */
    fun releaseHeld(chat: Chat) {
        val held = chat.draftHeld ?: return
        if (chat.draftFlight > 0 || chat.draftTyping || chat.draftDirty) return
        chat.draftHeld = null
        draftIn(chat, held)
    }

    /** (the chat being left) Dirty typing saved to its host now. */
    fun flushDraft(chat: Chat) {
        chat.draftTyping = false
        if (chat.reverting == null && chat.draftDirty && keepsDraft(chat)) saveDraft(chat, boxMarkdown(chat))
    }

    /** Another head's write of chat's draft (draft_changed), or its host's as it's attached again: in the box, unless
     * this box is dirty or saving (held: put in after, if its host had it after this box's). */
    fun draftIn(chat: Chat, d: Msg.ChatDraft?) {
        val rev = d?.rev?.toLong() ?: return
        if (!chat.draftOpen || rev <= chat.draftRev) return // (one seen already, or older than one that has been)
        if (d.head == chat.head) { // (this box's own save, back)
            chat.draftRev = rev
            return
        }
        if (chat.draftFlight > 0 || chat.draftTyping || chat.draftDirty || chat.reverting != null) {
            if ((chat.draftHeld?.rev?.toLong() ?: -1L) < rev) chat.draftHeld = d
            return
        }
        chat.draftRev = rev
        chat.draftText = d.text.orEmpty()
        if (boxMarkdown(chat) != chat.draftText) {
            chat.draft.value = null
            chat.boxText.value = chat.draftText
        }
    }

    // ---- a chat's queue: kept by its host (hostqueue), the same on every head that opens it, on any machine. What's
    // queued here is added there, and what's sent, edited or deleted here dropped there; its host leads it, sending it
    // as the turn ends, so every head shows it and it's sent once. One its host won't take stays this phone's

    /** Does chat's host keep its queue (a host's chat this phone may write, not a new one nor a Claude Desktop chat)? */
    fun keepsQueue(chat: Chat) = chat.sender == null && chat.chrome.writes.value && conns[chat.hostId]?.state == "up" &&
        session(chat.hostId, chat.sid)?.let { it.draft != true && it.desktop != true } != false

    private fun queueOp(chat: Chat, op: JSONObject) {
        val c = conns[chat.hostId] ?: return
        c.request(op.put("op", Op.QUEUE).put("session", chat.sid)) { r ->
            main.post { if (open === chat && r.optString("op") == Op.QUEUE) queueIn(chat, r.optJSONObject("queue")) }
        }
    }

    /** A message just queued, added to its host's queue (its content made off the main thread). */
    fun queueAdd(chat: Chat, q: Queued) {
        val id = q.uuid ?: return
        if (!keepsQueue(chat)) return
        val c = conns[chat.hostId] ?: return
        q.hosted = 1
        io.execute {
            val content = try { build(id, q.text, q.files)?.getJSONObject("message")?.get("content") } catch (e: Exception) { null }
            main.post {
                if (content == null) {
                    q.hosted = 0
                    return@post
                }
                val add = JSONObject().put("id", id).put("text", q.text).put("content", content)
                c.request(JSONObject().put("op", Op.QUEUE).put("session", chat.sid).put("add", JSONArray().put(add))) { r ->
                    main.post {
                        if (r.optString("op") != Op.QUEUE) { // (not taken: this phone's to send, as its turn ends)
                            if (q.hosted == 1) q.hosted = 0
                        } else {
                            val has = r.optJSONObject("queue")?.optJSONArray("turns").list().any { it.optString("id") == id }
                            if (open === chat) queueIn(chat, r.optJSONObject("queue"))
                            // (its host's queue has no row for it: sent at once, as no turn was going)
                            if (q.hosted == 1) { if (has && open !== chat) q.hosted = 2 else chat.queued.remove(q) }
                        }
                    }
                }
            }
        }
    }

    /** Queued messages sent, edited or deleted here: dropped from its host's queue. */
    fun queueDrop(chat: Chat, qs: List<Queued>) {
        val ids = qs.filter { it.hosted != 0 && it in chat.queued }.mapNotNull { it.uuid }
        if (ids.isNotEmpty()) queueOp(chat, JSONObject().put("drop", JSONArray(ids)))
    }

    /** (an interrupt about to be sent) The queued message ↑ was pressed on, its host's: named to its host, which sends it
     * and those before it as the turn ends, not this phone. */
    fun queueUptoHost(chat: Chat) {
        val q = chat.queueUpto ?: return
        if (q.hosted == 0 || q.uuid == null) return
        chat.queueUpto = null
        queueOp(chat, JSONObject().put("upto", q.uuid))
    }

    /** The open chat's queue as its host has it, under the conversation: what's gone from it gone here, what's new in
     * it put in its place, what's being added (and what its host won't take) left as it is. */
    fun queueIn(chat: Chat, q: JSONObject?) {
        if (q == null || chat.sender != null) return
        val rev = q.optLong("rev")
        if (rev < chat.queueRev) return
        chat.queueRev = rev
        val turns = q.optJSONArray("turns").list()
        val ids = turns.map { it.optString("id") }.toSet()
        chat.queued.removeAll { it.hosted == 2 && it.uuid !in ids }
        var at = 0
        for (t in turns) {
            val id = t.optString("id")
            val here = chat.queued.indexOfFirst { it.uuid == id }
            if (here >= 0) {
                chat.queued[here].hosted = 2
                at = here + 1
                continue
            }
            val files = (t.opt("content") as? JSONArray).list().mapNotNull { b ->
                val src = b.optJSONObject("source")?.takeIf { it.optString("type") == "base64" } ?: return@mapNotNull null
                val bytes = try { java.util.Base64.getDecoder().decode(src.optString("data")) } catch (_: Exception) { return@mapNotNull null }
                when (b.optString("type")) {
                    "image" -> Picked("image", src.optString("media_type"), bytes)
                    "document" -> Picked(b.optString("title").ifBlank { "document.pdf" }, "application/pdf", bytes)
                    else -> null
                }
            }
            chat.queued.add(at.coerceAtMost(chat.queued.size), Queued(t.optString("text"), files, id).also { it.hosted = 2 })
            at++
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

    /** More of the chat's transcript read (its last part, the first time: a long one isn't all fetched; what's before
     * it is read as it's scrolled up to, readEarlier). */
    fun read(chat: Chat) {
        if (chat.restoring) return // (read once what's kept is in)
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
            val bytes = if (data.isEmpty()) ByteArray(0) else Base64.getDecoder().decode(data)
            val text = String(bytes, Charsets.UTF_8)
            val at = got.offset?.toLong() ?: from // (where it read from: the start, if from's past its end)
            val next = got.next?.toLong() ?: at
            val more = next < (got.size?.toLong() ?: next)
            val lines = parse(text, cutFirst = chat.offset < 0 && at > 0)
            val first = if (at == 0L) 0L else bytes.indexOf('\n'.code.toByte()).let { if (it < 0) next else at + it + 1 }
            main.post {
                if (r.optString("op") == Op.TRANSCRIPT && chat.offset > 0 && chat.offset == from && at != from) {
                    restart(chat) // (its transcript's shorter than what's read: made anew on its host, read anew)
                    chat.reading = false
                    read(chat)
                    return@post
                }
                if (r.optString("op") == Op.TRANSCRIPT && (chat.offset < 0 || chat.offset == from)) {
                    if (chat.start < 0) {
                        chat.start = first
                        chat.earlier.value = first > 0
                    }
                    if (chat.rawFrom < 0) {
                        chat.rawFrom = first
                        if (first - at < bytes.size) chat.raw.write(bytes, (first - at).toInt(), bytes.size - (first - at).toInt())
                    } else chat.raw.write(bytes)
                    LinkCache.trim(chat)
                    chat.offset = next
                    take(chat, lines)
                }
                chat.reading = false
                if (more || chat.again) {
                    chat.again = false
                    read(chat)
                } else {
                    settle(chat.calls, session(chat.hostId, chat.sid)?.state)
                    settleTasks(chat)
                    chat.loaded.value = true
                    if (open === chat && conns[chat.hostId]?.state == "up" && chat.hostId in live && !isWorking(chat))
                        Queue.resend(chat) // (back, and caught up: what waited for it)
                    tellSeen(chat)
                }
            }
        }
    }

    /** A chat read again from nothing: what was read of it let go of. */
    private fun restart(chat: Chat) {
        chat.era++ // (what's before it, being read: let go of, as it's of the transcript as it was)
        chat.readingEarlier = false
        chat.lines.clear()
        chat.ends.clear()
        chat.raw.reset()
        chat.rawFrom = -1
        chat.offset = -1
        chat.start = -1
        chat.earlier.value = false
    }

    /** Lines of the chat's transcript, in order, taken into what it shows and keeps. */
    private fun take(chat: Chat, lines: List<Line>) {
        for (line in lines) {
            when (line.kind) {
                "cost" -> line.text.toDoubleOrNull()?.let { chat.chrome.cost.value = it; chat.chrome.costSeen = it }
                "call", "stopped" -> took(chat.calls, chat.lines, line)
                "result", "task" -> {
                    if (line.kind == "result" && chat.start > 0) { // (its call before what's read: kept for it)
                        val id = try { JSONObject(line.text).optString("id") } catch (_: Exception) { "" }
                        if (id.isNotEmpty() && chat.calls.byId[id] == null) chat.orphans.add(line)
                    }
                    noted(chat, line)
                    took(chat.calls, chat.lines, line)
                    tasksFrom(chat, line)
                }
                "use", "link" -> noted(chat, line)
                "compact" -> {
                    CompactMarks.dropLive(chat)
                    chat.lines.add(line)
                }
                "compactsum" -> CompactMarks.addSummary(chat, line)
                "record" -> line.uuid?.let { chat.ends[it] = chat.lines.size }
                "rewind" -> { // (what's after the record it keeps last dropped: all of what's read, if
                    val keep = line.uuid?.let { chat.ends[it] } ?: 0 // that's before it)
                    if (chat.start > 0) line.uuid?.let { if (chat.ends[it] == null) chat.rewound.add(it) }
                    while (chat.lines.size > keep) chat.lines.removeAt(chat.lines.size - 1)
                    chat.ends.values.removeAll { it > keep }
                }
                else -> {
                    if (line.kind == "you") {
                        chat.sent.removeAll { it.id == line.uuid }
                        if (line.uuid != null) chat.queued.removeAll { it.tried && it.uuid == line.uuid } // (taken
                    }                                                                                    // as its link went)
                    if (line.kind == "error" && chat.chrome.loginStandsIn && needsLogin(line.text)) {
                        chat.chrome.loginStandsIn = false // (its sign-in card's in its place already)
                        Logins.stoodFor(chat, line.text)
                    }
                    else chat.lines.add(line)
                }
            }
        }
    }

    /** The part of the chat's transcript before what's read (up to window of it), read and put before its lines, as
     * the desktop's history reads further back as it's scrolled up (transcripts.HistoryIndex). */
    fun readEarlier(chat: Chat, window: Long = TAIL, tries: Int = 0) {
        if (chat.readingEarlier || chat.start <= 0) return
        val c = conns[chat.hostId] ?: return
        chat.readingEarlier = true
        val era = chat.era
        val until = chat.start
        val from = (until - window).coerceAtLeast(0)
        val got = java.io.ByteArrayOutputStream()
        fun again(wait: Long) { // (not read: asked again once its host is up, later each time it fails while up)
            main.postDelayed({
                when {
                    chat.era != era -> {}
                    chat.prepended == null -> chat.readingEarlier = false // (its page gone: asked as it's next scrolled up to)
                    conns[chat.hostId]?.state != "up" -> again(1000)
                    else -> { chat.readingEarlier = false; readEarlier(chat, window, tries + 1) }
                }
            }, wait)
        }
        fun step(at: Long) {
            c.request(Msg.Transcript(session = chat.sid, offset = at, limit = until - at).json()) { r ->
                if (chat.era != era) return@request
                val ok = r.optString("op") == Op.TRANSCRIPT
                val reply = Msg.TranscriptReply.from(r)
                val data = reply.data.orEmpty()
                val bytes = if (!ok || data.isEmpty()) ByteArray(0) else Base64.getDecoder().decode(data)
                val next = reply.next?.toLong() ?: at
                got.write(bytes, 0, minOf(bytes.size.toLong(), until - at).toInt())
                if (ok && next in (at + 1) until until) step(next)
                else main.post { if (chat.era != era) return@post; if (ok) earlierRead(chat, from, until, window, got.toByteArray()) else again(1000L shl minOf(tries, 4)) }
            }
        }
        step(from)
    }

    /** What's before the chat's lines, read (readEarlier): taken as a chat of its own would take it, then put before
     * them, with what was waiting on it (a rewind's cut, a result's call) settled. */
    private fun earlierRead(chat: Chat, from: Long, until: Long, window: Long, bytes: ByteArray) {
        chat.readingEarlier = false
        val nl = bytes.indexOf('\n'.code.toByte())
        val begin = if (from == 0L) 0L else if (nl < 0) until else from + nl + 1
        if (begin >= until && from > 0) return readEarlier(chat, window * 4) // (a line longer than it: more of it)
        val part = Chat(chat.hostId, chat.sid)
        part.start = begin // (its own results and rewinds that go back before it kept, as the chat's are)
        take(part, parse(String(bytes, Charsets.UTF_8), cutFirst = from > 0))
        if (chat.rewound.isNotEmpty()) { // (a rewind after it back into it, or before it: what's after its record goes)
            val found = chat.rewound.mapNotNull { part.ends[it] }
            val keep = if (found.size < chat.rewound.size) 0 else found.min()
            chat.rewound.removeAll { it in part.ends }
            while (part.lines.size > keep) part.lines.removeAt(part.lines.size - 1)
            part.ends.values.removeAll { it > keep }
        }
        for (o in chat.orphans.toList()) {
            val id = try { JSONObject(o.text).optString("id") } catch (_: Exception) { "" }
            if (part.calls.byId[id] == null) continue
            noted(part, o)
            took(part.calls, part.lines, o)
            tasksFrom(part, o)
            chat.orphans.remove(o)
        }
        chat.rewound.addAll(part.rewound) // (what's still waiting, settled by the parts before it)
        chat.orphans.addAll(part.orphans)
        settle(part.calls, session(chat.hostId, chat.sid)?.state)
        val before = itemIndex(chat, chat.lines.size - 1) + 1
        val n = part.lines.size
        chat.calls.byId.putAll(part.calls.byId)
        chat.ends.replaceAll { _, v -> v + n }
        chat.ends.putAll(part.ends)
        chat.lines.addAll(0, part.lines)
        for (a in part.agents) if (chat.agents.none { it.toolUseId == a.toolUseId }) chat.agents.add(a) // (newest first)
        for (a in part.artifacts) if (chat.artifacts.none { it.url == a.url }) chat.artifacts.add(a)
        val heard = part.chrome.taskOrder.filter { it !in chat.chrome.tasks } // (its background tasks, heard first)
        for (id in heard) chat.chrome.tasks[id] = part.chrome.tasks[id]!!
        chat.chrome.taskOrder.addAll(0, heard)
        if (heard.isNotEmpty()) settleTasks(chat)
        chat.start = begin
        val had = chat.earlier.value
        chat.earlier.value = begin > 0
        chat.prepended?.invoke(itemIndex(chat, chat.lines.size - 1) + 1 - before, had)
        if (n == 0 && begin > 0) readEarlier(chat) // (nothing shown from it: on to the part before)
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
        chat.calls.byId[tool]?.let { call ->
            when (m.optString("subtype")) {
                "task_started" -> call.bind(m.optString("task_id"), m.optString("task_type"))
                "task_notification" -> call.finish(m.optString("status"), m.optString("summary"), null)
            }
        }
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

    /** The id of the host a connection's to (this phone's own host's too), if it's known. */
    fun hostIdOf(connId: String): String? = if (connId == "phone") PhoneHost.id else machine(connId)

    /** Is a chat (its connection's id, its session's) in front of you on any machine of the mesh, this phone too, as
     * each host says of its own machine's apps (Info's front, from their telemetry)? */
    fun inFront(connId: String, sid: String): Boolean {
        val host = hostIdOf(connId) ?: return false
        return conns.values.any { c -> c.state == "up" && c.info?.front.orEmpty().any { it.host == host && it.session == sid } }
    }

    /** The machine a connection's to (its id, as the mesh knows it), if it's known: this phone's agents, none. */
    private fun machine(connId: String): String? = when {
        connId == "phone" -> null
        connId.startsWith("mesh:") -> connId.removePrefix("mesh:")
        else -> conns[connId]?.info?.id?.takeIf(::isId) ?: hosts.firstOrNull { it.id == connId }?.let { HostStore.machine(app, it) }
    }

    /** A chat's draft: what's written, its files, and its queue (Queue.draftOf). */
    class Draft(val text: String, val files: List<Picked>, val queue: List<Queued> = emptyList(), val upto: Int? = null,
                val hold: Boolean = false, val dirty: Boolean = false) { // (dirty: its text written here, its host's
        // answer to its save not come as it was left)
        val empty get() = text.isBlank() && files.isEmpty() && queue.isEmpty()
    }
    val drafts = HashMap<String, Draft>() // (a chat left, "host/session": what was written and queued there, as it has them again)

    private val io = java.util.concurrent.Executors.newSingleThreadExecutor()

    /** What you wrote (and the files you chose) sent to the chat at once, as Baude sends it: a user message for its
     * agent, drawn as yours meanwhile (Outgoing). If its host says no, it's taken out and put back in the box, and why
     * said. uuid: the one it's sent by, if it was sent before (a queued message its host may not have taken). taken:
     * once its host has given it its agent. */
    fun send(chat: Chat, text: String, files: List<Picked>, keep: Boolean = true, uuid: String? = null,
             failed: (() -> Unit)? = null, taken: (() -> Unit)? = null) {
        val c = conns[chat.hostId]?.takeIf { it.state == "up" } ?: run { // (its host went as it was written: queued,
            if (keep && open === chat) Queue.putBackTried(chat, Queued(text, files, uuid ?: java.util.UUID.randomUUID() // to go
                .toString(), tried = uuid != null))                                                             // once it's back)
            else failed?.invoke()
            return
        }
        chat.said.value = null
        // (what it sent, text and files, sent again from a sign-in card, if the turn fails for want of it)
        chat.chrome.turnSent = if (text.isBlank() && files.isEmpty()) null else text to files
        Telemetry.sent(chat)
        Shares.used(chat)
        chat.busy.value = true
        val id = uuid ?: java.util.UUID.randomUUID().toString()
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
                val kept = keep && open === chat
                val sent = Outgoing(id, lines, text, files, kept)
                chat.sent.add(sent)
                if (kept) Queue.save(chat) // (kept on disk as tried till its host takes it: if the app's ended meanwhile,
                                           // it's sent again by its uuid, and taken once)
                c.request(Msg.Input(session = chat.sid, msg = msg).json()) { r ->
                    main.post {
                        chat.busy.value = false
                        val went = r.optString("op") == Op.ERROR && Msg.Error.from(r).error.let {
                            it == Connection.NOT_CONNECTED || it == Connection.WENT }
                        if (r.optString("op") != Op.ERROR) {
                            sent.accepted = true
                            if (kept) Queue.taken(chat, id)
                            taken?.invoke()
                        } else if (went && kept) { // (its link went before its host answered: it may have been taken,
                            chat.sent.remove(sent) // so it goes again by its uuid, taken once)
                            if (open === chat) Queue.putBackTried(chat, Queued(text, files, id))
                        } else if (r.optString("op") == Op.ERROR) {
                            val e = Msg.Error.from(r)
                            chat.sent.remove(sent)
                            if (kept) Queue.taken(chat, id)
                            if (keep) Queue.addDraft(chat.hostId, chat.sid, text, files)
                            failed?.invoke()
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

    /** What a chat not open was left queued, sent to it as its turn ended (Queue.sendLeft): by the uuid it keeps, so its
     * host takes it once if it's sent again; out of its queue once its host takes it (Queue.taken), kept as not sent if
     * its host says no (Queue.refused). */
    fun sendQueued(hostId: String, sid: String, q: Queued) {
        val id = q.uuid ?: return
        val c = conns[hostId]?.takeIf { it.state == "up" } ?: return // (still queued, tried: sent as its host lists it again)
        flying.add(id)
        io.execute {
            val msg = try {
                build(id, q.text, q.files)
            } catch (e: Exception) {
                null
            }
            main.post {
                if (msg == null) { // (nothing in it: dropped)
                    flying.remove(id)
                    return@post Queue.taken(hostId, sid, id)
                }
                c.request(Msg.Input(session = sid, msg = msg).json()) { r ->
                    main.post {
                        flying.remove(id)
                        val e = if (r.optString("op") == Op.ERROR) Msg.Error.from(r) else null
                        when {
                            e == null -> Queue.taken(hostId, sid, id)
                            e.error == Connection.NOT_CONNECTED || e.error == Connection.WENT -> {} // (still queued:
                            // sent again as its host lists it again)
                            else -> Queue.refused(hostId, sid, id, e.said?.ifBlank { null } ?: Wording.BotBrowser.hostSaidNo)
                        }
                    }
                }
            }
        }
    }

    /** The uuids of queued messages of chats not open being sent (sendQueued): not sent again till their host answers. */
    val flying = HashSet<String>()

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

    /** A chat's resume after a usage limit (its resume_at) cancelled on its host, as Baude's cancel_resume: the line
     * gone as its listing says so; why not, if it isn't. */
    fun resumeCancel(chat: Chat) {
        val c = conns[chat.hostId] ?: return
        c.request(Msg.ResumeCancel(session = chat.sid).json()) { r ->
            main.post {
                if (r.optString("op") == Op.ERROR)
                    Toast.makeText(app, Wording.LimitResume.notCancelled(why = Msg.Error.from(r).said.orEmpty()
                        .ifBlank { Wording.BotBrowser.hostSaidNo }), Toast.LENGTH_SHORT).show()
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
        val end = turn.size?.let { Msg.TurnEnd(uuid = turn.uuid, size = it) }
        when {
            turn.isError == true -> notify(contact, Wording.Meta.endedError(error = result.ifEmpty { Wording.Meta.errorUnknown }), end)
            result.isNotEmpty() -> notify(contact, result, end)
            else -> main.postDelayed({ // (no result said: its transcript's end, once its new size is listed)
                val size = session(c.id, sid)?.size?.toLong() ?: 0
                last(c, sid, size, end)
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
            if (s.ended != null && !unseen(s)) continue // (its turn's end seen meanwhile, elsewhere on the mesh)
            last(c, s.id.orEmpty(), s.size?.toLong() ?: 0, s.ended)
        }
    }

    private fun last(c: Connection, sid: String, size: Long, end: Msg.TurnEnd?) {
        val contact = contact(c.id, sid) ?: return
        c.request(Msg.Transcript(session = sid, offset = (size - 64_000).coerceAtLeast(0)).json()) { r ->
            val data = Msg.TranscriptReply.from(r).data.orEmpty()
            val text = if (data.isEmpty()) "" else String(Base64.getDecoder().decode(data), Charsets.UTF_8)
            val last = parse(text, cutFirst = true).lastOrNull { it.kind == "bot" || it.kind == "error" }?.text ?: return@request
            main.post { notify(contact, last, end) }
        }
    }

    private val notified = HashMap<String, Pair<String, Long>>() // (a Bot: what it was last notified for, and when)

    /** end: the turn's end it's of (its uuid, and its transcript's size then, what's seen of it is measured against). */
    private fun notify(contact: Contact, text: String, end: Msg.TurnEnd?) {
        val now = android.os.SystemClock.elapsedRealtime()
        val before = notified[contact.id]
        if (before != null && before.first == text && now - before.second < 120_000) return // (the same turn, told
        notified[contact.id] = text to now                                                  // twice: once)
        post(contact, text, null, end = end)
    }

    // ---- what's been seen (wire's Seen): a turn's end seen on any head of the mesh takes back its notification
    // everywhere, and its dot

    /** Has its last turn's end not been seen yet, anywhere on the mesh (its host's ended and seen)? */
    fun unseen(s: Msg.Session?): Boolean {
        if (s == null) return false
        val end = s.ended?.size?.toLong() ?: return false
        return (s.seen?.size?.toLong() ?: -1) < end
    }

    /** Has a turn's end at end (its transcript's size then) been seen, or one after it? */
    private fun seenPast(s: Msg.Session?, end: Long): Boolean = (s?.seen?.size?.toLong() ?: -1) >= end

    /** A chat's dot, as its host lists it: working or waiting as it says; done (blue) while its last turn's end hasn't
     * been seen anywhere; else none. */
    fun dot(s: Msg.Session): String = when {
        s.state == "working" || s.state == "waiting" -> s.state.orEmpty()
        unseen(s) -> "done"
        else -> s.state.orEmpty()
    }

    /** The tag of a chat's turn's notification (with TURN_NOTE): what it's taken back by, here or by its host's word. */
    fun turnTag(hostId: String, sid: String) = "chat:$hostId/$sid"

    /** (a host listed its sessions) Each notification of a turn's end it's posted for a chat of it (up now, whether
     * this process posted it or one before it, Android having ended the app since) taken back once that turn's end,
     * or one after it, has been seen: on a head anywhere on the mesh, this one too. */
    private fun withdrawSeen(hostId: String, now: List<Msg.Session>) {
        val nm = app.getSystemService(NotificationManager::class.java) ?: return
        val prefix = turnTag(hostId, "")
        for (n in try { nm.activeNotifications } catch (_: Exception) { return }) {
            val tag = n.tag ?: continue
            if (n.id != TURN_NOTE || !tag.startsWith(prefix)) continue
            val end = n.notification.extras.getLong(TURN_END, -1)
            val s = now.firstOrNull { it.id == tag.removePrefix(prefix) }
            if (end >= 0 && seenPast(s, end)) nm.cancel(tag, TURN_NOTE)
        }
    }

    /** The hosts of yours whose chats have a notification of a turn's end up that their word can take back, as SeenJob
     * looks: one with its turn's end in it (TURN_END), on a host that's still yours. (A member's, reached only through a
     * host's mesh, waits for the app's own start; so does every one while there's no key to reach a host with.) */
    fun turnNotes(): Set<String> {
        val nm = app.getSystemService(NotificationManager::class.java) ?: return emptySet()
        if (key.value == null) return emptySet()
        val yours = HostStore.load(app).map { it.id }.toSet() - HostStore.deactivated(app).toSet()
        return (try { nm.activeNotifications } catch (_: Exception) { return emptySet() })
            .filter { it.id == TURN_NOTE && it.tag?.startsWith("chat:") == true && it.notification.extras.containsKey(TURN_END) }
            .map { it.tag.removePrefix("chat:").substringBeforeLast('/') }.filter { it in yours }.toSet()
    }

    /** (SeenJob) The hosts given reached, for what's been seen there: the app running, those of its links that are
     * down made again; not started (Android ended it), only links to those hosts made, nothing else of the app
     * started (not this phone's own host, nor the mesh's members). The links made here, to close once it's done. */
    fun reachForSeen(ctx: Context, ids: Set<String>): Set<String> {
        if (started) {
            ids.forEach { conns[it]?.resume(fresh = false) }
            return emptySet()
        }
        app = ctx.applicationContext
        if (key.value == null) readKey()
        channel()
        if (key.value == null) return emptySet()
        val made = HashSet<String>()
        for (h in HostStore.load(app)) {
            if (h.id !in ids || conns[h.id]?.state == "up") continue
            conns.remove(h.id)?.stop()
            start(Connection(app, Place.Direct(h), ::identify, { null }, ::heard) { main.post { tick.value++ } })
            made.add(h.id)
        }
        return made
    }

    /** (SeenJob, done) The links it made let go of, unless the app's started since (they're its own then). */
    fun dropForSeen(ids: Set<String>) {
        if (started) return
        for (id in ids) {
            conns.remove(id)?.stop()
            states.remove(id)
        }
    }

    /** When a host (a connection's id) last listed its sessions (elapsedRealtime; 0: not since the app started). */
    fun listedAt(hostId: String): Long = listedAt[hostId] ?: 0

    /** The open chat seen (the app in front, its chat page shown, scrolled to its end): its host told its last turn's
     * end is seen here, once what's read of it reaches that end (once a turn: toldSeen). */
    fun tellSeen(chat: Chat? = open) {
        chat ?: return
        if (!shown || open !== chat || !chat.inView || !chat.atEnd) return
        val s = session(chat.hostId, chat.sid)
        val end = s?.ended ?: return
        if (!unseen(s) || chat.offset < (end.size?.toLong() ?: Long.MAX_VALUE)) return
        val key = "${chat.hostId}/${chat.sid}"
        if (toldSeen[key] == end.uuid) return
        val c = conns[chat.hostId]?.takeIf { it.state == "up" } ?: return
        toldSeen[key] = end.uuid.orEmpty()
        c.request(Msg.Seen(session = chat.sid, upto = chat.offset).json()) { r ->
            if (r.optString("op") != Op.SEEN_SET) main.post { if (toldSeen[key] == end.uuid) toldSeen.remove(key) }
        }
    }

    /** A notification for a Bot that opens its chat: what it said (one a Bot, each in place of the last), or one of
     * its asks (tag: its own, taken back once it's answered). On the lock screen, only which Bot wrote or is asking:
     * what it said shows once the phone's unlocked. */
    private fun post(contact: Contact, text: String, tag: String?, asked: Boolean = false, end: Msg.TurnEnd? = null) {
        Settings.load(app)
        if (!Settings.notify.value) return // (Settings → Notifications off)
        if (inFront(contact.hostId, contact.id)) return // (in front of you on a machine of the mesh)
        if (end != null && seenPast(session(contact.hostId, contact.id), end.size?.toLong() ?: 0)) return // (seen
        // already, elsewhere)
        if (!asked && contact.session.bot?.picture != null && contact.pictureKey !in pictures) // (its picture first)
            return picture(contact) { post(contact, text, tag, asked = true, end = end) }
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
            .setLargeIcon(pictures[contact.pictureKey]?.let { notificationPicture(app, it.asAndroidBitmap()) })
            .setContentIntent(pending)
            .setAutoCancel(true)
            .addExtras(android.os.Bundle().apply { // (the turn's end it's of: what's seen is measured against, withdrawSeen)
                end?.size?.let { putLong(TURN_END, it.toLong()) }
                end?.uuid?.let { putString(TURN_UUID, it) }
            })
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setPublicVersion(NotificationCompat.Builder(app, MESSAGES)
                .setSmallIcon(R.drawable.ic_baude)
                .setContentTitle(contact.name)
                .setContentText(if (tag != null) Wording.Asks.waiting else Wording.Meta.finished)
                .build())
            .build()
        try {
            if (tag != null) NotificationManagerCompat.from(app).notify(tag, ASK_NOTE, n)
            else {
                NotificationManagerCompat.from(app).notify(turnTag(contact.hostId, contact.id), TURN_NOTE, n)
                SeenJob.schedule(app) // (taken back by what its host says, the app ended by Android or not)
            }
        } catch (_: SecurityException) {
        }
    }

    const val MESSAGES = "messages"
    private const val ASK_NOTE = 2 // (an ask's notification: its tag says which)
    const val TURN_NOTE = 3 // (a Bot's turn's notification: its tag says whose, turnTag)
    private const val TURN_END = "dev.baude.android.TURN_END" // (its extras': the turn's end, TurnEnd's size and uuid)
    private const val TURN_UUID = "dev.baude.android.TURN_UUID"
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
        if (type == "cost-state") { // (what claude had spent on it when it last exited: a resume counts on from it)
            out.add(Line("cost", o.optDouble("totalCostUSD", 0.0).toString()))
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
        if (type == "system" && o.optString("subtype") == "compact_boundary") { // (where claude compacted it)
            if (sidechain || !o.optBoolean("isSidechain")) out.add(Line("compact",
                JSONObject().put("meta", o.optJSONObject("compactMetadata") ?: JSONObject()).toString(), uuid))
            return
        }
        if (type != "user" && type != "assistant") return
        if (o.optBoolean("isMeta") || (o.optBoolean("isSidechain") && !sidechain)) return
        if (o.optBoolean("isCompactSummary")) { // (what it wrote for itself: under its mark)
            val content = o.optJSONObject("message")?.opt("content")
            val first = when (content) {
                is String -> content
                is JSONArray -> content.list().firstOrNull { it.optString("type") == "text" }?.optString("text")
                else -> null
            }.orEmpty()
            out.add(Line("compactsum", first, uuid))
            return
        }
        val msg = o.optJSONObject("message") ?: return
        val blocks = when (val content = msg.opt("content")) {
            is JSONArray -> content.list()
            is String -> listOf(JSONObject().put("type", "text").put("text", content))
            else -> emptyList()
        }
        val typed = ArrayList<String>() // (what you wrote in this record, and the files with it: one message)
        val sent = ArrayList<Sent>()
        for (b in blocks) {
            when (b.optString("type")) {
                "thinking" -> if (type == "assistant") b.optString("thinking").trim().takeIf { it.isNotEmpty() }?.let {
                    out.add(Line("thinking", it, uuid))
                }
                "text" -> {
                    var t = b.optString("text")
                    if (type == "user") {
                        t = LEADING_REMINDERS.replace(t, "").trim()
                        when {
                            t.startsWith("<task-notification") -> out.add(Line("task", taskNote(t).toString(), uuid))
                            t.startsWith(STOPPED) -> out.add(Line("stopped", "", uuid))
                            t.contains("<command-name>") -> typed.add(command(t))
                            t.isEmpty() || NOT_TYPED.any { t.startsWith(it) } -> {}
                            else -> typed.add(t)
                        }
                    } else if (t.isNotBlank()) {
                        out.add(Line(if (o.optBoolean("isApiErrorMessage")) "error" else "bot", t.trim(), uuid))
                        val urls = ARTIFACT_URL.findAll(t).map { it.value }.toList()
                        if (urls.isNotEmpty()) out.add(Line("link", JSONObject().put("urls", JSONArray(urls)).toString(), uuid))
                    }
                }
                "image" -> if (type == "user") b.optJSONObject("source")?.takeIf { it.optString("type") == "base64" }?.let {
                    sent.add(Sent("image", "image", it.optString("media_type"), it.optString("data")))
                }
                "document" -> if (type == "user") sent.add(Sent("file", b.optString("title").ifBlank { "document.pdf" },
                    "application/pdf", null))
                "tool_use", "server_tool_use" -> if (type == "assistant") {
                    val name = b.optString("name")
                    out.add(Line("call", JSONObject().put("id", b.optString("id")).put("name", name)
                        .put("input", b.optJSONObject("input") ?: JSONObject()).toString(), uuid))
                    if (isAgent(name) || name.substringAfterLast("__") == "Artifact") out.add(Line("use",
                        JSONObject().put("id", b.optString("id")).put("name", name)
                            .put("input", b.optJSONObject("input") ?: JSONObject()).toString(), uuid))
                }
                "tool_result" -> {
                    if (type == "user") out.add(Line("result", JSONObject()
                        .put("id", b.optString("tool_use_id")).put("is_error", b.optBoolean("is_error"))
                        .put("text", resultText(b.opt("content")))
                        .put("images", JSONArray((b.opt("content") as? JSONArray).objects().filter { it.optString("type") == "image" }
                            .mapNotNull { it.optJSONObject("source")?.optString("data")?.ifEmpty { null } }
                            .map { JSONObject().put("data", it) })).toString(), uuid))
                }
                else -> {
                    if (type == "assistant" && b.optString("type").endsWith("_tool_result")) out.add(Line("result", JSONObject()
                        .put("id", b.optString("tool_use_id")).put("server", true)
                        .put("is_error", b.optJSONObject("content")?.optString("type").orEmpty().endsWith("_error"))
                        .put("text", "").toString(), uuid))
                }
            }
        }
        if (type == "user" && (typed.isNotEmpty() || sent.isNotEmpty())) out.add(Line("you", typed.joinToString("\n\n"), uuid, sent))
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

/** "Server / Tool" for an MCP server's tool (tool_label), any other as it is. */
fun toolTitle(name: String): String = McpNames.label(name)

fun humanize(id: String) =
    id.replace(Regex("([a-z0-9])([A-Z])"), "$1 $2").replace("_", " ").replace("-", " ").trim().split(Regex("\\s+"))
        .joinToString(" ").lowercase()

private fun JSONArray?.objects(): List<JSONObject> =
    if (this == null) emptyList() else (0 until length()).mapNotNull { optJSONObject(it) }
