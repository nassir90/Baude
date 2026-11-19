package dev.baude.android

import android.content.Context
import com.jcraft.jsch.ChannelExec
import com.jcraft.jsch.JSch
import com.jcraft.jsch.Session
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStream
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import android.os.SystemClock
import java.util.concurrent.atomic.AtomicInteger
import kotlin.concurrent.thread

const val PING_MS = 5_000L    // (wire.PING_S: each end says something at least this often)
const val SILENT_MS = 16_000L // (wire.SILENT_S: silent this long, the other end's taken for gone)
const val MIN_VERSION = 8     // (wire's version 8: Bots)
const val START_MS = 14_000L  // (connect may start the host process: up to 20 s to its hello, as Baude's 25 allow)

/** What ssh offers and takes here: what Android's own crypto has, P-256's ECDH and ECDSA, RSA and AES. */
private val KEX = "ecdh-sha2-nistp256,ecdh-sha2-nistp384,ecdh-sha2-nistp521,diffie-hellman-group16-sha512," +
    "diffie-hellman-group14-sha256"
private val HOST_KEYS = "ecdsa-sha2-nistp256,ecdsa-sha2-nistp384,ecdsa-sha2-nistp521,rsa-sha2-512,rsa-sha2-256"

/** A connection to a host's process, as Baude's own (wire.connect_ssh): ssh to the host, "<command> connect" run
 * there, and the host process's JSON lines over its stdin and stdout. Made again whenever it drops, a little later each
 * time; not while the host's key isn't one you've trusted (shown, for you to say). */
class Connection(
    private val ctx: Context,
    val host: Host,
    private val key: Key,
    private val heard: (Connection, JSONObject) -> Unit,
    private val changed: (Connection) -> Unit,
) {
    @Volatile var state = "connecting" // (connecting, up, down, key: the host's key waits to be trusted)
    @Volatile var why: String? = null
    @Volatile var info: JSONObject? = null
    @Volatile var shown: ShownKey? = null
    @Volatile private var stopped = false
    @Volatile private var out: OutputStream? = null
    @Volatile private var session: Session? = null
    private val calls = ConcurrentHashMap<Int, (JSONObject) -> Unit>()
    private val nextReq = AtomicInteger(100)
    @Volatile private var writes: LinkedBlockingQueue<ByteArray>? = null // (sent on a thread of its own: Android
    // allows no network from its main thread)

    fun start() {
        thread(name = "ssh-${host.address}", isDaemon = true) { loop() }
    }

    fun stop() {
        stopped = true
        session?.disconnect()
    }

    private fun set(state: String, why: String? = null) {
        this.state = state
        this.why = why
        changed(this)
    }

    private fun loop() {
        var wait = 1_000L
        while (!stopped) {
            wake.clear() // (a wake from before this try: not one for after it)
            var reached = false
            try {
                reached = run()
                if (!stopped) set("down", said.ifBlank { if (reached) "the host went" else "the host's Baude didn't start" })
            } catch (e: Exception) {
                if (stopped) break
                val repo = lastRepo
                if (repo?.shown != null) {
                    shown = repo.shown
                    set("key", if (repo.shown!!.changed) "the host's key changed" else "the host's key isn't trusted yet")
                    return // (made again once you've said: Mesh.trust)
                }
                set("down", plain(e))
            } finally {
                out = null
                session?.disconnect()
                session = null
                failAll()
            }
            if (reached) wait = 1_000L // (it was up: tried again at once, then more slowly each time it fails)
            wait = if (wake.poll(wait, TimeUnit.MILLISECONDS) != null) 1_000L // (the network's back: now)
            else (wait * 2).coerceAtMost(30_000L)
        }
    }

    @Volatile private var lastRepo: TrustedKeys? = null
    @Volatile private var heardAt = 0L // (SystemClock.elapsedRealtime of the last byte heard)
    private val wake = LinkedBlockingQueue<Unit>() // (kick: the wait between tries cut short)
    @Volatile private var said = "" // (what ssh, or the host's command, last said on stderr: why it went)

    /** Tried again now, not after the wait (the network's back). */
    fun kick() {
        if (state == "down") wake.offer(Unit)
    }

    /** One connection, till it ends: whether it got as far as the host process's hello. */
    private fun run(): Boolean {
        set("connecting")
        said = ""
        val jsch = JSch()
        jsch.addIdentity("baude", key.private, key.public, null)
        val repo = TrustedKeys(ctx, host)
        lastRepo = repo
        jsch.hostKeyRepository = repo
        val s = jsch.getSession(host.user, host.address, host.port)
        s.setConfig("StrictHostKeyChecking", "yes")
        s.setConfig("PreferredAuthentications", "publickey")
        s.setConfig("kex", KEX)
        s.setConfig("server_host_key", HOST_KEYS)
        s.setConfig("PubkeyAcceptedAlgorithms", "ecdsa-sha2-nistp256")
        s.setServerAliveInterval(15_000)
        s.setServerAliveCountMax(3)
        session = s
        s.connect(15_000)
        val ch = s.openChannel("exec") as ChannelExec
        ch.setCommand("${host.command} connect")
        val input = object : java.io.FilterInputStream(ch.inputStream) { // (every byte heard: a long line coming
            override fun read(): Int = super.read().also { heardAt = SystemClock.elapsedRealtime() } // slowly isn't
            override fun read(b: ByteArray, off: Int, len: Int): Int =                                // silence)
                super.read(b, off, len).also { heardAt = SystemClock.elapsedRealtime() }
        }
        val stream = ch.outputStream
        val errors = ch.errStream
        ch.connect(15_000)
        val stderr = thread(name = "stderr-${host.address}", isDaemon = true) {
            try {
                BufferedReader(InputStreamReader(errors, Charsets.UTF_8)).forEachLine { line ->
                    if (line.isNotBlank()) said = line.trim().take(200)
                }
            } catch (_: Exception) {
            }
        }
        val queue = LinkedBlockingQueue<ByteArray>()
        val writer = thread(name = "write-${host.address}", isDaemon = true) {
            try {
                while (true) {
                    val line = queue.take()
                    if (line.isEmpty()) break
                    stream.write(line)
                    if (queue.isEmpty()) stream.flush()
                }
            } catch (_: Exception) {
                s.disconnect() // (can't write: the reader ends, and it's made again)
            }
        }
        writes = queue
        out = stream
        var reached = false
        request(JSONObject().put("op", "hello").put("client", "baude-android")) { reply ->
            val server = reply.optJSONObject("server")
            when {
                reply.optString("op") != "hello" -> {
                    said = reply.optString("error", "the host said no")
                    s.disconnect()
                }
                (server?.optInt("version", 1) ?: 1) < MIN_VERSION -> {
                    said = "this host's Baude is too old for Bots: update it"
                    s.disconnect()
                }
                else -> {
                    info = server
                    said = "" // (what it said starting: not why it goes later)
                    reached = true
                    set("up")
                    send(JSONObject().put("op", "subscribe"))
                }
            }
        }
        heardAt = SystemClock.elapsedRealtime() + START_MS // (a host process connect starts: its hello waited for)
        val pinger = thread(name = "ping-${host.address}", isDaemon = true) {
            try {
                while (!stopped && out != null) {
                    Thread.sleep(PING_MS)
                    send(JSONObject().put("op", "ping"))
                    if (SystemClock.elapsedRealtime() - heardAt > SILENT_MS) { // (nothing from it: gone, whatever
                        said = "the host stopped answering"                   // the socket says)
                        s.disconnect()
                        break
                    }
                }
            } catch (_: InterruptedException) {
            }
        }
        try {
            BufferedReader(InputStreamReader(input, Charsets.UTF_8), 1 shl 16).use { r ->
                while (!stopped) {
                    val line = r.readLine() ?: break
                    val msg = try {
                        JSONObject(line)
                    } catch (_: Exception) {
                        continue // (not a line of the host process's: a shell's greeting, say)
                    }
                    val req = msg.optInt("req", -1)
                    val call = if (req >= 0) calls.remove(req) else null
                    if (call != null) call(msg) else heard(this, msg)
                }
            }
        } finally {
            pinger.interrupt()
            writes = null
            queue.put(ByteArray(0))
            writer.join(1_000)
            for (i in 0 until 10) if (!ch.isClosed) Thread.sleep(50) // (its exit status comes after its end: a moment)
            stderr.join(500)
            if (said.isBlank() && ch.exitStatus > 0) said = "the host's command ended (status ${ch.exitStatus})"
        }
        return reached
    }

    /** A line to the host process, sent in turn on its own thread (false if there's no connection). */
    fun send(msg: JSONObject): Boolean {
        val q = writes ?: return false
        q.put((msg.toString() + "\n").toByteArray(Charsets.UTF_8))
        return true
    }

    /** A request, its reply to then (on this connection's thread), or an error reply if it can't be sent or the
     * connection goes first. */
    fun request(msg: JSONObject, then: (JSONObject) -> Unit) {
        val req = nextReq.getAndIncrement()
        calls[req] = then
        if (!send(msg.put("req", req))) {
            calls.remove(req)
            then(JSONObject().put("op", "error").put("error", "not connected"))
        }
    }

    private fun failAll() {
        val waiting = calls.values.toList()
        calls.clear()
        waiting.forEach { it(JSONObject().put("op", "error").put("error", "the connection went")) }
    }

    private fun plain(e: Exception): String {
        val m = e.message ?: e.javaClass.simpleName
        return when {
            m.contains("Auth fail", true) -> "this phone's key isn't in the host's authorized_keys"
            m.contains("timeout", true) || m.contains("timed out", true) -> "the host didn't answer"
            m.contains("UnknownHost", true) || e is java.net.UnknownHostException -> "no such host"
            m.contains("Connection refused", true) -> "the host refused the connection"
            else -> m
        }
    }
}
