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
    private const val MOST = 4_000_000    // (a chat's transcript kept: its last this many bytes at most)
    private val io = java.util.concurrent.Executors.newSingleThreadExecutor()
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
    fun keepSessions(now: () -> Map<String, List<Msg.Session>>) {
        if (sessionsDue || root == null) return
        sessionsDue = true
        Mesh.later(2_000) {
            sessionsDue = false
            val o = JSONObject()
            for ((h, l) in now()) o.put(h, JSONArray(l.map { it.json() }))
            val bytes = o.toString().toByteArray()
            io.execute { try { write(File(root, "sessions.json"), bytes) } catch (_: Exception) {} }
        }
    }

    /** A chat's transcript as it was kept: where it starts in the transcript, and its bytes from there (null: none). */
    fun chat(hostId: String, sid: String): Pair<Long, ByteArray>? {
        val dir = File(root ?: return null, "chats")
        val f = File(dir, name("$hostId/$sid"))
        return try {
            val bytes = f.readBytes()
            val nl = bytes.indexOf('\n'.code.toByte())
            val head = JSONObject(String(bytes, 0, nl, Charsets.UTF_8))
            if (head.optString("key") != "$hostId/$sid") return null
            head.getLong("start") to bytes.copyOfRange(nl + 1, bytes.size)
        } catch (_: Exception) {
            null
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
