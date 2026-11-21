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

const val PING_MS = WireConstants.PING_S * 1000L     // (each end says something at least this often)
const val SILENT_MS = WireConstants.SILENT_S * 1000L // (silent this long, the other end's taken for gone)
const val START_MS = 14_000L  // (connect may start the host process: up to 20 s to its hello, as Baude's 25 allow)
const val OPEN_MS = WireConstants.OPEN_S * 1000L // (a member's tunnel has this long to open)
/** msg as it goes, as wire.py's Channel.send has it: its nulls left out where its fields can't be null
 * (WireCheck.unnulled). One the wire's schema hasn't (an op, a field: WireCheck.problem) is said in the log and not
 * sent (null); a host's answer (answer true) goes as its request's INVALID refusal instead, if it has a req. */
fun checked(msg: JSONObject, answer: Boolean = false): JSONObject? {
    val out = WireCheck.unnulled(msg)
    val bad = WireCheck.problem(out) ?: return out.also { WireStrict.check("sent", it) }
    android.util.Log.w("wire", "not sent: $bad")
    val req = out.opt("req")?.takeIf { it != JSONObject.NULL } ?: return null
    return if (answer) WireCheck.refused(WireCheck.INVALID, req, bad) else null
}

/** A head's own request that the schema hasn't (an op, a field: WireCheck.problem), as its INVALID refusal, for its
 * caller: it isn't sent (null: it's one the schema has). */
fun ownRefusal(msg: JSONObject): JSONObject? {
    val bad = WireCheck.problem(msg) ?: return null
    android.util.Log.w("wire", "not sent: $bad")
    return WireCheck.refused(WireCheck.INVALID, msg.opt("req"), bad)
}

/** Every message sent and read type-checked against the schema (WireCheck.problems), what's wrong logged under the
 * wire tag, each once, as $BAUDE_WIRE_STRICT has wire.py do: on if the app's external files folder has a file
 * wire-strict in it as it starts (adb shell touch /sdcard/Android/data/dev.baude.android/files/wire-strict). */
object WireStrict {
    @Volatile var on = false
        private set
    private val said = java.util.Collections.newSetFromMap(ConcurrentHashMap<String, Boolean>())

    fun look(ctx: Context) {
        on = try {
            ctx.getExternalFilesDir(null)?.let { java.io.File(it, "wire-strict").exists() } == true
        } catch (_: Exception) {
            false
        }
        if (on) android.util.Log.i("wire", "strict: what's sent and read is checked")
    }

    /** A message sent or read (how), as it is on the wire: what of it isn't as the schema has it, in the log. */
    fun check(how: String, msg: JSONObject) {
        if (!on) return
        for (p in WireCheck.problems(JSONObject(msg.toString()))) {
            val line = "$how: $p"
            if (line in said) continue
            if (said.size < SAID_MOST) said.add(line) // (each once, the first SAID_MOST; after that, all of them)
            android.util.Log.w("wire", line)
        }
    }

    private const val SAID_MOST = 1000
}

/** What an error says, for a person: its error, and a refusal's detail after it (null: it says nothing). */
val Msg.Error.said: String? get() = error?.let { e -> detail?.ifBlank { null }?.let { "$e: $it" } ?: e }

/** Why a request didn't do what it asked, for a person: an error's (said), else what its reply's own error field
 * (own) says; "" for neither. */
fun said(r: JSONObject, own: String?): String = (if (r.optString("op") == Op.ERROR) Msg.Error.from(r).said else own).orEmpty()

/** Is it an id a host may give (its own, a session's), as wire.is_id has it: a plain word, no path in it? */
fun isId(value: String?): Boolean = value != null && Regex("[A-Za-z0-9][A-Za-z0-9_-]{0,63}").matches(value)

const val RELAY_RETRY_MS = 10_000L // (a member that's dropped, tried again this often while it's listed: Baude's 10 s)

/** What ssh offers and takes here: what Android's own crypto has, P-256's ECDH and ECDSA, RSA and AES. */
private val KEX = "ecdh-sha2-nistp256,ecdh-sha2-nistp384,ecdh-sha2-nistp521,diffie-hellman-group16-sha512," +
    "diffie-hellman-group14-sha256"
private val HOST_KEYS = "ecdsa-sha2-nistp256,ecdsa-sha2-nistp384,ecdsa-sha2-nistp521,rsa-sha2-512,rsa-sha2-256"

/** Where a connection goes: a host you added, over ssh; or a member of the mesh one of them is in, through it (relay,
 * as Baude reaches a member through its own machine's host process). */
sealed class Place {
    abstract val id: String

    data class Direct(val host: Host) : Place() {
        override val id: String get() = host.id
    }

    data class Member(val member: String) : Place() {
        override val id: String get() = "mesh:$member"
    }

    /** This phone's own host (PhoneHost), talked to in the app. */
    object Phone : Place() {
        override val id: String get() = "phone"
    }
}

/** A connection to a host's process, as Baude's own (wire.connect_ssh): ssh to the host, "<command> connect" run
 * there, and the host process's JSON lines over its stdin and stdout. A member of the mesh is reached the same way
 * through a host that's up and lists it (route): another "<command> connect" on that host's ssh, told to relay to the
 * member, and from then on a tunnel to it, hello first as ever (wire.through). Made again whenever it drops, a little
 * later each time (a member: every RELAY_RETRY_MS while a host lists it, else once one does); not while the host's key
 * isn't one you've trusted (shown, for you to say). */
open class Connection(
    private val ctx: Context,
    val place: Place,
    private val identify: (JSch, (String) -> Unit) -> Unit, // (its keys given to JSch; why one couldn't sign, said)
    private val route: (String) -> Connection?,
    private val heard: (Connection, JSONObject) -> Unit,
    private val subscribes: Boolean = true, // (false: one for a watch of its own, which a connection carries one of)
    private val changed: (Connection) -> Unit,
) {
    val id: String get() = place.id
    val host: Host? get() = (place as? Place.Direct)?.host
    @Volatile var state = "connecting" // (connecting, up, down, key: the host's key waits to be trusted)
    @Volatile var why: String? = null
    @Volatile var info: Msg.Info? = null
    @Volatile var shown: ShownKey? = null
    @Volatile var via: Connection? = null // (a member's: the host it's reached through, this time)
    @Volatile var listed = true // (a member's: whether a host that's up lists it)
    @Volatile private var stopped = false
    @Volatile private var out: OutputStream? = null
    @Volatile private var session: Session? = null
    @Volatile private var end: () -> Unit = {} // (this connection let go of: its ssh, or a member's channel on another's)
    private val calls = ConcurrentHashMap<Int, (JSONObject) -> Unit>()

    companion object {
        const val NOT_CONNECTED = "not connected"  // (a request's error: never written)
        const val WENT = "the connection went"     // (a request's error: written, and maybe had)
    }
    private val nextReq = AtomicInteger(100)
    @Volatile private var writes: LinkedBlockingQueue<ByteArray>? = null // (sent on a thread of its own: Android
    // allows no network from its main thread)

    open fun start() {
        thread(name = "conn-$id", isDaemon = true) { loop() }
    }

    open fun stop() {
        stopped = true
        wake.offer(Unit)
        end()
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
                set("down", if (e is NoRoute) e.message else plain(e))
            } finally {
                out = null
                end()
                end = {}
                session = null
                failAll()
            }
            if (place is Place.Member) { // (as Baude's: tried again soon while a host lists it, else once one does)
                if (listed) wake.poll(RELAY_RETRY_MS, TimeUnit.MILLISECONDS) else wake.take()
                continue
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
    @Volatile private var signError: String? = null // (why this phone's key couldn't sign, this try)

    /** Tried again now, not after the wait (the network's back, or a member's listed again). */
    fun kick() {
        if (state == "down") wake.offer(Unit)
    }

    private class NoRoute(why: String) : Exception(why)

    /** Another "<command> connect" on this host's ssh, for a member reached through it (null if it isn't up). */
    fun exec(): ChannelExec? {
        val s = session ?: return null
        val h = host ?: return null
        if (state != "up" || !s.isConnected) return null
        return (s.openChannel("exec") as ChannelExec).apply { setCommand("${h.command} connect") }
    }

    /** The channel to the host process: ssh to a host you added, or one on the ssh of the host a member's reached
     * through. */
    private fun open(): ChannelExec = when (val p = place) {
        is Place.Direct -> {
            val jsch = JSch()
            signError = null
            identify(jsch) { signError = it }
            val repo = TrustedKeys(ctx, p.host)
            lastRepo = repo
            jsch.hostKeyRepository = repo
            val s = jsch.getSession(p.host.user, p.host.address, p.host.port)
            s.setConfig("StrictHostKeyChecking", "yes")
            s.setConfig("PreferredAuthentications", "publickey")
            s.setConfig("kex", KEX)
            s.setConfig("server_host_key", HOST_KEYS)
            s.setConfig("PubkeyAcceptedAlgorithms", "ecdsa-sha2-nistp256")
            s.setServerAliveInterval(15_000)
            s.setServerAliveCountMax(3)
            session = s
            end = { s.disconnect() }
            s.connect(15_000)
            (s.openChannel("exec") as ChannelExec).apply { setCommand("${p.host.command} connect") }
        }
        is Place.Phone -> throw NoRoute("this phone's own host isn't reached over ssh")
        is Place.Member -> {
            val v = route(p.member) ?: throw NoRoute("none of your hosts reaches it now")
            via = v
            val ch = try {
                v.exec()
            } catch (_: Exception) {
                null
            } ?: throw NoRoute("${v.host?.label ?: "its host"} went")
            end = { ch.disconnect() }
            ch
        }
    }

    /** One connection, till it ends: whether it got as far as the host process's hello. */
    private fun run(): Boolean {
        set("connecting")
        said = ""
        val ch = open()
        val input = object : java.io.FilterInputStream(ch.inputStream) { // (every byte heard: a long line coming
            override fun read(): Int = super.read().also { heardAt = SystemClock.elapsedRealtime() } // slowly isn't
            override fun read(b: ByteArray, off: Int, len: Int): Int =                                // silence)
                super.read(b, off, len).also { heardAt = SystemClock.elapsedRealtime() }
        }
        val stream = ch.outputStream
        val errors = ch.errStream
        ch.connect(15_000)
        val stderr = thread(name = "stderr-$id", isDaemon = true) {
            try {
                BufferedReader(InputStreamReader(errors, Charsets.UTF_8)).forEachLine { line ->
                    if (line.isNotBlank()) said = line.trim().take(200)
                }
            } catch (_: Exception) {
            }
        }
        val queue = LinkedBlockingQueue<ByteArray>()
        val writer = thread(name = "write-$id", isDaemon = true) {
            try {
                while (true) {
                    val line = queue.take()
                    if (line.isEmpty()) break
                    stream.write(line)
                    if (queue.isEmpty()) stream.flush()
                }
            } catch (_: Exception) {
                end() // (can't write: the reader ends, and it's made again)
            }
        }
        writes = queue
        out = stream
        var reached = false
        val hello = {
            request(Msg.Hello(client = "baude-android", commit = BuildConfig.COMMIT.ifEmpty { null }).json()) { reply ->
                val server = Msg.HelloReply.from(reply).server
                when {
                    reply.optString("op") != Op.HELLO -> {
                        said = Msg.Error.from(reply).said ?: Wording.BotBrowser.hostSaidNo
                        end()
                    }
                    else -> {
                        info = server
                        said = "" // (what it said starting: not why it goes later)
                        reached = true
                        set("up")
                        if (subscribes) // (archived ones too: the home screen lists them, folded)
                            send(Msg.Subscribe(archived = true).json())
                    }
                }
            }
        }
        val member = (place as? Place.Member)?.member
        if (member == null) hello()
        else request(Msg.Relay(to = member).json()) { reply -> // (wire.through)
            if (reply.optString("op") == Op.RELAYED) hello()
            else {
                said = Msg.Error.from(reply).said ?: "${via?.host?.label ?: "its host"} couldn't reach it"
                end()
            }
        }
        heardAt = SystemClock.elapsedRealtime() + START_MS + // (a host process connect starts: its hello waited for;
            if (member != null) OPEN_MS else 0L              // a member's tunnel, the time it has to open)
        val pinger = thread(name = "ping-$id", isDaemon = true) {
            try {
                while (!stopped && out != null) {
                    Thread.sleep(PING_MS)
                    send(Msg.Ping().json())
                    if (SystemClock.elapsedRealtime() - heardAt > SILENT_MS) { // (nothing from it: gone, whatever
                        said = "the host stopped answering"                   // the socket says)
                        end()
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
                    WireStrict.check("read", msg)
                    val req = (msg.opt("req") as? Number)?.toInt() ?: -1 // (any line's: every op's may have one)
                    val call = if (req >= 0) calls.remove(req) else null
                    if (call != null) call(msg) else heard(this, msg)
                }
            }
        } catch (e: java.io.IOException) { // (a member's channel let go of: closed under the reader)
            if (place is Place.Direct) throw e
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

    /** A line to the host process, sent in turn on its own thread (false if there's no connection; one the schema
     * hasn't, said in the log, isn't sent). */
    open fun send(msg: JSONObject): Boolean {
        val q = writes ?: return false
        val line = checked(msg) ?: return true
        q.put((line.toString() + "\n").toByteArray(Charsets.UTF_8))
        return true
    }

    /** A request, its reply to then (on this connection's thread), or an error reply if it can't be sent or the
     * connection goes first (one the schema hasn't: its INVALID refusal, here, not sent). */
    open fun request(msg: JSONObject, then: (JSONObject) -> Unit) {
        val req = nextReq.getAndIncrement()
        ownRefusal(msg.put("req", req))?.let { return then(it) }
        calls[req] = then
        if (!send(msg)) {
            calls.remove(req)
            then(Msg.Error(error = NOT_CONNECTED).json())
        }
    }

    private fun failAll() {
        val waiting = calls.values.toList()
        calls.clear()
        waiting.forEach { it(Msg.Error(error = WENT).json()) }
    }

    private fun plain(e: Exception): String {
        val m = e.message ?: e.javaClass.simpleName
        return when {
            signError != null -> "this phone's key couldn't sign: $signError"
            m.contains("Auth fail", true) -> "this phone's key isn't in the host's authorized_keys"
            m.contains("timeout", true) || m.contains("timed out", true) -> "the host didn't answer"
            m.contains("UnknownHost", true) || e is java.net.UnknownHostException -> "no such host"
            m.contains("Connection refused", true) -> "the host refused the connection"
            else -> m
        }
    }
}
