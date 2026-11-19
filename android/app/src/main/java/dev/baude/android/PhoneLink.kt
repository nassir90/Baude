package dev.baude.android

import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import androidx.compose.runtime.mutableStateOf
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.LinkedBlockingQueue
import kotlin.concurrent.thread

/** This phone in the mesh, as a member: a link made from here to a host of yours that's up, as a host process makes
 * one (mesh.py: hello, then join {host} on another "<command> connect" over that host's ssh), the phone's record told
 * the mesh (who it is, and that it's linked to that host), and each tunnel the mesh opens to it served by PhoneHost as
 * a host process serves one (open, then data both ways, till close). So every member, and Baude through any of them,
 * reaches the phone's agents through that host, though the phone has no way in of its own. A leaf: one link at a
 * time, to the first of your hosts that's up, and nothing passed on through it (a tunnel to another member's refused).
 * Its record says it isn't to be dialled (dial false: there's no sshd on a phone). */
object PhoneLink {
    val state = mutableStateOf("none") // (none, linking, linked)
    val via = mutableStateOf<String?>(null) // (the host it's linked through, as you named it)
    val why = mutableStateOf<String?>(null) // (why it isn't, last time)
    private val main = Handler(Looper.getMainLooper())
    @Volatile private var running = false
    private var fails = 0
    private var later: Runnable? = null
    private val tunnels = ConcurrentHashMap<Int, Tunnel>()
    @Volatile private var writes: LinkedBlockingQueue<ByteArray>? = null
    @Volatile private var seq = 0L

    private class Tunnel(val t: Int, from: String?) : Head(from) {
        override fun send(msg: JSONObject) {
            line(JSONObject().put("op", "data").put("r", t).put("msg", msg))
        }
    }

    private fun line(o: JSONObject) {
        writes?.put((o.toString() + "\n").toByteArray(Charsets.UTF_8))
    }

    /** (main thread: a host's connection changed) Linked, if it isn't and a host of yours is up. */
    fun look() {
        if (running) return
        val conn = Mesh.hosts.firstNotNullOfOrNull { h -> Mesh.conn(h.id)?.takeIf { it.state == "up" } } ?: return
        later?.let { main.removeCallbacks(it) }
        later = null
        running = true
        state.value = "linking"
        via.value = conn.host?.label
        thread(name = "phone-link", isDaemon = true) {
            var linked = false
            var said: String? = null
            try {
                linked = link(conn)
            } catch (e: Exception) {
                said = e.message ?: e.javaClass.simpleName
            }
            main.post {
                running = false
                state.value = "none"
                why.value = said ?: if (linked) "the link to ${via.value ?: "its host"} dropped" else why.value
                fails = if (linked) 0 else fails + 1
                val wait = RETRY_MS[minOf(fails, RETRY_MS.size - 1)]
                later = Runnable { later = null; look() }.also { main.postDelayed(it, wait) }
            }
        }
    }

    private fun hello(): JSONObject = JSONObject().put("id", PhoneHost.id).put("name", PhoneHost.name)
        .put("group", PhoneHost.name).put("host", PhoneHost.name).put("version", PhoneHost.VERSION).put("dial", false)

    /** Its record, as the mesh has a member's: who it is, its providers (none: it serves no models), its links. */
    private fun record(peer: String): JSONObject = JSONObject().put("id", PhoneHost.id).put("seq", seq)
        .put("info", hello().put("providers", JSONArray())).put("links", JSONArray().put(peer))

    /** One link, till it drops: whether it was made (joined). */
    private fun link(conn: Connection): Boolean {
        val ch = conn.exec() ?: throw Exception("${conn.host?.label ?: "the host"} isn't connected")
        val queue = LinkedBlockingQueue<ByteArray>()
        var heard = SystemClock.elapsedRealtime() + START_MS
        var joined = false
        var peer: String? = null
        try {
            ch.connect(15_000)
            val out = ch.outputStream
            val writer = thread(name = "phone-link-out", isDaemon = true) {
                try {
                    while (true) {
                        val b = queue.take()
                        if (b.isEmpty()) break
                        out.write(b)
                        if (queue.isEmpty()) out.flush()
                    }
                } catch (_: Exception) {
                    ch.disconnect()
                }
            }
            writes = queue
            val pinger = thread(name = "phone-link-ping", isDaemon = true) {
                try {
                    while (!ch.isClosed) {
                        Thread.sleep(PING_MS)
                        line(JSONObject().put("op", "ping"))
                        for (t in tunnels.values) t.send(JSONObject().put("op", "ping")) // (a head through a tunnel
                        if (SystemClock.elapsedRealtime() - heard > SILENT_MS) {          // hears at least this often)
                            ch.disconnect()
                            break
                        }
                    }
                } catch (_: InterruptedException) {
                }
            }
            line(JSONObject().put("op", "hello").put("client", "baude-android").put("version", PhoneHost.VERSION).put("req", 0))
            try {
                BufferedReader(InputStreamReader(ch.inputStream, Charsets.UTF_8), 1 shl 16).use { r ->
                    while (true) {
                        val text = r.readLine() ?: break
                        heard = SystemClock.elapsedRealtime()
                        val f = try {
                            JSONObject(text)
                        } catch (_: Exception) {
                            continue // (not a line of the host process's: a shell's greeting, say)
                        }
                        val op = f.optString("op")
                        if (!joined) {
                            when (op) {
                                "hello" -> line(JSONObject().put("op", "join").put("host", hello()).put("req", 1))
                                "joined" -> {
                                    peer = f.optJSONObject("host")?.optString("id")?.takeIf(::isId)
                                        ?: throw Exception("the host gave no id")
                                    joined = true
                                    seq = maxOf(seq + 1, System.currentTimeMillis() * 1_000_000)
                                    line(JSONObject().put("op", "mesh").put("records", JSONArray().put(record(peer!!))))
                                    main.post {
                                        state.value = "linked"
                                        why.value = null
                                    }
                                }
                                "error" -> throw Exception(f.optString("error", "the host said no"))
                            }
                            continue
                        }
                        frame(f, peer!!)
                    }
                }
            } catch (e: java.io.IOException) { // (its channel let go of: closed under the reader)
                if (!joined) throw e
            } finally {
                pinger.interrupt()
                writes = null
                queue.put(ByteArray(0))
                writer.join(1_000)
            }
        } finally {
            ch.disconnect()
            for (t in tunnels.values) PhoneHost.take(t, null)
            tunnels.clear()
        }
        return joined
    }

    /** A frame on the link (mesh.py's): records, a tunnel opened to here, a tunnel's message or its end. */
    private fun frame(f: JSONObject, peer: String) {
        when (f.optString("op")) {
            "mesh" -> for (r in f.optJSONArray("records").list()) { // (this phone's own, from before a restart: its
                if (r.optString("id") == PhoneHost.id && r.optLong("seq") >= seq) {  // record told anew, newer)
                    seq = r.optLong("seq") + 1
                    line(JSONObject().put("op", "mesh").put("records", JSONArray().put(record(peer))))
                }
            }
            "open" -> {
                val t = f.optInt("t", -1).takeIf { it >= 0 } ?: return
                if (f.optString("to") != PhoneHost.id) {
                    line(JSONObject().put("op", "close").put("r", t).put("error", "${PhoneHost.name} is a phone: it passes nothing on"))
                    return
                }
                val tunnel = Tunnel(t, f.optString("from").takeIf(::isId))
                tunnels[t] = tunnel
                line(JSONObject().put("op", "opened").put("r", t))
            }
            "data" -> {
                val t = tunnels[f.optInt("t", -1)] ?: return
                f.optJSONObject("msg")?.let { PhoneHost.take(t, it) }
            }
            "close" -> tunnels.remove(f.optInt("t", -1))?.let { PhoneHost.take(it, null) }
        }
    }

    private val RETRY_MS = longArrayOf(3_000, 10_000, 30_000, 120_000)
}
