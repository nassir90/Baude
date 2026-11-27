package dev.baude.android

import android.content.Context
import android.widget.Toast
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.json.JSONObject
import java.io.File

/** A message you sent that its transcript doesn't show yet: drawn as the transcript will draw it (lines), its text and
 * files kept to put back in the box if its host says no. */
class Outgoing(val id: String, val lines: List<Line>, val text: String, val files: List<Picked>, val kept: Boolean = false) {
    var accepted = false // (its host said it took it; kept: in its chat's draft on disk as tried till then)
}

/** A message written while a turn runs, or while its host isn't reached (offline), queued under the chat. uuid: the
 * one it's sent by: if it was sent and its host may have taken it (tried: its link went before the answer came), sent
 * by it again, so its host takes it once. failed: why its host said no to it, sent by itself; it waits for you (↑). */
class Queued(val text: String, val files: List<Picked>, val uuid: String? = null, val offline: Boolean = false,
             val tried: Boolean = false, val failed: String? = null) {
    var hosted = 0 // (in its chat's queue on its host, by its uuid, which sends it: 1 asked to add it, 2 it has it)
}

object Queue {
    private val ALL = Queued("", emptyList())

    /** What's written, sent as the desktop's send does: at once, or, while a turn runs, queued under the chat; while
     * its host isn't reached, queued to go once it's back (resend). */
    fun submit(chat: Chat, working: Boolean, text: String, files: List<Picked>) {
        val reached = Mesh.conn(chat.hostId)?.state == "up"
        if (chat.sender == null && (working || !reached))
            chat.queued.add(Queued(text.trim(), files, java.util.UUID.randomUUID().toString(), offline = !reached)
                .also { if (reached) Mesh.queueAdd(chat, it) }) // (a host's chat's: kept there, and sent by it)
        else chat.send(text, files)
    }

    /** A message whose host didn't answer as it was sent (its link went: it may have been taken) put back in the queue,
     * after those put back before it, to go by its uuid once its host's back. */
    fun putBackTried(chat: Chat, q: Queued) {
        val at = chat.queued.indexOfFirst { !it.tried }.let { if (it < 0) chat.queued.size else it }
        chat.queued.add(at, Queued(q.text, q.files, q.uuid, offline = true, tried = true))
    }

    /** (its host back, and what it missed read: those it took already are gone from the queue) What was queued while
     * its host wasn't reached sent, each by its own uuid, in order, unless a turn's going (then at its end: flush). */
    fun resend(chat: Chat) {
        val going = chat.queued.filter { it.offline && it.failed == null }
        if (going.isEmpty()) return
        chat.queued.removeAll(going.toSet())
        for (q in going) chat.send(q.text, q.files, q.uuid)
    }

    /** The queue sent as one message when a turn ends, including interruption (up to the one ↑ was pressed on, if one was). */
    fun flush(chat: Chat) {
        val upto = chat.queueUpto ?: ALL
        chat.stopAsked = false
        chat.queueUpto = null
        if (chat.queued.isEmpty()) return
        val turns = if (upto === ALL) chat.queued.filter { it.failed == null && it.hosted == 0 } // (one its host said no
            // to: by ↑ only; one its host has: sent by it)
            else chat.queued.take(chat.queued.indexOf(upto) + 1)
        if (turns.isEmpty()) return
        if (Mesh.conn(chat.hostId)?.state != "up") { // (kept, to go once its host's back: resend)
            for (q in turns) chat.queued[chat.queued.indexOf(q)] = Queued(q.text, q.files, q.uuid ?: java.util.UUID.randomUUID().toString(),
                offline = true, tried = q.tried)
            return
        }
        Mesh.queueDrop(chat, turns)
        chat.queued.removeAll(turns.toSet())
        if (turns.any { it.tried }) { // (one its host may have taken: each by its own uuid, so it's taken once)
            for (q in turns) chat.send(q.text, q.files, q.uuid)
            return
        }
        val text = turns.map { it.text }.filter { it.isNotBlank() }.joinToString("\n\n")
        val files = turns.flatMap { it.files }
        chat.send(text, files, turns.singleOrNull()?.uuid)
    }

    /** ↑ on a queued message: it and those before it sent together, now: to the turn going on, which takes it in and
     * goes on (as Baude's send_at_turn), or as a turn of its own. */
    fun sendNow(chat: Chat, q: Queued) {
        chat.queueUpto = q
        flush(chat)
    }

    /** A queued message taken out (✕, or its revert): out of its host's queue too, if it has it. */
    fun drop(chat: Chat, q: Queued) {
        Mesh.queueDrop(chat, listOf(q))
        chat.queued.remove(q)
    }

    private val io = java.util.concurrent.Executors.newSingleThreadExecutor()
    private lateinit var dir: File

    private fun file(key: String) = File(dir, java.security.MessageDigest.getInstance("SHA-1").digest(key.toByteArray())
        .joinToString("") { "%02x".format(it) } + ".json")

    private fun filesJson(files: List<Picked>) = org.json.JSONArray(files.map {
        JSONObject().put("name", it.name).put("mime", it.mime).put("data", it.base64()) })

    private fun filesOf(a: org.json.JSONArray?) = (0 until (a?.length() ?: 0)).map { a!!.getJSONObject(it).let { x ->
        Picked(x.optString("name"), x.optString("mime"), java.util.Base64.getDecoder().decode(x.optString("data"))) } }

    private fun write(key: String, d: Mesh.Draft?) {
        val f = file(key)
        if (d == null || d.empty) {
            f.delete()
            return
        }
        val o = JSONObject().put("key", key).put("text", d.text).put("files", filesJson(d.files))
            .put("queued", org.json.JSONArray(d.queue.map { q -> JSONObject().put("text", q.text)
                .put("files", filesJson(q.files)).put("offline", q.offline).put("tried", q.tried)
                .apply { q.uuid?.let { put("uuid", it) }; q.failed?.let { put("failed", it) } } }))
            .put("hold", d.hold).put("dirty", d.dirty).apply { d.upto?.let { put("upto", it) } }
        File(dir, f.name + ".new").apply { writeText(o.toString()) }.renameTo(f)
    }

    /** (the app started) The drafts kept on disk read back, each by its chat's key; those of hosts that are gone stay. */
    fun loadDrafts(filesDir: File) {
        dir = File(filesDir, "drafts").apply { mkdirs() }
        for (f in dir.listFiles().orEmpty()) {
            if (!f.name.endsWith(".json")) continue
            try {
                val o = JSONObject(f.readText())
                val queued = o.optJSONArray("queued")
                val queue = (0 until (queued?.length() ?: 0)).map { queued!!.getJSONObject(it).let { q ->
                    Queued(q.optString("text"), filesOf(q.optJSONArray("files")), q.optString("uuid").ifBlank { null },
                        q.optBoolean("offline"), q.optBoolean("tried"), q.optString("failed").ifBlank { null }) } }
                Mesh.drafts[o.getString("key")] = Mesh.Draft(o.optString("text"), filesOf(o.optJSONArray("files")), queue,
                    if (o.has("upto")) o.optInt("upto") else null, o.optBoolean("hold"), o.optBoolean("dirty"))
            } catch (_: Exception) {
            }
        }
    }

    /** A chat's draft kept: in memory (for when it's opened again) and on disk. */
    fun setDraft(key: String, d: Mesh.Draft?) {
        if (d == null || d.empty) Mesh.drafts.remove(key) else Mesh.drafts[key] = d
        io.execute { try { write(key, d) } catch (_: Exception) { } }
    }

    private fun joined(a: String, b: String) = listOf(a, b).filter { it.isNotBlank() }.joinToString("\n\n")

    /** What a chat wasn't sent (its host said no) kept: in the box if it's open, else after what its draft has. */
    fun addDraft(hostId: String, sid: String, text: String, files: List<Picked>) {
        val chat = Mesh.open?.takeIf { it.hostId == hostId && it.sid == sid }
        if (chat != null) {
            chat.draft.value = listOfNotNull(chat.draft.value, text.ifBlank { null }).joinToString("\n").ifBlank { null }
            chat.files.addAll(0, files)
            return
        }
        val key = "$hostId/$sid"
        val was = Mesh.drafts[key]
        setDraft(key, Mesh.Draft(joined(was?.text.orEmpty(), text), was?.files.orEmpty() + files, was?.queue.orEmpty(),
            was?.upto, was?.hold == true, dirty = text.isNotBlank() || was?.dirty == true))
    }

    /** A chat's draft as it stands: what's written and its files, and its queue: the one ↑ was pressed on (its place)
     * and whether its turn was stopped (hold: its end sends nothing). */
    private fun draftOf(chat: Chat): Mesh.Draft {
        val text = listOf(chat.draft.value.orEmpty(), chat.typed).filter { it.isNotBlank() }.joinToString("\n\n")
        val going = chat.sent.filter { it.kept && !it.accepted } // (sent, its host's answer not come: sent again by its
            .map { Queued(it.text, it.files, it.id, offline = true, tried = true) } // uuid if the app's ended first)
        val mine = chat.queued.filter { it.hosted == 0 } // (its host's kept there)
        val upto = mine.indexOf(chat.queueUpto).takeIf { it >= 0 }?.let { it + going.size }
        return Mesh.Draft(text, chat.files.toList(), going + mine, upto, chat.stopAsked && mine.isNotEmpty(),
            chat.draftDirty)
    }

    /** A message its host took (its uuid): out of its chat's queue, the open chat's or its draft's. */
    fun taken(chat: Chat, uuid: String) = taken(chat.hostId, chat.sid, uuid)

    fun taken(hostId: String, sid: String, uuid: String) = change(hostId, sid, uuid) { null }

    /** A queued message its host said no to (why), sent by itself (Mesh.sendQueued): kept in its queue as not sent,
     * not sent again till you say (↑). */
    fun refused(hostId: String, sid: String, uuid: String, why: String) = change(hostId, sid, uuid) { q ->
        Queued(q.text, q.files, q.uuid, q.offline, q.tried, failed = why)
    }

    /** The queued message by uuid in a chat's queue (the open chat's, or its draft's) changed to what to says, or
     * taken out (null). */
    private fun change(hostId: String, sid: String, uuid: String, to: (Queued) -> Queued?) {
        val chat = Mesh.open?.takeIf { it.hostId == hostId && it.sid == sid && it.sender == null }
        if (chat != null) {
            val i = chat.queued.indexOfFirst { it.uuid == uuid }
            val now = if (i >= 0) to(chat.queued[i]) else null
            if (now != null) chat.queued[i] = now else if (i >= 0) chat.queued.removeAt(i)
            save(chat)
            return
        }
        val key = "$hostId/$sid"
        val d = Mesh.drafts[key] ?: return
        val i = d.queue.indexOfFirst { it.uuid == uuid }
        if (i < 0) return
        val now = to(d.queue[i])
        val queue = d.queue.toMutableList().apply { if (now == null) removeAt(i) else set(i, now) }
        val upto = d.upto?.let { if (now == null && i <= it) it - 1 else it }?.takeIf { it >= 0 }
        setDraft(key, Mesh.Draft(d.text, d.files, queue, upto, d.hold && queue.isNotEmpty(), d.dirty))
    }

    /** (a host's sessions listed) A chat not open that was left with a queue (its draft's), whose turn that host lists
     * over now, where it was at one before: what's queued sent to it, as the open chat's is as its turn ends (flush):
     * all of it, or up to the one ↑ was pressed on. */
    fun sendLeft(hostId: String, before: List<Msg.Session>?, now: List<Msg.Session>) {
        fun working(sid: String, l: List<Msg.Session>?) = l?.firstOrNull { it.id == sid }?.state.let { it == "working" || it == "waiting" }
        for ((key, d) in Mesh.drafts.toList()) {
            val sid = key.substringAfterLast('/')
            if (d.queue.isEmpty() || key != "$hostId/$sid" || Mesh.open?.let { it.hostId == hostId && it.sid == sid } == true) continue
            if (working(sid, now) || d.queue.any { it.uuid in Mesh.flying }) continue // (or being sent: its answer waited for)
            val ended = before != null && working(sid, before)
            if (!ended && (d.hold || d.queue.none { it.offline && it.failed == null })) continue // (its turn's ended; or
            // it was written while its host wasn't reached, and it's back)
            val turns = (if (d.upto != null) d.queue.take(d.upto + 1) else d.queue).takeWhile { it.failed == null }
            if (turns.isEmpty()) continue // (one its host said no to first: it waits for you)
            fun tried(q: Queued) = Queued(q.text, q.files, q.uuid ?: java.util.UUID.randomUUID().toString(), offline = true, tried = true)
            val going = if (turns.any { it.offline || it.tried }) turns.map(::tried) // (each by its own uuid: taken once,
                else listOf(tried(turns.singleOrNull() ?: Queued(turns.map { it.text }.filter { it.isNotBlank() } // if it
                    .joinToString("\n\n"), turns.flatMap { it.files })))                                      // was before)
            setDraft(key, Mesh.Draft(d.text, d.files, going + d.queue.drop(turns.size), dirty = d.dirty)) // (kept till its host takes it:
            going.forEach { Mesh.sendQueued(hostId, sid, it) }                            // Queue.taken)
        }
    }

    /** The open chat's draft kept on disk, as it changes. */
    fun save(chat: Chat) {
        val d = draftOf(chat)
        val key = "${chat.hostId}/${chat.sid}"
        io.execute { try { write(key, d) } catch (_: Exception) { } }
    }

    /** A chat left: its draft kept, as its box has it again. */
    fun keepDraft(chat: Chat) = setDraft("${chat.hostId}/${chat.sid}", draftOf(chat))

    /** The draft kept for a chat opened: under its key, or its machine's as a member's. */
    fun takeDraft(hostId: String, sid: String, machine: String?): Mesh.Draft? {
        val keys = listOfNotNull("$hostId/$sid", machine?.let { "mesh:$it/$sid" })
        return keys.mapNotNull { Mesh.drafts.remove(it) }.reduceOrNull { a, b ->
            Mesh.Draft(joined(a.text, b.text), a.files + b.files, a.queue + b.queue, b.upto?.let { a.queue.size + it } ?: a.upto,
                if (b.queue.isEmpty()) a.hold else b.hold, a.dirty || b.dirty) }
    }

    /** (the app started, before any chat opens) What the old unsent folder kept put into each message's chat's draft,
     * on disk; a message's files let go of only once its draft is written (one that can't be read stays). */
    fun migrateUnsent(filesDir: File, hostOf: (machine: String?, host: String) -> String) {
        val root = File(filesDir, "unsent")
        val found = ArrayList<Triple<Long, File, JSONObject>>()
        for (d in root.listFiles().orEmpty()) {
            if (d.name.endsWith(".tmp")) continue
            try {
                val o = JSONObject(File(d, "message.json").readText())
                found.add(Triple(o.optLong("at"), d, o))
            } catch (_: Exception) {
            }
        }
        for ((_, d, o) in found.sortedBy { it.first }) {
            try {
                val a = o.optJSONArray("files")
                val files = (0 until (a?.length() ?: 0)).map { i ->
                    val f = a!!.getJSONObject(i)
                    Picked(f.optString("name"), f.optString("mime"), File(d, "$i").readBytes())
                }
                val key = hostOf(o.optString("machine").ifBlank { null }, o.optString("host")) + "/" + o.optString("session")
                val was = Mesh.drafts[key]
                val now = Mesh.Draft(joined(was?.text.orEmpty(), o.optString("text")), was?.files.orEmpty() + files,
                    was?.queue.orEmpty(), was?.upto, was?.hold == true, dirty = true)
                write(key, now)
                Mesh.drafts[key] = now
                d.deleteRecursively()
            } catch (_: Exception) {
            }
        }
        root.delete() // (only if it's empty now)
    }
}

/** A round flat button on a turn (a queued or held message's): its content, its label for what it does. */
@Composable
fun TurnButton(label: String, onClick: () -> Unit, content: @Composable () -> Unit) {
    Box(Modifier.size(24.dp).clip(CircleShape).clickable(onClick = onClick).semantics(mergeDescendants = true) {
        contentDescription = label
    }, contentAlignment = Alignment.Center) { content() }
}

/** ↑, as the desktop's queued and held turns' button has it. */
@Composable
private fun UpArrow() {
    Text("↑", style = Type.bold.copy(fontSize = 13.sp), color = LocalBaude.current.dim)
}

/** A turn's buttons, hidden till a tap on the turn reveals them; modifier: where they sit in its row (LastLine). */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun Revealed(shown: Boolean, modifier: Modifier, buttons: @Composable () -> Unit) {
    AnimatedVisibility(shown, modifier, enter = fadeIn(tween(150)), exit = fadeOut(tween(150))) {
        Row(horizontalArrangement = Arrangement.spacedBy(2.dp), verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(end = 4.dp)) { buttons() }
    }
}

/** A message queued under the chat while a turn ran, as Baude's queued turn is: grey, right-aligned, its buttons
 * (✕ drops it, ↑ sends it now, revert puts it in the box) level with its last line, shown by a tap (or a long press)
 * and hidden by another tap. While its host isn't reached, a cloud beside it: it goes once it's back. One its host said
 * no to, sent by itself: a warning beside it, saying why; it goes again only by ↑. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun QueuedRow(chat: Chat, q: Queued) {
    val c = LocalBaude.current
    var shown by remember { mutableStateOf(false) }
    val level = rememberLastLine()
    Mesh.tick.value
    val waiting = Mesh.conn(chat.hostId)?.state != "up" || q.failed != null
    Row(Modifier.fillMaxWidth().semantics(mergeDescendants = true) {}
        .combinedClickable(onClick = { shown = !shown }, onLongClick = { shown = true }),
        horizontalArrangement = Arrangement.End) {
        if (waiting && !shown) Icon(if (q.failed != null) Adwaita.DialogWarning else Adwaita.WeatherOvercast,
            q.failed?.let { Wording.HeldSend.notSent(why = it) } ?: Wording.Hosts.waitsFor(host = Mesh.label(chat.hostId)),
            tint = if (q.failed != null) c.warning else c.dim, modifier = Modifier.alignBy(level.buttons).padding(end = 6.dp).size(14.dp.icon))
        Revealed(shown, Modifier.alignBy(level.buttons)) {
            TurnButton(Wording.ChatTurns.queuedDelete, { Queue.drop(chat, q) }) {
                Icon(Adwaita.WindowClose, null, tint = c.dim, modifier = Modifier.size(12.dp.icon))
            }
            TurnButton(Wording.ChatTurns.queuedSendNow, { Queue.sendNow(chat, q) }) { UpArrow() }
            TurnButton(Wording.ChatTurns.queuedEdit, {
                Queue.drop(chat, q)
                chat.edit.value = q.text
                chat.files.addAll(0, q.files)
            }) { Icon(Adwaita.ViewRefresh, null, tint = c.dim, modifier = Modifier.size(12.dp.icon)) }
        }
        Column(Modifier.alignBy(level.bubble).weight(1f, fill = false).padding(start = if (shown) 0.dp else if (waiting) 26.dp else 48.dp)
            .clip(RoundedCornerShape(Gap.bubbleRadius)).background(c.quiet)
            .padding(horizontal = Gap.bubbleH, vertical = Gap.bubbleV), horizontalAlignment = Alignment.End) {
            for (f in q.files) Text(when {
                f.mime.startsWith("image/") -> "🖼 A picture"
                f.mime == "application/pdf" -> "📄 " + f.name
                else -> f.name
            }, style = Type.body, color = c.quietFg, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (q.text.isNotBlank()) Text(q.text, style = Type.body, color = c.quietFg)
        }
    }
}

/** A message its host holds for the chat (a subagent's answer that came after its host restarted), as Baude's held
 * turn shows one: grey, from whom at its top, what a person's shown of it, ✕ to drop it and ↑ to send it to the agent
 * (once no turn's going), their buttons level with its last line, shown by a tap (or a long press) and hidden by
 * another tap. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun Held(chat: Chat, h: Msg.HeldMessage, working: Boolean) {
    val c = LocalBaude.current
    val ctx = LocalContext.current
    var shown by remember { mutableStateOf(false) }
    val level = rememberLastLine()
    Row(Modifier.fillMaxWidth().semantics(mergeDescendants = true) {}
        .combinedClickable(onClick = { shown = !shown }, onLongClick = { shown = true }),
        horizontalArrangement = Arrangement.End) {
        Revealed(shown, Modifier.alignBy(level.buttons)) {
            TurnButton(Wording.Held.delete, { Mesh.held(chat, h.id.orEmpty(), false) }) {
                Icon(Adwaita.WindowClose, null, tint = c.dim, modifier = Modifier.size(12.dp.icon))
            }
            TurnButton(Wording.Held.send, { heldSend(ctx, chat, h, working) }) { UpArrow() }
        }
        Column(Modifier.alignBy(level.bubble).weight(1f, fill = false).clip(RoundedCornerShape(Gap.bubbleRadius)).background(c.quiet)
            .padding(horizontal = Gap.bubbleH, vertical = Gap.bubbleV), horizontalAlignment = Alignment.End) {
            Text(Wording.Common.fromWho(author = h.sender?.let(::senderName) ?: Wording.Senders.hostUnnamed), style = Type.caption,
                color = c.quietFg)
            Prose(SAID_BY.replace(h.shown ?: h.text.orEmpty(), "").trim(), c.quietFg, c.inside)
        }
    }
}

/** A held message's ↑: refused, and why, as a toast, while a turn runs, its host isn't there or its subscription's
 * disabled. */
private fun heldSend(ctx: Context, chat: Chat, h: Msg.HeldMessage, working: Boolean) {
    val why = when {
        working -> Wording.HeldSend.afterTurn
        Mesh.conn(chat.hostId) == null -> Wording.HeldSend.whileNoHost
        Mesh.conn(chat.hostId)?.state != "up" -> Wording.HeldSend.whileDown
        subscriptionOff(chat) -> Wording.HeldSend.whileDisabled
        else -> null
    }
    if (why != null) Toast.makeText(ctx, why, Toast.LENGTH_SHORT).show() else Mesh.held(chat, h.id.orEmpty(), true)
}
