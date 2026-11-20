package dev.baude.android

import android.content.Context
import android.content.Intent
import android.net.LocalServerSocket
import android.net.LocalSocket
import android.os.Handler
import android.os.Looper
import android.os.Process
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Public
import androidx.compose.runtime.Composable
import androidx.compose.ui.platform.LocalContext
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.security.SecureRandom
import kotlin.concurrent.thread

/** A Bot's browser on this phone ("Phone Use"): what a Bot's browser tools ask of its Emission, asked of a browser
 * here instead (BrowserActivity, in a process of its own, its profile the Bot's), while it's open for that Bot (in
 * front or out of sight). Its Bot's host is told (wire's emission_serve, on the connection its chat's reached on,
 * directly or through the mesh), and from then on carries the Bot's tools' requests here (emission_call) and takes
 * their answers (emission_answer); once it's closed, they're Emission's on that host again, the Bot told so with what
 * it hadn't been told yet.
 *
 * The browser's process talks to this one on a socket of the app's own (a uid check: no other app), a line of JSON
 * each way, as Emission's profile socket is. */
object PhoneBrowser {
    const val MIN_VERSION = 16 // (wire's version 16: emission_serve, a Bot's browser on a phone)
    private val main = Handler(Looper.getMainLooper())
    private val name = "dev.baude.android.emission." + ByteArray(12).also { SecureRandom().nextBytes(it) }
        .joinToString("") { "%02x".format(it) } // (where the browser's process finds this one: not to be guessed)
    private var server: LocalServerSocket? = null

    /** The Bot whose browser's open here (hostId: the connection its chat's on), the browser's link, and whether its
     * host carries its requests here. */
    private class Served(val hostId: String, val bot: String, val name: String, val link: Link) {
        var up = false // (its host's said it carries its requests here)
        var acting = false
        var note = "" // (what the browser hadn't told the Bot, as it closed; and the pages it had open)
        var urls = org.json.JSONArray()
    }

    private var served: Served? = null
    private var next: Intent? = null // (a browser asked for while another's closing: opened once it's gone)
    private val calls = HashMap<String, Connection>() // (a request carried to the browser: the connection it came on)

    /** Whether the Bot's host can carry its tools' requests to a phone (a host of yours, or a member of its mesh). */
    fun offered(hostId: String): Boolean = offers(Mesh.conn(hostId)?.info, "emission_serve", MIN_VERSION)

    /** The Bot's browser opened here (shown again, if it's open already). */
    fun open(ctx: Context, hostId: String, bot: String, botName: String) {
        listen()
        val intent = Intent(ctx, BrowserActivity::class.java).putExtra("host", hostId).putExtra("bot", bot)
            .putExtra("name", botName).putExtra("socket", name).putExtra("where", Mesh.label(hostId))
        val now = served
        if (now != null && now.bot != bot) { // (another Bot's: its process ends first, its WebView that Bot's profile's)
            next = intent
            now.link.send(JSONObject().put("op", "close"))
            return
        }
        ctx.startActivity(intent)
    }

    private fun listen() {
        if (server != null) return
        val s = LocalServerSocket(name)
        server = s
        thread(name = "phone-browser", isDaemon = true) {
            while (true) {
                val sock = try {
                    s.accept()
                } catch (_: Exception) {
                    break
                }
                if (sock.peerCredentials.uid != Process.myUid()) { // (this app's own process only)
                    sock.close()
                    continue
                }
                Link(sock).start()
            }
        }
    }

    /** The browser's process, on the socket. */
    private class Link(val sock: LocalSocket) {
        private val out = sock.outputStream
        private val writes = java.util.concurrent.LinkedBlockingQueue<ByteArray>()

        fun start() {
            thread(name = "phone-browser-out", isDaemon = true) {
                try {
                    while (true) {
                        val line = writes.take()
                        if (line.isEmpty()) break
                        out.write(line)
                        out.flush()
                    }
                } catch (_: Exception) {
                }
            }
            thread(name = "phone-browser-in", isDaemon = true) {
                try {
                    BufferedReader(InputStreamReader(sock.inputStream, Charsets.UTF_8), 1 shl 16).use { r ->
                        while (true) {
                            val line = r.readLine() ?: break
                            val msg = try {
                                JSONObject(line)
                            } catch (_: Exception) {
                                continue
                            }
                            main.post { heard(this, msg) }
                        }
                    }
                } catch (_: Exception) {
                }
                writes.put(ByteArray(0))
                main.post { gone(this) }
            }
        }

        fun send(msg: JSONObject) = writes.put((msg.toString() + "\n").toByteArray(Charsets.UTF_8))
    }

    private fun heard(link: Link, msg: JSONObject) {
        when (msg.optString("op")) {
            "hello" -> {
                served?.takeIf { it.link !== link }?.let { stop(it) }
                val s = Served(msg.optString("host"), msg.optString("bot"), msg.optString("name"), link)
                served = s
                Mesh.conn(s.hostId)?.let { changed(it) }
                tick()
            }
            "again" -> served?.takeIf { it.link === link && !it.up }?.let { s -> // (shown again: served again, if
                Mesh.conn(s.hostId)?.let { changed(it) }                             // it's been let go of)
            }
            "bye" -> served?.takeIf { it.link === link }?.let {
                it.note = msg.optString("note")
                it.urls = msg.optJSONArray("urls") ?: org.json.JSONArray()
            }
            "answer" -> { // (a request's answer: to the host that asked)
                val id = msg.optString("id")
                val conn = calls.remove(id) ?: return
                val reply = JSONObject().put("op", "emission_answer").put("id", id)
                if (msg.has("result")) reply.put("result", msg.get("result")) else reply.put("error", msg.optString("error"))
                conn.send(reply)
            }
        }
    }

    private fun gone(link: Link) {
        val s = served
        if (s != null && s.link === link) stop(s)
        next?.let {
            next = null
            Mesh.app.startActivity(it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        }
    }

    /** The host told it's Emission's again, and what's waiting on the browser answered as gone. */
    private fun stop(s: Served) {
        if (served === s) served = null
        Mesh.conn(s.hostId)?.let { c ->
            if (c.state == "up") c.send(JSONObject().put("op", "emission_serve").put("bot", s.bot).put("on", false)
                .put("note", s.note).put("urls", s.urls))
        }
        for ((id, conn) in calls.toList()) {
            calls.remove(id)
            conn.send(JSONObject().put("op", "emission_answer").put("id", id).put("error", "the phone's browser closed"))
        }
    }

    /** (Mesh's, as a connection's state changes) The Bot's host told the phone's browser is its again each time its
     * connection comes up (it's forgotten when it drops); the browser told how that went; what was carried to the
     * browser on a connection that's dropped let go of (its host answers those itself). */
    fun changed(c: Connection) {
        val s = served ?: return
        if (s.hostId != c.id) return
        if (c.state != "up") {
            calls.entries.removeAll { it.value === c }
            if (s.up || c.state != "connecting") s.link.send(JSONObject().put("op", "serving").put("on", false)
                .put("why", "Can't connect to ${Mesh.label(s.hostId)}" + (c.why?.let { ": $it" } ?: "")))
            s.up = false
            return
        }
        if (!offers(c.info, "emission_serve", MIN_VERSION)) {
            s.link.send(JSONObject().put("op", "serving").put("on", false)
                .put("why", "${Mesh.label(s.hostId)}'s Baude is too old for this: update it"))
            return
        }
        c.request(JSONObject().put("op", "emission_serve").put("bot", s.bot).put("on", true)) { r ->
            main.post {
                if (served !== s) return@post
                s.up = r.optString("op") == "emission_serving"
                s.link.send(JSONObject().put("op", "serving").put("on", s.up)
                    .apply { if (!s.up) put("why", r.optString("error", "the host said no")) })
            }
        }
    }

    /** (Mesh's) emission_unserved: another phone shows this Bot's browser now. */
    fun unserved(c: Connection, msg: JSONObject) {
        val s = served ?: return
        if (s.hostId != c.id || s.bot != msg.optString("bot")) return
        s.up = false
        calls.entries.removeAll { it.value === c }
        s.link.send(JSONObject().put("op", "serving").put("on", false).put("why", msg.optString("why",
            "another phone shows this Bot's browser now")))
    }

    /** (while one's open: each little while) The Bot's turn at its tools or not, for its glow. */
    private fun tick() {
        val s = served ?: return
        val acting = Mesh.sessions[s.hostId]?.firstOrNull { it.optString("id") == s.bot }?.optBoolean("acting") ?: false
        if (acting != s.acting) {
            s.acting = acting
            s.link.send(JSONObject().put("op", "acting").put("on", acting))
        }
        main.postDelayed({ if (served === s) tick() }, 400)
    }

    /** (Mesh's) emission_call from a host: carried to the browser open for that Bot, if it is; else answered so. */
    fun called(c: Connection, msg: JSONObject) {
        val id = msg.optString("id")
        val bot = msg.optJSONObject("bot")?.optString("id")
        val s = served
        if (s == null || s.hostId != c.id || s.bot != bot || !s.up) {
            c.send(JSONObject().put("op", "emission_answer").put("id", id).put("error", "the phone's browser is closed"))
            return
        }
        calls[id] = c
        s.link.send(JSONObject().put("op", "call").put("id", id).put("call", msg.optJSONObject("call") ?: JSONObject()))
    }
}

/** The Bot's browser on this phone, opened from its chat: a globe, as Emission's is. */
@Composable
fun PhoneBrowserButton(hostId: String, sid: String, name: String) {
    val ctx = LocalContext.current
    Mesh.tick.value
    if (!PhoneBrowser.offered(hostId)) return
    IconButton(Icons.Filled.Public, "$name's browser on this phone") { PhoneBrowser.open(ctx, hostId, sid, name) }
}
