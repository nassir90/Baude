package dev.baude.android

import android.os.Handler
import android.os.Looper
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.concurrent.Executors

/** A file of an unsent message, as it's shown: its name and type (its bytes are kept on disk). */
class Attached(val name: String, val mime: String)

/** A message you sent to a chat, kept on this phone (its files too) till its host has it, as a messaging app keeps
 * one. id: the uuid it's sent with, which its host takes it once by. hostId: the connection it was sent on; machine:
 * that host's machine, if it's known, so it's found again however the machine's reached later. at: when it was put
 * in line (its chat's are sent in that order). state: "waiting" (to be sent, once its host's there and those before
 * it are), "sending", or "failed" (its host said no, why: ↑ puts it in line again, ✕ drops it). */
class Unsent(val id: String, val hostId: String, val machine: String?, val sid: String, val text: String,
             val files: List<Attached>, at: Long, state: String = "waiting", why: String? = null) {
    var at = at
    val state = mutableStateOf(state)
    val why = mutableStateOf(why)
    @Volatile var held: List<Picked>? = null // (its files' bytes, till they're written)
}

/** The messages not had by their hosts yet, kept in the app's files (unsent/<id>/: message.json, and its files by
 * number), so leaving the chat or the app going doesn't lose one. Read, written and let go of off the main thread,
 * in order (io); a message's files read from disk only as it's sent. */
object Pending {
    val all = mutableStateListOf<Unsent>()
    val io = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())
    private lateinit var root: File
    private var last = 0L

    /** (the app started) What was kept read back, off the main thread, oldest first; one that was being sent is
     * sent again (its host takes it once). then: on the main thread, once they're listed. */
    fun load(ctx: android.content.Context, then: () -> Unit) {
        root = File(ctx.filesDir, "unsent")
        io.execute {
            val found = ArrayList<Unsent>()
            for (d in root.listFiles().orEmpty()) {
                if (d.name.endsWith(".tmp")) { // (being written as the app went: not kept, nothing sent of it)
                    d.deleteRecursively()
                    continue
                }
                try {
                    val o = JSONObject(File(d, "message.json").readText())
                    val files = o.optJSONArray("files").let { a ->
                        (0 until (a?.length() ?: 0)).map { a!!.getJSONObject(it).let { f ->
                            Attached(f.optString("name"), f.optString("mime")) } }
                    }
                    found.add(Unsent(d.name, o.optString("host"), o.optString("machine").ifBlank { null },
                        o.optString("session"), o.optString("text"), files, o.optLong("at"),
                        if (o.optString("state") == "failed") "failed" else "waiting", o.optString("why").ifBlank { null }))
                } catch (_: Exception) {
                }
            }
            main.post {
                all.addAll(0, found.sortedBy { it.at })
                last = maxOf(last, found.maxOfOrNull { it.at } ?: 0L)
                then()
            }
        }
    }

    /** A time to put one in line by: after every other's. */
    fun next(): Long {
        last = maxOf(last + 1, System.currentTimeMillis())
        return last
    }

    /** The chat's (by its session's id, which no other chat's is, wherever it's reached), oldest first. */
    fun of(sid: String): List<Unsent> = all.filter { it.sid == sid }.sortedBy { it.at }

    /** Kept: the first time, made whole in a folder of its own and moved into place; after, what it is now. */
    fun keep(u: Unsent) {
        val first = u !in all
        if (first) all.add(u)
        val o = JSONObject().put("host", u.hostId).put("machine", u.machine ?: "").put("session", u.sid)
            .put("text", u.text).put("at", u.at).put("state", u.state.value).put("why", u.why.value ?: "")
            .put("files", JSONArray(u.files.map { JSONObject().put("name", it.name).put("mime", it.mime) }))
        io.execute {
            val d = File(root, u.id)
            if (first) {
                val tmp = File(root, "${u.id}.tmp").apply { deleteRecursively(); mkdirs() }
                u.held?.forEachIndexed { i, f -> File(tmp, "$i").writeBytes(f.bytes) }
                File(tmp, "message.json").writeText(o.toString())
                tmp.renameTo(d)
                u.held = null
            } else if (d.exists()) {
                File(d, "message.json.new").apply { writeText(o.toString()) }.renameTo(File(d, "message.json"))
            }
        }
    }

    /** (on io) Its files, as they're sent. */
    fun files(u: Unsent): List<Picked> = u.held ?: u.files.mapIndexed { i, f ->
        Picked(f.name, f.mime, File(File(root, u.id), "$i").readBytes())
    }

    /** Let go of: its host has it, you've dropped it, or its chat or host is gone. */
    fun drop(u: Unsent) {
        all.remove(u)
        io.execute { File(root, u.id).deleteRecursively() }
    }
}
