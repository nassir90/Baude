package dev.baude.android

import android.graphics.BitmapFactory
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.compositeOver
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalViewConfiguration
import androidx.compose.ui.unit.dp
import java.util.Base64
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject

private val thumbnails = android.util.LruCache<String, androidx.compose.ui.graphics.ImageBitmap>(64)

/** What a message's revert needs of the chat's page: whether its button's shown, how it's shown, and what it does. */
class RevertOffer(val revealed: Boolean, val reveal: () -> Unit, val onRevert: () -> Unit)

/** A revert asked for and not made: the message it takes back, and what was in the box before. */
class Reverting(val uuid: String, val draft: String) {
    var asked by androidx.compose.runtime.mutableStateOf(false) // (sent, claude's answer not in yet)
}

/** A message of yours as Baude draws it (.user-bubble): a bubble at the right, its files in a row over its text, which
 * is markdown, and the revert button beside it, shown by a long press on the message. */
@Composable
fun UserTurn(line: Line, revert: RevertOffer?) {
    val c = LocalBaude.current
    val slop = LocalViewConfiguration.current.touchSlop
    val hold = LocalViewConfiguration.current.longPressTimeoutMillis
    Row(Modifier.fillMaxWidth().then(if (revert == null) Modifier else Modifier.pointerInput(revert.reveal) {
        awaitEachGesture { // (watched, not taken: a long press on the text still starts its selection)
            val down = awaitFirstDown(requireUnconsumed = false, pass = PointerEventPass.Initial)
            var ended = false
            val held = withTimeoutOrNull(hold) {
                while (!ended) {
                    val e = awaitPointerEvent(PointerEventPass.Initial)
                    ended = e.changes.all { !it.pressed } || e.changes.any { (it.position - down.position).getDistance() > slop }
                }
            } == null
            if (held) revert.reveal()
        }
    }), horizontalArrangement = Arrangement.End, verticalAlignment = Alignment.Bottom) {
        RevertButton(revert)
        Spacer(Modifier.width(6.dp))
        Column(Modifier.weight(1f, fill = false).clip(RoundedCornerShape(Gap.bubbleRadius)).background(c.bubble)
            .padding(horizontal = Gap.bubbleH, vertical = Gap.bubbleV), horizontalAlignment = Alignment.End,
            verticalArrangement = Arrangement.spacedBy(6.dp)) {
            if (line.files.isNotEmpty()) Attachments(line.files)
            if (line.text.isNotBlank()) SelectionContainer {
                MarkdownView(line.text, under = c.bubble.compositeOver(c.inside))
            }
        }
    }
}

/** The revert button (.revert-turn): a round flat button, faded in when the message's long-pressed; its slot's kept. */
@Composable
private fun RevertButton(revert: RevertOffer?) {
    val c = LocalBaude.current
    val shown = revert?.revealed == true
    val alpha = animateFloatAsState(if (shown) 1f else 0f, tween(150, easing = FastOutSlowInEasing), label = "revert").value
    Box(Modifier.padding(bottom = 6.5.dp).size(24.dp).alpha(alpha).clip(CircleShape)
        .then(if (shown) Modifier.clickable { revert?.onRevert?.invoke() } else Modifier), contentAlignment = Alignment.Center) {
        Icon(Adwaita.ViewRefresh, Wording.ChatTurns.revert, tint = c.fg.copy(alpha = c.fg.alpha * 0.6f), modifier = Modifier.size(12.dp))
    }
}

/** A message's pictures and files in a row (.attach-thumb, .attach-chip): a picture a 96 square, cropped; a file a chip. */
@Composable
private fun Attachments(files: List<Sent>) {
    val c = LocalBaude.current
    val density = LocalDensity.current
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        for (f in files) {
            if (f.kind == "image") {
                val bitmap = androidx.compose.runtime.produceState<androidx.compose.ui.graphics.ImageBitmap?>(
                    thumbnails.get(f.data.orEmpty()), f.data) {
                    if (value != null) return@produceState
                    value = withContext(kotlinx.coroutines.Dispatchers.Default) { try {
                        val bytes = Base64.getDecoder().decode(f.data.orEmpty())
                        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
                        var sample = 1
                        val want = (Tokens.Markdown.thumb.value * density.density).toInt()
                        while (minOf(bounds.outWidth, bounds.outHeight) / (sample * 2) >= want) sample *= 2
                        BitmapFactory.decodeByteArray(bytes, 0, bytes.size,
                            BitmapFactory.Options().apply { inSampleSize = sample })?.asImageBitmap()
                            ?.also { thumbnails.put(f.data.orEmpty(), it) }
                    } catch (_: Exception) {
                        null
                    } }
                }.value
                if (bitmap != null) Image(bitmap, f.name, contentScale = ContentScale.Crop,
                    modifier = Modifier.size(Tokens.Markdown.thumb).clip(RoundedCornerShape(6.dp)))
            } else Row(Modifier.clip(RoundedCornerShape(Gap.cardRadius)).background(c.raised)
                .padding(start = 4.dp, top = 2.dp, end = 2.dp, bottom = 2.dp), verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                Icon(if (f.mime == "application/pdf") Adwaita.XOfficeDocument else Adwaita.TextXGeneric, null, tint = c.fg,
                    modifier = Modifier.size(16.dp))
                Text(if (f.name.length > 22) f.name.take(10) + "…" + f.name.takeLast(11) else f.name, style = Type.body, color = c.fg,
                    maxLines = 1)
            }
        }
    }
}

/** A revert made (the box's text and files go once claude has rewound to before the message): rewind_conversation to
 * its host's claude, interrupting a turn that's running; once it says it has, the message and all after it are
 * gone from the chat and what's written is sent. Why not, if it won't (done(false); the revert stays asked). */
fun commitRevert(chat: Chat, r: Reverting, text: String, files: List<Picked>, done: (Boolean) -> Unit) {
    val uuid = r.uuid
    r.asked = true
    val request = JSONObject().put("subtype", "rewind_conversation").put("target_message_uuid", uuid)
        .put("interrupt_if_running", true)
    controlAnswer(chat.hostId, chat.sid, request) { response, why ->
        if (chat.reverting !== r) return@controlAnswer // (no longer pending: its answer does nothing)
        r.asked = false
        if (why != null || response?.optBoolean("rewound") != true) {
            val other = response?.keys()?.asSequence()?.filter { it != "rewound" }?.mapNotNull { response.opt(it) as? String }?.firstOrNull()
            chat.said.value = Wording.ChatTurns.couldntRevert(why = why ?: other ?: Wording.ChatHeader.claudeSaidNo)
            done(false)
            return@controlAnswer
        }
        val i = chat.lines.indexOfFirst { it.kind == "you" && it.uuid == uuid }
        if (i >= 0) {
            while (chat.lines.size > i) chat.lines.removeAt(chat.lines.size - 1)
            chat.ends.values.removeAll { it > i }
        }
        chat.reverting = null
        chat.files.clear()
        done(true)
        var waited = 0
        fun whenIdle() {
            if (Mesh.session(chat.hostId, chat.sid)?.state != "working" || waited >= 3000) chat.send(text, files)
            else {
                waited += 250
                Mesh.later(250) { whenIdle() }
            }
        }
        whenIdle()
    }
}
