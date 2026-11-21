package dev.baude.android

import android.content.Context
import android.os.Handler
import android.os.Looper
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
class Sent(val id: String, val lines: List<Line>, val text: String, val files: List<Picked>)

/** A message written while a turn runs, queued under the chat. */
class Queued(val text: String, val files: List<Picked>)

object Queue {
    private val main = Handler(Looper.getMainLooper())
    private val ALL = Queued("", emptyList())

    /** What's written, sent as the desktop's send does: at once, or, while a turn runs, queued under the chat. */
    fun submit(chat: Chat, working: Boolean, text: String, files: List<Picked>) {
        if (chat.sender == null && working) chat.queued.add(Queued(text.trim(), files)) else chat.send(text, files)
    }

    /** The queue sent as one message when a turn ends by itself (up to the one ↑ was pressed on, if one was); kept
     * when it was stopped. */
    fun flush(chat: Chat) {
        val upto = chat.queueUpto ?: (if (chat.stopAsked) null else ALL)
        chat.stopAsked = false
        chat.queueUpto = null
        if (upto == null || chat.queued.isEmpty()) return
        val turns = if (upto === ALL) chat.queued.toList() else chat.queued.take(chat.queued.indexOf(upto) + 1)
        chat.queued.removeAll(turns.toSet())
        val text = turns.map { it.text }.filter { it.isNotBlank() }.joinToString("\n\n")
        val files = turns.flatMap { it.files }
        if (Mesh.conn(chat.hostId)?.state == "up") chat.send(text, files)
        else {
            chat.edit.value = text
            chat.files.addAll(0, files)
        }
    }

    /** ↑ on a queued message: it and those before it sent together, now, or once the interrupted turn has ended. */
    fun sendNow(chat: Chat, q: Queued, working: Boolean) {
        if (working) {
            val now = chat.queueUpto
            if (now == null || chat.queued.indexOf(q) > chat.queued.indexOf(now)) chat.queueUpto = q
            interrupt(chat)
        } else {
            chat.queueUpto = q
            flush(chat)
        }
    }

    /** The running turn interrupted. */
    fun interrupt(chat: Chat) {
        chat.stopAsked = true
        val msg = JSONObject().put("type", "control_request").put("request_id", "baude_" + System.nanoTime())
            .put("request", JSONObject().put("subtype", "interrupt"))
        Mesh.conn(chat.hostId)?.send(Msg.Input(session = chat.sid, msg = msg).json())
    }

    /** A chat left: what's queued in it and written in its box kept, as its box has it again. */
    fun keepDraft(chat: Chat) {
        val key = "${chat.hostId}/${chat.sid}"
        val text = (chat.queued.map { it.text } + chat.typed).filter { it.isNotBlank() }.joinToString("\n\n")
        val files = chat.queued.flatMap { it.files } + chat.files
        if (text.isNotEmpty() || files.isNotEmpty()) Mesh.drafts[key] = Mesh.Draft(text, files) else Mesh.drafts.remove(key)
    }

    /** (off the main thread) What the app kept for a host in its unsent folder, once, put in its chat's box; the
     * folder removed. */
    fun restoreUnsent(filesDir: File) {
        val root = File(filesDir, "unsent")
        val found = ArrayList<Triple<Long, String, Mesh.Draft>>()
        for (d in root.listFiles().orEmpty()) {
            try {
                if (d.name.endsWith(".tmp")) continue
                val o = JSONObject(File(d, "message.json").readText())
                val a = o.optJSONArray("files")
                val files = (0 until (a?.length() ?: 0)).mapNotNull { i ->
                    val f = a!!.getJSONObject(i)
                    File(d, "$i").takeIf { it.exists() }?.let { Picked(f.optString("name"), f.optString("mime"), it.readBytes()) }
                }
                found.add(Triple(o.optLong("at"), o.optString("host") + "/" + o.optString("session"),
                    Mesh.Draft(o.optString("text"), files)))
            } catch (_: Exception) {
            }
        }
        root.deleteRecursively()
        if (found.isEmpty()) return
        main.post {
            for ((_, key, d) in found.sortedBy { it.first }) {
                val was = Mesh.drafts[key]
                Mesh.drafts[key] = if (was == null) d else Mesh.Draft(
                    listOf(was.text, d.text).filter { it.isNotBlank() }.joinToString("\n\n"), was.files + d.files)
            }
        }
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

/** A turn's buttons, hidden till a long-press on the turn reveals them. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun Revealed(shown: Boolean, buttons: @Composable () -> Unit) {
    AnimatedVisibility(shown, enter = fadeIn(tween(150)), exit = fadeOut(tween(150))) {
        Row(horizontalArrangement = Arrangement.spacedBy(2.dp), verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(end = 4.dp)) { buttons() }
    }
}

/** A message queued under the chat while a turn ran, as Baude's queued turn is: grey, right-aligned, its buttons
 * (✕ drops it, ↑ sends it now, revert puts it in the box) shown on a long-press. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun QueuedRow(chat: Chat, q: Queued, working: Boolean) {
    val c = LocalBaude.current
    var shown by remember { mutableStateOf(false) }
    Row(Modifier.fillMaxWidth().combinedClickable(onClick = { shown = false }, onLongClick = { shown = true }),
        horizontalArrangement = Arrangement.End, verticalAlignment = Alignment.Bottom) {
        Revealed(shown) {
            TurnButton(Wording.ChatTurns.queuedDelete, { chat.queued.remove(q) }) {
                Icon(Adwaita.WindowClose, null, tint = c.dim, modifier = Modifier.size(12.dp))
            }
            TurnButton(Wording.ChatTurns.queuedSendNow, { Queue.sendNow(chat, q, working) }) { UpArrow() }
            TurnButton(Wording.ChatTurns.queuedEdit, {
                chat.queued.remove(q)
                chat.edit.value = q.text
                chat.files.addAll(0, q.files)
            }) { Icon(Adwaita.ViewRefresh, null, tint = c.dim, modifier = Modifier.size(12.dp)) }
        }
        Column(Modifier.weight(1f, fill = false).padding(start = if (shown) 0.dp else 48.dp)
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
 * (once no turn's going), their buttons shown on a long-press. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun Held(chat: Chat, h: Msg.HeldMessage, working: Boolean) {
    val c = LocalBaude.current
    val ctx = LocalContext.current
    var shown by remember { mutableStateOf(false) }
    Row(Modifier.fillMaxWidth().combinedClickable(onClick = { shown = false }, onLongClick = { shown = true }),
        horizontalArrangement = Arrangement.End, verticalAlignment = Alignment.Bottom) {
        Revealed(shown) {
            TurnButton(Wording.Held.delete, { Mesh.held(chat, h.id.orEmpty(), false) }) {
                Icon(Adwaita.WindowClose, null, tint = c.dim, modifier = Modifier.size(12.dp))
            }
            TurnButton(Wording.Held.send, { heldSend(ctx, chat, h, working) }) { UpArrow() }
        }
        Column(Modifier.weight(1f, fill = false).clip(RoundedCornerShape(Gap.bubbleRadius)).background(c.quiet)
            .padding(horizontal = Gap.bubbleH, vertical = Gap.bubbleV), horizontalAlignment = Alignment.End) {
            Text(Wording.Common.fromWho(author = h.sender?.let(::senderName) ?: Wording.Senders.hostUnnamed), style = Type.caption,
                color = c.quietFg)
            Prose(SAID_BY.replace(h.shown ?: h.text.orEmpty(), "").trim(), c.quietFg, c.inside)
        }
    }
}

/** A held message's ↑: refused, and why, as a toast, while a turn runs or its host isn't there. */
private fun heldSend(ctx: Context, chat: Chat, h: Msg.HeldMessage, working: Boolean) {
    val why = when {
        working -> Wording.HeldSend.afterTurn
        Mesh.conn(chat.hostId) == null -> Wording.HeldSend.whileNoHost
        Mesh.conn(chat.hostId)?.state != "up" -> Wording.HeldSend.whileDown
        else -> null
    }
    if (why != null) Toast.makeText(ctx, why, Toast.LENGTH_SHORT).show() else Mesh.held(chat, h.id.orEmpty(), true)
}
