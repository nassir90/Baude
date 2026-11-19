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
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
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
    val pictureKey: String get() = "$hostId/$id/${session.optJSONObject("bot")?.opt("picture")}"
}

/** One line of a chat as it's shown: yours, the Bot's, what it did (a tool it used), or a note (a background task
 * ended, a turn stopped, the conversation compacted). rewind: a revert's mark, text the uuid of the last line kept. */
data class Line(val kind: String, val text: String, val uuid: String? = null)

/** A chat open: its lines, read from its transcript on its host (more read as it grows), what's sent to it. */
class Chat(val hostId: String, val sid: String) {
    val lines = mutableStateListOf<Line>()
    val busy = mutableStateOf(false)
    val said = mutableStateOf<String?>(null)
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
    val sessions = mutableStateMapOf<String, List<JSONObject>>() // (host id: its sessions)
    val pictures = mutableStateMapOf<String, ImageBitmap?>()
    val key = mutableStateOf<Key?>(null)
    var open: Chat? = null
    var shown = false // (the app in front: no notification for the chat open)
    private val conns = HashMap<String, Connection>()
    private val catchUp = HashSet<String>() // (hosts back after a drop: Bots' turns that ended meanwhile, notified)
    private var started = false

    fun start(ctx: Context) {
        if (started) return
        started = true
        app = ctx.applicationContext
        key.value = Keys.load(app)
        hosts.addAll(HostStore.load(app))
        channel()
        hosts.forEach { connect(it) }
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

    fun makeKey() {
        key.value = Keys.make(app)
        hosts.forEach { connect(it) } // (the new key: each host tried again with it)
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
        if (hosts.isEmpty()) app.stopService(Intent(app, MeshService::class.java))
    }

    fun connect(host: Host) {
        conns.remove(host.id)?.stop()
        val k = key.value ?: return
        val c = Connection(app, host, k, ::heard) { conn -> main.post { changed(conn) } }
        conns[host.id] = c
        states[host.id] = c
        c.start()
    }

    /** The key a host showed trusted (you've compared its fingerprint), and the host tried again. */
    fun trust(host: Host, shown: ShownKey) {
        HostStore.trust(app, host, shown.type, shown.blob)
        connect(host)
    }

    private fun changed(c: Connection) {
        if (conns[c.host.id] !== c) return
        tick.value++
        if (c.state == "up") {
            if (sessions[c.host.id] != null) catchUp.add(c.host.id)
            open?.let { if (it.hostId == c.host.id) attach(it) } // (read once its sessions are listed: their sizes)
        }
    }

    fun conn(hostId: String): Connection? = conns[hostId]

    // ---- what the hosts say

    private fun heard(c: Connection, msg: JSONObject) {
        main.post {
            if (conns[c.host.id] !== c) return@post
            when (msg.optString("op")) {
                "sessions" -> {
                    val now = msg.optJSONArray("sessions").list().filter { !it.optBoolean("draft") }
                    val before = sessions[c.host.id]
                    sessions[c.host.id] = now
                    if (catchUp.remove(c.host.id) && before != null) missed(c, before, now)
                    open?.let { if (it.hostId == c.host.id && it.offset < 0) read(it) }
                }
                "turn_ended" -> ended(c, msg)
                "claude" -> open?.let { if (it.hostId == c.host.id && it.sid == msg.optString("session")) read(it) }
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

    fun openChat(hostId: String, sid: String): Chat {
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

    private fun attach(chat: Chat) {
        conns[chat.hostId]?.request(JSONObject().put("op", "attach").put("session", chat.sid).put("live", true)
            .put("answers", false)) {}
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
                        if (line.kind != "rewind") {
                            chat.lines.add(line)
                            continue
                        }
                        val kept = chat.lines.indexOfLast { it.uuid != null && it.uuid == line.text }
                        if (kept >= 0 || line.text.isEmpty()) {
                            while (chat.lines.size > kept + 1) chat.lines.removeAt(chat.lines.size - 1)
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
            nm.createNotificationChannel(NotificationChannel(MESSAGES, "Messages", NotificationManager.IMPORTANCE_HIGH))
            nm.createNotificationChannel(NotificationChannel(LINKED, "Connected", NotificationManager.IMPORTANCE_MIN))
        }
    }

    /** A Bot's turn ended: what it said last (the turn's own result), as a notification (none for the chat that's
     * open in front). */
    private fun ended(c: Connection, msg: JSONObject) {
        val sid = msg.optString("session")
        val chat = open
        if (chat != null && chat.hostId == c.host.id && chat.sid == sid) {
            read(chat)
            if (shown) return
        }
        val contact = contact(c.host.id, sid) ?: return
        val result = if (msg.isNull("result")) "" else msg.optString("result").trim()
        when {
            msg.optBoolean("is_error") -> notify(contact, "Couldn't finish" + if (result.isNotEmpty()) ": $result" else "")
            result.isNotEmpty() -> notify(contact, result)
            else -> main.postDelayed({ // (no result said: its transcript's end, once its new size is listed)
                val size = sessions[c.host.id]?.firstOrNull { it.optString("id") == sid }?.optLong("size", 0) ?: 0
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
            if (shown && chat != null && chat.hostId == c.host.id && chat.sid == s.optString("id")) continue
            last(c, s.optString("id"), s.optLong("size", 0))
        }
    }

    private fun last(c: Connection, sid: String, size: Long) {
        val contact = contact(c.host.id, sid) ?: return
        c.request(JSONObject().put("op", "transcript").put("session", sid)
            .put("offset", (size - 64_000).coerceAtLeast(0))) { r ->
            val data = r.optString("data", "")
            val text = if (data.isEmpty()) "" else String(Base64.getDecoder().decode(data), Charsets.UTF_8)
            val last = parse(text, cutFirst = true).lastOrNull { it.kind == "bot" }?.text ?: return@request
            main.post { notify(contact, last) }
        }
    }

    private fun notify(contact: Contact, text: String) {
        if (Build.VERSION.SDK_INT >= 33 &&
            app.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED
        ) return
        val intent = Intent(app, MainActivity::class.java).putExtra("host", contact.hostId).putExtra("session", contact.id)
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        val pending = PendingIntent.getActivity(app, contact.id.hashCode(), intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val n = NotificationCompat.Builder(app, MESSAGES)
            .setSmallIcon(R.drawable.ic_baude)
            .setContentTitle(contact.name)
            .setContentText(text.take(200))
            .setStyle(NotificationCompat.BigTextStyle().bigText(text.take(2000)))
            .setContentIntent(pending)
            .setAutoCancel(true)
            .build()
        try {
            NotificationManagerCompat.from(app).notify(contact.id.hashCode(), n)
        } catch (_: SecurityException) {
        }
    }

    const val MESSAGES = "messages"
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
            out.add(Line("rewind", o.optString("leafUuid", "")))
            continue
        }
        if (type != "user" && type != "assistant") continue
        if (o.optBoolean("isMeta") || o.optBoolean("isSidechain")) continue
        val uuid = o.optString("uuid").ifBlank { null }
        if (o.optBoolean("isCompactSummary")) {
            out.add(Line("note", "The conversation was compacted", uuid))
            continue
        }
        val msg = o.optJSONObject("message") ?: continue
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
                            t.isEmpty() || NOT_TYPED.any { t.startsWith(it) } -> {}
                            else -> out.add(Line("you", t, uuid))
                        }
                    } else if (t.isNotBlank()) out.add(Line("bot", t.trim(), uuid))
                }
                "image" -> if (type == "user") out.add(Line("you", "🖼 A picture", uuid))
                "document" -> if (type == "user") out.add(Line("you", "📄 " + b.optString("title", "A document"), uuid))
                "tool_use" -> if (type == "assistant") out.add(Line("tool", toolLine(b), uuid))
            }
        }
    }
    return out
}

private val LEADING_REMINDERS = Regex("""\A(?:\s*<system-reminder>.*?</system-reminder>)+\s*""", RegexOption.DOT_MATCHES_ALL)
private const val STOPPED = "[Request interrupted by user"
private val NOT_TYPED = listOf("<system-reminder", "<local-command", "<command-", "Caveat:", "<ci-monitor-event")

private fun toolLine(b: JSONObject): String {
    val name = b.optString("name")
    val input = b.optJSONObject("input") ?: JSONObject()
    val what = input.optString("description").ifBlank { input.optString("command") }
        .ifBlank { input.optString("file_path") }.ifBlank { input.optString("url") }.ifBlank { input.optString("pattern") }
    return if (what.isBlank()) name else "$name · ${what.lines().first().take(80)}"
}
