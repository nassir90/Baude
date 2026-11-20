package dev.baude.android

import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.concurrent.Executors

/** A message you sent to a chat, kept on this phone (its files too) till its host has it, as a messaging app keeps
 * one. id: the uuid it's sent with, which its host and its agent keep it by. state: "sending"; "waiting" (its host
 * wasn't there: sent once it is); "checking" (the connection went as it was sent, or the app did: its chat read once
 * its host's back, and let go of if it's there); "failed" (its host said no, why, or it wasn't there after all: ↑ to
 * send it again, ✕ to drop it). */
class Unsent(val id: String, val hostId: String, val sid: String, val text: String, val files: List<Picked>,
             state: String = "sending", why: String? = null) {
    val state = mutableStateOf(state)
    val why = mutableStateOf(why)
}

/** The messages not had by their hosts yet, kept in the app's files (unsent/<id>/: what it is, and its files), so
 * leaving the chat or the app going doesn't lose one; written and let go of off the main thread, in order. */
object Pending {
    val all = mutableStateListOf<Unsent>()
    private val io = Executors.newSingleThreadExecutor()
    private lateinit var root: File

    /** (the app started) What was kept read back: one that was being sent, checked for once its host's back. */
    fun load(ctx: android.content.Context) {
        root = File(ctx.filesDir, "unsent")
        for (d in root.listFiles().orEmpty()) try {
            val o = JSONObject(File(d, "message.json").readText())
            val files = o.optJSONArray("files").let { a ->
                (0 until (a?.length() ?: 0)).map { i ->
                    val f = a!!.getJSONObject(i)
                    Picked(f.optString("name"), f.optString("mime"), File(d, "$i").readBytes())
                }
            }
            val state = o.optString("state").let { if (it == "sending") "checking" else it }
            all.add(Unsent(d.name, o.optString("host"), o.optString("session"), o.optString("text"), files, state,
                o.optString("why").ifBlank { null }))
        } catch (_: Exception) {
            d.deleteRecursively() // (half written, as the app went: nothing to send)
        }
    }

    /** The chat's (where it's reached now), oldest first. */
    fun of(hostId: String, sid: String): List<Unsent> = all.filter { Mesh.at(it.hostId) == hostId && it.sid == sid }

    /** Kept (its files the first time), or kept as it is now. */
    fun keep(u: Unsent) {
        val first = u !in all
        if (first) all.add(u)
        val o = JSONObject().put("host", u.hostId).put("session", u.sid).put("text", u.text)
            .put("state", u.state.value).put("why", u.why.value ?: "")
            .put("files", JSONArray(u.files.map { JSONObject().put("name", it.name).put("mime", it.mime) }))
        io.execute {
            val d = File(root, u.id).apply { mkdirs() }
            if (first) u.files.forEachIndexed { i, f -> File(d, "$i").writeBytes(f.bytes) }
            File(d, "message.json.new").apply { writeText(o.toString()) }.renameTo(File(d, "message.json"))
        }
    }

    /** Let go of: its host has it, or you've dropped it. */
    fun drop(u: Unsent) {
        all.remove(u)
        io.execute { File(root, u.id).deleteRecursively() }
    }
}
