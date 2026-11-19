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
import java.util.concurrent.atomic.AtomicInteger
import kotlin.concurrent.thread

const val PING_MS = 5_000L // (as wire.PING_S: a host process takes a connection silent for 16 s for gone)

/** What ssh's offered and taken here: Android's own crypto has P-256's ECDH and ECDSA, RSA and AES, not X25519 or
 * Ed25519 (JSch makes those with Java 11's and 15's classes, or BouncyCastle's: none here). */
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
            try {
                run()
                wait = 1_000L
                if (!stopped) set("down", "the host went")
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
            Thread.sleep(wait)
            wait = (wait * 2).coerceAtMost(30_000L)
        }
    }

    @Volatile private var lastRepo: TrustedKeys? = null

    private fun run() {
        set("connecting")
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
        val input = ch.inputStream
        val stream = ch.outputStream
        ch.connect(15_000)
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
        request(JSONObject().put("op", "hello").put("client", "baude-android")) { reply ->
            if (reply.optString("op") == "hello") {
                info = reply.optJSONObject("server")
                set("up")
                send(JSONObject().put("op", "subscribe"))
            } else set("down", reply.optString("error", "the host said no"))
        }
        val pinger = thread(name = "ping-${host.address}", isDaemon = true) {
            try {
                while (!stopped && out != null) {
                    Thread.sleep(PING_MS)
                    send(JSONObject().put("op", "ping"))
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
        }
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
