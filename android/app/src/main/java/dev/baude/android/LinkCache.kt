package dev.baude.android

import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** What the hosts last said, kept on this phone so the app shows it at once, its host reached or not: each host's
 * sessions as it last listed them, and the chats last open, the part of each one's transcript that was read (whole
 * lines, from a line's start: as read from its host, it's read on from where it ends). Android kills the app's process
 * and its connections while it's put away; this is what's on screen as it comes back, till its hosts are reached again. */
object LinkCache {
    private const val CHATS = 12          // (the chats kept: those last open)
    const val MOST = 4_000_000            // (a chat's transcript kept: its last this many bytes at most)
    private val io = java.util.concurrent.Executors.newSingleThreadExecutor()
    private val later = java.util.concurrent.Executors.newSingleThreadScheduledExecutor()
    private var latest: Map<String, List<Msg.Session>> = emptyMap()
    private var root: File? = null
    private var sessionsDue = false

    fun start(filesDir: File) {
        root = File(filesDir, "link-cache").apply { mkdirs() }
    }

    private fun name(key: String) = java.security.MessageDigest.getInstance("SHA-1").digest(key.toByteArray())
        .joinToString("") { "%02x".format(it) }

    private fun write(f: File, bytes: ByteArray) {
        val tmp = File(f.path + ".new")
        tmp.writeBytes(bytes)
        tmp.renameTo(f)
    }

    /** The sessions each of hosts last listed (none kept: none). */
    fun sessions(hosts: Collection<String>): Map<String, List<Msg.Session>> {
        val f = File(root ?: return emptyMap(), "sessions.json")
        val o = try { JSONObject(f.readText()) } catch (_: Exception) { return emptyMap() }
        return hosts.filter { o.has(it) }.associateWith { h ->
            o.optJSONArray(h).list().mapNotNull { try { Msg.Session.from(it) } catch (_: Exception) { null } }
        }
    }

    /** The sessions the hosts list now, kept (once every couple of seconds at most: they're listed again as any
     * changes). */
    fun keepSessions(now: Map<String, List<Msg.Session>>) {
        root ?: return
        synchronized(this) {
            latest = now
            if (sessionsDue) return
            sessionsDue = true
        }
        later.schedule({
            val kept = synchronized(this) { sessionsDue = false; latest }
            try {
                val o = JSONObject()
                for ((h, l) in kept) o.put(h, JSONArray(l.map { it.json() }))
                write(File(root, "sessions.json"), o.toString().toByteArray())
            } catch (_: Exception) {
            }
        }, 2, java.util.concurrent.TimeUnit.SECONDS)
    }

    private fun file(key: String): File? = root?.let { File(File(it, "chats"), name(key)) }

    /** Whether a chat's transcript is kept. */
    fun has(hostId: String, sid: String) = file("$hostId/$sid")?.exists() == true

    /** A chat's transcript as it was kept, its last most bytes of it at most, from a line's start: where that starts in
     * the transcript, and its bytes from there (null: none), told off the main thread. */
    fun chat(hostId: String, sid: String, most: Long, then: (Pair<Long, ByteArray>?) -> Unit) {
        val key = "$hostId/$sid"
        val f = file(key) ?: return then(null)
        io.execute {
            then(try {
                java.io.RandomAccessFile(f, "r").use { r ->
                    val head = JSONObject(String((r.readLine() ?: "").toByteArray(Charsets.ISO_8859_1), Charsets.UTF_8))
                    if (head.optString("key") != key) null
                    else {
                        val body = r.filePointer
                        val from = maxOf(body, r.length() - most - 1) // (and the byte before it: is a line's start there?)
                        val got = ByteArray((r.length() - from).toInt())
                        r.seek(from)
                        r.readFully(got)
                        val cut = if (from == body) 0 else got.indexOf('\n'.code.toByte()).let { if (it < 0) got.size else it + 1 }
                        head.getLong("start") + (from + cut - body) to got.copyOfRange(cut, got.size)
                    }
                }
            } catch (_: Exception) {
                null
            })
        }
    }

    /** A chat's transcript as it's been read, cut to its last MOST bytes (from a line's start) once it's grown well past
     * them: what's kept of it, and what's held of it meanwhile. */
    fun trim(chat: Chat) {
        if (chat.raw.size() <= MOST + MOST / 4) return
        val bytes = chat.raw.toByteArray()
        var cut = bytes.size - MOST
        while (cut < bytes.size && bytes[cut - 1] != '\n'.code.toByte()) cut++
        chat.raw.reset()
        chat.raw.write(bytes, cut, bytes.size - cut)
        chat.rawFrom += cut
    }

    /** A host removed: what's kept of it let go of, its sessions and its chats' transcripts. */
    fun forget(hostId: String) {
        val dir = File(root ?: return, "chats")
        synchronized(this) { latest = latest - hostId }
        io.execute {
            try {
                val f = File(root, "sessions.json")
                val o = JSONObject(f.readText())
                if (o.has(hostId)) {
                    o.remove(hostId)
                    write(f, o.toString().toByteArray())
                }
            } catch (_: Exception) {
            }
            for (f in dir.listFiles().orEmpty()) try {
                val key = f.bufferedReader().use { JSONObject(it.readLine() ?: "").optString("key") }
                if (key.startsWith("$hostId/")) f.delete()
            } catch (_: Exception) {
            }
        }
    }

    /** A chat's transcript as it's been read, kept: its last MOST bytes, from a line's start; the oldest kept let go
     * of past CHATS. */
    fun keepChat(chat: Chat) {
        val dir = File(root ?: return, "chats")
        if (chat.rawFrom < 0) return
        var bytes = chat.raw.toByteArray()
        var start = chat.rawFrom
        if (bytes.size > MOST) {
            var cut = bytes.size - MOST
            while (cut < bytes.size && bytes[cut - 1] != '\n'.code.toByte()) cut++
            bytes = bytes.copyOfRange(cut, bytes.size)
            start += cut
        }
        val key = "${chat.hostId}/${chat.sid}"
        val head = (JSONObject().put("key", key).put("start", start).toString() + "\n").toByteArray()
        io.execute {
            try {
                dir.mkdirs()
                write(File(dir, name(key)), head + bytes)
                dir.listFiles().orEmpty().filter { !it.name.endsWith(".new") }.sortedByDescending { it.lastModified() }
                    .drop(CHATS).forEach { it.delete() }
            } catch (_: Exception) {
            }
        }
    }
}
