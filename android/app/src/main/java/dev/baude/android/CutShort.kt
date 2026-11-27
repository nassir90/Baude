package dev.baude.android

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp

/** Chats whose Continue was pressed and not yet sent (BaudeWindow.cut_continuing), by "host/session". */
object CutShort {
    val continuing = mutableStateMapOf<String, Boolean>()
}

/** The line at a chat's end that says a restart cut its last turn short (BaudeWindow.show_cut), and its Continue,
 * which tells the chat to carry on. What's typed and attached stays in the box. */
@Composable
fun CutShortLine(chat: Chat) {
    val c = LocalBaude.current
    val at = chat.hostId
    val key = "$at/${chat.sid}"
    val cut = Mesh.session(at, chat.sid)?.cut
    LaunchedEffect(cut) { if (cut == null) CutShort.continuing.remove(key) }
    if (cut == null || chat.sender != null) return
    val going = CutShort.continuing[key] == true
    Row(Modifier.padding(top = 2.dp), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
        if (going) {
            Spinner()
            Text(Wording.CutShort.continuing, style = Type.body, color = c.dim)
            return@Row
        }
        Text(Wording.CutShort.line, style = Type.body, color = c.dim)
        Box(Modifier.clip(RoundedCornerShape(6.dp)).background(c.fg.copy(alpha = 0.10f))
            .semantics { contentDescription = Wording.CutShort.tip(message = Wording.CutShort.message) }
            .clickable {
                val gone = Mesh.conn(at)?.state != "up" || Stopping.line(at) != null
                if (gone || chat.chrome.missing.value || exhausted(chat) || subscriptionOff(chat)) return@clickable // (what the box couldn't send now)
                CutShort.continuing[key] = true
                Mesh.send(chat, Wording.CutShort.message, emptyList(), keep = false) { CutShort.continuing.remove(key) }
            }.padding(horizontal = 8.dp, vertical = 1.dp)) {
            Text(Wording.Common.`continue`, style = Type.body.copy(fontSize = Tokens.Font.body * 0.9f,
                lineHeight = Tokens.Font.bodyLine * 0.9f), color = c.fg)
        }
    }
}
