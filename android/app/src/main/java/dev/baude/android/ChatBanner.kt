package dev.baude.android

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay

/** What's in the way of a chat from outside it, said in a strip under its header while it is (Adw.Banner): its host
 * stopping or not there, its host not having the chat any more, claude retrying the model's API. Nothing, and no
 * room, when nothing is. */
@Composable
fun ChatBanner(chat: Chat, open: (String, String) -> Unit) {
    val c = LocalBaude.current
    Mesh.tick.value
    val at = chat.hostId
    var expired by remember { mutableStateOf(0) } // (a host's words as it stopped, said for a minute: drawn again then)
    val said = Stopping.said[at]
    LaunchedEffect(said) {
        if (said != null) {
            delay(Stopping.left(at))
            expired++
        }
    }
    expired
    val name = Mesh.label(at)
    val conn = Mesh.conn(at)
    val retry = chat.chrome.retrying.value
    val stopping = Stopping.line(at)
    val dots = if (stopping == null && conn?.state == "up" && !chat.chrome.missing.value && retry != null) dotStep() else 0
    var button: Pair<String, () -> Unit>? = null
    val title: AnnotatedString? = when {
        stopping != null -> AnnotatedString(stopping)
        conn == null || conn.state != "up" -> AnnotatedString(if (conn?.state == "connecting") Wording.Hosts.looking(host = name)
            else conn?.why?.let { Wording.Hosts.downBecause(host = name, why = it) } ?: Wording.Hosts.down(host = name))
        chat.chrome.missing.value -> {
            button = Wording.ChatBanner.newChat to { newChatOn(chat, open) }
            AnnotatedString(Wording.ChatBanner.missing(name = name))
        }
        retry != null -> {
            val status = retry.opt("error_status")?.takeIf { !retry.isNull("error_status") && it.toString().isNotBlank() }
            val what = if (status != null) Wording.ChatBanner.apiStatus(status = status) else Wording.ChatBanner.apiSilent
            val attempt = retry.optInt("attempt", 0)
            val max = retry.optInt("max_retries", 0)
            val line = if (attempt > 0 && max > 0) Wording.ChatBanner.retryingAttempt(what = what, attempt = attempt, max = max)
                else Wording.ChatBanner.retrying(what = what)
            buildAnnotatedString {
                append(line)
                append(dotsLit(".", dots))
            }
        }
        else -> null
    }
    if (title == null) return
    Row(Modifier.fillMaxWidth().background(lerp(c.window, c.accentBg, 0.3f)).padding(horizontal = 12.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(title, style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis, textAlign = TextAlign.Center,
            modifier = Modifier.weight(1f))
        button?.let { (label, go) -> Button(label, onClick = go) }
    }
}
