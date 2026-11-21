package dev.baude.android

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp

// ------------------------------------------------------------------------------------------------------ a chat --

/** A chat's page: what it and you have said, what it did, and a box to write in (files too: pictures and PDFs as
 * Baude sends them, text files in the message). name: what the box asks you to message. */
@Composable
fun ChatPage(chat: Chat, name: String, hint: String = "Message $name", attach: Boolean = true) {
    val c = LocalBaude.current
    val ctx = LocalContext.current
    val sid = chat.sid
    val at = chat.hostId // (where it's reached now: a member's chat moves to a host of yours that's its machine)
    val session = Mesh.session(at, sid)
    val working = session?.state == "working" || chat.thinking.value
    var text by androidx.compose.runtime.saveable.rememberSaveable(at, sid) { mutableStateOf("") }
    val files = chat.files
    val pick = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        take(ctx, chat, uris)
    }
    LaunchedEffect(chat, chat.draft.value) { // (shared from another app: after what's written)
        val shared = chat.draft.value ?: return@LaunchedEffect
        text = if (text.isBlank()) shared else text.trimEnd() + "\n" + shared
        chat.draft.value = null
    }
    val list = rememberLazyListState()
    // (kept at the end while it's there: what it said last, or what it asks, a tall one's foot too, what it's writing
    // as it grows; scrolled up to read, left where it is)
    var follow by remember(chat) { mutableStateOf(true) }
    LaunchedEffect(list.isScrollInProgress) { if (!list.isScrollInProgress) follow = !list.canScrollForward }
    LaunchedEffect(chat.lines.size, chat.asks.size, chat.streaming.value.length / 120) {
        val n = list.layoutInfo.totalItemsCount
        if (n > 0 && follow) {
            list.animateScrollToItem(n - 1)
            list.scrollBy(1e6f)
        }
    }
    Mesh.tick.value
    Column(Modifier.fillMaxSize().inside(c)) {
        LazyColumn(Modifier.weight(1f).fillMaxWidth(), state = list,
            contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = Gap.chatH, vertical = Gap.chatV),
            verticalArrangement = Arrangement.spacedBy(Gap.messageGap)) {
            items(chat.lines) { line -> Said(line, line.uuid?.let { chat.senders[it] }.takeIf { line.kind == "you" }) }
            if (chat.streaming.value.isNotEmpty()) item { SelectionContainer { Prose(chat.streaming.value) } }
            items(chat.asks, key = { it.rid }) { ask -> // (this phone's own agents: as claude's)
                Asked(chat, ask, alwaysOffered(session?.let { it.model.orEmpty() }, at),
                    if (session?.bot != null) name else "Claude") // (a Bot's question: its name's)
            }
            val held = session?.held.orEmpty().filter { it.id.orEmpty() !in chat.holding }
            items(held, key = { "held:" + it.id.orEmpty() }) { h -> Held(chat, h, working) }
            items(Pending.of(sid), key = { "unsent:" + it.id }) { Waiting(it, Mesh.conn(Mesh.reach(it))?.state == "up") }
            if (working && chat.asks.isEmpty()) item {
                Text("Working…", style = Type.caption, color = c.dim)
            }
        }
        chat.said.value?.let { Text(it, style = Type.caption, color = c.dim, modifier = Modifier.padding(horizontal = Gap.chatH)) }
        // the composer: rounded 12, a ring of the rule, its files over what's written, + and send beside it
        Column(
            Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 8.dp)
                .clip(RoundedCornerShape(Gap.composerRadius)).background(c.inside)
                .border(1.dp, c.rule, RoundedCornerShape(Gap.composerRadius)).padding(6.dp),
        ) {
            if (files.isNotEmpty()) Row(Modifier.padding(bottom = 6.dp).horizontalScroll(rememberScrollState()),
                horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                for (f in files.toList()) Row(
                    Modifier.clip(RoundedCornerShape(8.dp)).background(c.raised).padding(start = 8.dp, end = 2.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(f.name, style = Type.caption, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.widthIn(max = 140.dp))
                    IconButton(Adwaita.WindowClose, Wording.Common.remove) { files.remove(f) }
                }
            }
            Row(verticalAlignment = Alignment.Bottom) {
                if (attach) IconButton(Adwaita.ListAdd, "Attach images, PDFs or files") { pick.launch(arrayOf("*/*")) }
                Box(Modifier.weight(1f).padding(horizontal = 6.dp, vertical = 7.dp)) {
                    if (text.isEmpty()) Text(hint, style = Type.body, color = c.dim)
                    BasicTextField(text, { text = it }, textStyle = Type.body.copy(color = c.fg),
                        cursorBrush = SolidColor(c.accentBg), modifier = Modifier.fillMaxWidth().heightIn(max = 160.dp))
                }
                val ready = (text.isNotBlank() || files.isNotEmpty()) && !chat.busy.value && chat.preparing.value == 0
                IconButton(Adwaita.Return, Wording.Common.send, enabled = ready, tint = if (ready) c.accentFg else c.fg,
                    background = if (ready) c.accentBg else null) {
                    chat.send(text, files.toList())
                    text = ""
                    files.clear()
                }
            }
        }
    }
}

/** Files picked (or shared from another app) attached to the chat's message: read, and pictures made small, off the
 * main thread; why one can't be, said. */
fun take(ctx: Context, chat: Chat, uris: List<Uri>) {
    val main = android.os.Handler(android.os.Looper.getMainLooper())
    chat.preparing.value += uris.size
    kotlin.concurrent.thread(name = "pick", isDaemon = true) { // (read, and pictures made small, off the main thread)
        for (uri in uris) {
            val (picked, why) = try {
                read(ctx, uri)
            } catch (e: Throwable) { // (too big to hold, say: said, not a crash)
                null to "That file couldn't be read"
            }
            main.post {
                chat.preparing.value -= 1
                if (picked != null) chat.files.add(picked)
                if (why != null) Toast.makeText(ctx, why, Toast.LENGTH_SHORT).show()
            }
        }
    }
}

/** What a Bot asks to do, as Baude's permission card has it: Allow, Always allow (for Claude Code's models), Deny;
 * claude's own questions (AskUserQuestion) as a form, answered or skipped. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun Asked(chat: Chat, ask: Ask, alwaysOffered: Boolean, name: String = "Claude") {
    val c = LocalBaude.current
    val questions = ask.input.optJSONArray("questions")
    Column(Modifier.fillMaxWidth().card(c), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (ask.tool == "AskUserQuestion" && questions != null) {
            Text(Wording.Asks.hasQuestion(name = name), style = Type.bold, color = c.fg)
            Questions(questions, answer = { Mesh.answer(chat, ask, allow = true, answers = it) },
                skip = { Mesh.answer(chat, ask, allow = false) })
            return@Column
        }
        Text(Wording.Asks.allowTool(tool = toolTitle(ask.tool)), style = Type.bold, color = c.fg)
        val shown = shownInput(ask.input)
        if (shown.isNotBlank()) Text(shown, style = Type.mono, color = c.fg, maxLines = 14, overflow = TextOverflow.Ellipsis,
            modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(Gap.codeRadius)).background(c.raised)
                .padding(horizontal = 8.dp, vertical = 6.dp))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Button(Wording.Common.allow, suggested = true) { Mesh.answer(chat, ask, allow = true) }
            // (a plan's agreed to each time)
            if (alwaysOffered && ask.tool != "ExitPlanMode") Button(Wording.Asks.alwaysAllow(tool = toolDisplay(ask.tool).first)) {
                Mesh.answer(chat, ask, allow = true, always = true)
            }
            Button(Wording.Common.deny, destructive = true) { Mesh.answer(chat, ask, allow = false) }
        }
        val always = Mesh.alwaysAllowed(chat)
        if (always.isNotEmpty()) Text("Always allowed in this chat: " + always.sorted().joinToString(", ") {
            toolDisplay(it).first }, style = Type.caption, color = c.dim)
    }
}

/** A call's input as the card shows it: its command, or its fields one a line. */
private fun shownInput(input: org.json.JSONObject): String {
    input.optString("command").ifBlank { null }?.let { return it }
    return input.keys().asSequence().joinToString("\n") { k ->
        val v = input.opt(k)
        "$k: " + (if (v is String) v else v.toString()).lines().take(6).joinToString("\n  ").take(400)
    }
}

/** claude's questions (AskUserQuestion), as Baude's QuestionForm: each its options (one picked, or several if it
 * says so) and Other, to write your own. Answer sends {question: what's picked, comma-joined}; Skip, none. */
@Composable
private fun Questions(questions: org.json.JSONArray, answer: (org.json.JSONObject) -> Unit, skip: () -> Unit) {
    val c = LocalBaude.current
    val qs = (0 until questions.length()).mapNotNull { questions.optJSONObject(it) }.filter { it.optString("question").isNotBlank() }
    val picked = remember { mutableStateListOf<Set<String>>().apply { repeat(qs.size) { add(emptySet()) } } }
    val other = remember { mutableStateListOf<String?>().apply { repeat(qs.size) { add(null) } } }
    fun answers() = org.json.JSONObject().apply {
        qs.forEachIndexed { i, q ->
            val all = picked[i].toList() + listOfNotNull(other[i]?.trim()?.ifBlank { null })
            put(q.optString("question"), all.joinToString(", "))
        }
    }
    qs.forEachIndexed { i, q ->
        val multi = q.optBoolean("multiSelect")
        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
            val head = q.optString("header")
            if (head.isNotBlank()) Text(head, style = Type.caption, color = c.dim)
            Text(q.optString("question"), style = Type.bold, color = c.fg)
            val options = q.optJSONArray("options")
            for (j in 0 until (options?.length() ?: 0)) {
                val o = options!!.opt(j)
                val label = (o as? org.json.JSONObject)?.optString("label") ?: o.toString()
                val about = (o as? org.json.JSONObject)?.optString("description").orEmpty()
                val on = label in picked[i]
                Choice(on, multi, label, about) {
                    picked[i] = if (multi) (if (on) picked[i] - label else picked[i] + label) else setOf(label)
                    if (!multi) other[i] = null
                }
            }
            Choice(other[i] != null, multi, Wording.Common.other, "") {
                other[i] = if (other[i] != null) null else ""
                if (!multi && other[i] != null) picked[i] = emptySet()
            }
            other[i]?.let { Field(it, { v -> other[i] = v }, Wording.Asks.ownAnswer) }
        }
    }
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        val ready = qs.indices.all { picked[it].isNotEmpty() || !other[it].isNullOrBlank() }
        Button(Wording.Asks.answer, suggested = ready) { if (ready) answer(answers()) }
        Button(Wording.Asks.skip, onClick = skip)
    }
}

@Composable
private fun Choice(on: Boolean, multi: Boolean, label: String, about: String, toggle: () -> Unit) {
    val c = LocalBaude.current
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(6.dp)).clickable(onClick = toggle).padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(18.dp).clip(if (multi) RoundedCornerShape(4.dp) else androidx.compose.foundation.shape.CircleShape)
            .background(if (on) c.accentBg else c.inside)
            .border(1.5.dp, if (on) c.accentBg else c.dim, if (multi) RoundedCornerShape(4.dp)
                else androidx.compose.foundation.shape.CircleShape),
            contentAlignment = Alignment.Center) {
            if (on && multi) androidx.compose.material3.Icon(Adwaita.ObjectSelect, null, tint = c.accentFg,
                modifier = Modifier.size(14.dp))
            else if (on) Box(Modifier.size(6.dp).clip(androidx.compose.foundation.shape.CircleShape).background(c.accentFg))
        }
        Spacer(Modifier.width(8.dp))
        Column {
            Text(label, style = Type.body, color = c.fg)
            if (about.isNotBlank()) Text(about, style = Type.caption, color = c.dim)
        }
    }
}

/** Who a message is from, as its bubble's head says it (wire.sender_name): "Ada, a Bot on lab-mac". */
fun senderName(s: Msg.Sender): String = senderName(s.kind, s.name, s.hostName)

/** The same of a sender as its host's sender store keeps it. */
fun senderName(s: Msg.StoredSender): String = senderName(s.kind, s.name, s.hostName)

private fun senderName(kind: String?, name0: String?, hostName: String?): String {
    val name = name0.orEmpty()
    val who = when (kind.orEmpty()) {
        "bot" -> if (name.isBlank()) Wording.Senders.botUnnamed else Wording.Senders.bot(name = name)
        "chat" -> if (name.isBlank()) Wording.Senders.chatUnnamed else Wording.Senders.chat(name = name)
        "subagent" -> if (name.isBlank()) Wording.Senders.subagentUnnamed else Wording.Senders.subagent(name = name)
        "host" -> name.ifBlank { Wording.Senders.hostUnnamed }
        else -> name.ifBlank { Wording.Senders.another } // (a kind of a newer host's: by its name)
    }
    val where = hostName.orEmpty()
    return if (where.isNotBlank() && kind.orEmpty() != "host") Wording.Senders.on(who = who, where = where) else who
}

/** A message's text without the line its host put at its head for its agent (wire.said_by), for one its sender
 * store says is another's. */
private val SAID_BY = Regex("""\A<baude-sender(?: [a-z_]+="[^"<>]*")*>[^<]*</baude-sender>(?:\n\n|\n|\z)""")

/** A line of the chat: from (a message of yours) who it's from instead, if its host's sender store says it's
 * another's: where yours are, grey, as Baude draws it, with "from <who>" small at its top, and what the store says is
 * shown of it in place of its text if it says (shown: a held answer sent, drawn as the held one was). */
@Composable
fun Said(line: Line, from: Msg.StoredSender? = null) {
    val c = LocalBaude.current
    when {
        from != null -> Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
            Column(Modifier.padding(start = 48.dp).clip(RoundedCornerShape(Gap.bubbleRadius)).background(c.raised)
                .padding(horizontal = Gap.bubbleH, vertical = Gap.bubbleV), horizontalAlignment = Alignment.End) {
                Text(Wording.Common.fromWho(author = senderName(from)), style = Type.caption, color = c.dim)
                SelectionContainer {
                    val shown = from.shown
                    if (shown != null) Prose(SAID_BY.replace(shown, "").trim(), codeBg = c.inside)
                    else Text(SAID_BY.replace(line.text, "").trim(), style = Type.body, color = c.fg)
                }
            }
        }
        else -> SaidLine(line)
    }
}

/** A message its host holds for the chat (a subagent's answer after its host restarted), as Baude's queue box shows
 * one: grey, from whom at its top, what a person's shown of it (shown: its answer, as an answer's drawn, or what
 * became of it; else what it says), ✕ to drop it and ↑ to send it to the agent (once no turn's going). */
@Composable
private fun Held(chat: Chat, h: Msg.HeldMessage, working: Boolean) {
    val c = LocalBaude.current
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End, verticalAlignment = Alignment.Bottom) {
        IconButton(Adwaita.WindowClose, Wording.Held.delete) { Mesh.held(chat, h.id.orEmpty(), false) }
        IconButton(Adwaita.Return, if (working) Wording.HeldSend.afterTurn else Wording.Held.send,
            enabled = !working) { Mesh.held(chat, h.id.orEmpty(), true) }
        Column(Modifier.weight(1f, fill = false).clip(RoundedCornerShape(Gap.bubbleRadius)).background(c.raised)
            .padding(horizontal = Gap.bubbleH, vertical = Gap.bubbleV), horizontalAlignment = Alignment.End) {
            Text(Wording.Common.fromWho(author = h.sender?.let(::senderName) ?: Wording.Senders.hostUnnamed), style = Type.caption, color = c.dim)
            Prose(SAID_BY.replace(h.shown ?: h.text.orEmpty(), "").trim(), c.dim, c.inside)
        }
    }
}

/** A message you sent that its host doesn't have yet (Pending), as yours is drawn but faint, how it stands under it:
 * being sent, waiting for its host, or not sent (why), with ✕ to drop it and ↑ to put it in line again. */
@Composable
private fun Waiting(u: Unsent, up: Boolean) {
    val c = LocalBaude.current
    val failed = u.state.value == "failed"
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End, verticalAlignment = Alignment.Bottom) {
        if (failed) {
            IconButton(Adwaita.WindowClose, "Delete this message") { Mesh.dropUnsent(u) }
            IconButton(Adwaita.Return, "Send it again") { Mesh.again(u) }
        }
        Column(Modifier.weight(1f, fill = false).padding(start = if (failed) 0.dp else 48.dp), horizontalAlignment = Alignment.End) {
            Column(Modifier.clip(RoundedCornerShape(Gap.bubbleRadius)).background(c.bubble.copy(alpha = c.bubble.alpha * 0.55f))
                .padding(horizontal = Gap.bubbleH, vertical = Gap.bubbleV), horizontalAlignment = Alignment.End) {
                for (f in u.files) Text((if (f.mime.startsWith("image/")) "🖼 " else "📄 ") + f.name, style = Type.body,
                    color = c.dim, maxLines = 1, overflow = TextOverflow.Ellipsis)
                if (u.text.isNotBlank()) Text(u.text, style = Type.body, color = c.dim)
            }
            Text(when {
                failed -> Wording.HeldSend.notSent(why = u.why.value ?: Wording.BotBrowser.hostSaidNo)
                u.state.value == "sending" || up -> "Sending…"
                else -> "It's sent once this chat's host is there"
            }, style = Type.caption, color = if (failed) c.destructive else c.dim, modifier = Modifier.padding(top = 2.dp, end = 4.dp))
        }
    }
}

@Composable
private fun SaidLine(line: Line) {
    val c = LocalBaude.current
    when (line.kind) {
        "you" -> Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
            SelectionContainer {
                Text(line.text, style = Type.body, color = c.fg, modifier = Modifier.padding(start = 48.dp)
                    .clip(RoundedCornerShape(Gap.bubbleRadius)).background(c.bubble)
                    .padding(horizontal = Gap.bubbleH, vertical = Gap.bubbleV))
            }
        }
        "tool" -> Text(line.text, style = Type.caption, color = c.dim, maxLines = 2, overflow = TextOverflow.Ellipsis,
            modifier = Modifier.fillMaxWidth().card(c))
        "rows" -> MetaRows(line.text)
        "action" -> MetaAction(line.text)
        "note", "error" -> Text(line.text, style = Type.caption, color = c.dim, textAlign = TextAlign.Center,
            modifier = Modifier.fillMaxWidth())  // (.sysnote: 0.85em, faded)
        else -> SelectionContainer { Prose(line.text) }
    }
}

/** What a Bot said: its text, and its code (``` fenced) in Baude's code blocks (codeBg: theirs and inline code's fill,
 * the view's on a raised bubble, where the raised fill wouldn't show). */
@Composable
fun Prose(text: String, color: Color = LocalBaude.current.fg, codeBg: Color = LocalBaude.current.raised) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        text.split("```").forEachIndexed { i, part ->
            if (i % 2 == 1) {
                val code = part.substringAfter("\n", part).trimEnd()
                Text(code, style = Type.mono, color = color, modifier = Modifier.fillMaxWidth()
                    .clip(RoundedCornerShape(Gap.codeRadius)).background(codeBg).padding(horizontal = 8.dp, vertical = 6.dp))
            } else if (part.isNotBlank()) {
                for (block in part.trim().split(Regex("\n\\s*\n"))) Markdown(block, color, codeBg)
            }
        }
    }
}

/** A paragraph of Markdown as Baude's chat draws it: headings (.md-h1 1.44em, .md-h2 1.2em), bullets, **bold**,
 * *italic* and `code` (monospace, on the raised fill). */
@Composable
private fun Markdown(block: String, color: Color, codeBg: Color) {
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        for (raw in block.lines()) {
            val h = Regex("^(#{1,6})\\s+(.*)$").find(raw)
            val bullet = Regex("^\\s*([-*+]|\\d+\\.)\\s+(.*)$").find(raw)
            val (text, style) = when {
                h != null -> h.groupValues[2] to Type.body.copy(
                    fontSize = Type.body.fontSize * (if (h.groupValues[1].length == 1) 1.44f else if (h.groupValues[1].length == 2) 1.2f else 1f),
                    fontWeight = Tokens.Font.bold)
                bullet != null -> ((if (bullet.groupValues[1].first().isDigit()) bullet.groupValues[1] + " " else "•  ") +
                    bullet.groupValues[2]) to Type.body
                else -> raw to Type.body
            }
            Text(inline(text, codeBg), style = style, color = color)
        }
    }
}

private fun inline(text: String, codeBg: Color) = androidx.compose.ui.text.buildAnnotatedString {
    val token = Regex("""`([^`]+)`|\*\*([^*]+)\*\*|__([^_]+)__|\*([^*\s][^*]*)\*|_([^_\s][^_]*)_""")
    var at = 0
    for (m in token.findAll(text)) {
        append(text.substring(at, m.range.first))
        val (v, style) = when {
            m.groups[1] != null -> m.groupValues[1] to androidx.compose.ui.text.SpanStyle(
                fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace, background = codeBg)
            m.groups[2] != null -> m.groupValues[2] to androidx.compose.ui.text.SpanStyle(
                fontWeight = androidx.compose.ui.text.font.FontWeight.Bold)
            m.groups[3] != null -> m.groupValues[3] to androidx.compose.ui.text.SpanStyle(
                fontWeight = androidx.compose.ui.text.font.FontWeight.Bold)
            else -> (m.groups[4] ?: m.groups[5])!!.value to androidx.compose.ui.text.SpanStyle(
                fontStyle = androidx.compose.ui.text.font.FontStyle.Italic)
        }
        withStyle(style) { append(v) }
        at = m.range.last + 1
    }
    append(text.substring(at))
}

/** A file picked, as it's sent (a picture as Baude makes one: prepare), or why it can't be. */
private fun read(ctx: Context, uri: Uri): Pair<Picked?, String?> {
    val r = ctx.contentResolver
    var name = "file"
    r.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { cur ->
        if (cur.moveToFirst()) {
            name = cur.getString(0) ?: name
            if (!cur.isNull(1) && cur.getLong(1) > MAX_FILE) return null to "$name is too big to send (20 MB at most)"
        }
    }
    val mime = r.getType(uri) ?: "application/octet-stream"
    val bytes = try {
        r.openInputStream(uri)?.use { it.readBytes() }
    } catch (e: Exception) {
        null
    } ?: return null to "$name couldn't be read"
    if (mime.startsWith("image/")) {
        val (type, data) = prepare(bytes, mime) ?: return null to "$name isn't a picture this phone can read"
        return Picked(name, type, data) to null
    }
    val text = mime.startsWith("text/") || mime in setOf("application/json", "application/xml", "application/x-yaml")
    if (mime != "application/pdf" && !text) return null to "$name can't be sent: pictures, PDFs and text files can"
    return Picked(name, mime, bytes) to null
}

/** A picture as Baude sends one (prepare_image): a type the API takes, at most MAX_EDGE across and MAX_BYTES big
 * (5 MB once base64'd), made smaller and a JPEG (or a PNG, with see-through parts) where it isn't. */
private fun prepare(bytes: ByteArray, mime: String): Pair<String, ByteArray>? {
    val bounds = android.graphics.BitmapFactory.Options().apply { inJustDecodeBounds = true }
    android.graphics.BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
    if (bounds.outWidth <= 0) return null
    val turn = try { // (a camera's picture, upright as it was taken: its EXIF orientation)
        when (android.media.ExifInterface(java.io.ByteArrayInputStream(bytes)).getAttributeInt(
            android.media.ExifInterface.TAG_ORIENTATION, android.media.ExifInterface.ORIENTATION_NORMAL)) {
            android.media.ExifInterface.ORIENTATION_ROTATE_90 -> 90f
            android.media.ExifInterface.ORIENTATION_ROTATE_180 -> 180f
            android.media.ExifInterface.ORIENTATION_ROTATE_270 -> 270f
            else -> 0f
        }
    } catch (_: Exception) {
        0f
    }
    if (turn == 0f && mime in Mesh.IMAGE_TYPES && maxOf(bounds.outWidth, bounds.outHeight) <= MAX_EDGE &&
        bytes.size <= MAX_BYTES) return mime to bytes
    var sample = 1 // (decoded at a fraction of its size where it's far bigger: a 50 MP photo isn't held whole)
    while (maxOf(bounds.outWidth, bounds.outHeight) / (sample * 2) >= MAX_EDGE) sample *= 2
    var bmp = android.graphics.BitmapFactory.decodeByteArray(bytes, 0, bytes.size,
        android.graphics.BitmapFactory.Options().apply { inSampleSize = sample }) ?: return null
    if (turn != 0f) {
        bmp = android.graphics.Bitmap.createBitmap(bmp, 0, 0, bmp.width, bmp.height,
            android.graphics.Matrix().apply { postRotate(turn) }, true)
    }
    val edge = maxOf(bmp.width, bmp.height)
    if (edge > MAX_EDGE) {
        val k = MAX_EDGE.toFloat() / edge
        bmp = android.graphics.Bitmap.createScaledBitmap(bmp, (bmp.width * k).toInt().coerceAtLeast(1),
            (bmp.height * k).toInt().coerceAtLeast(1), true)
    }
    if (bmp.hasAlpha()) {
        val png = java.io.ByteArrayOutputStream().also { bmp.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it) }
        if (png.size() <= MAX_BYTES) return "image/png" to png.toByteArray()
    }
    if (bmp.hasAlpha()) { // (a JPEG has no see-through: on white, as Baude puts it)
        val flat = android.graphics.Bitmap.createBitmap(bmp.width, bmp.height, android.graphics.Bitmap.Config.ARGB_8888)
        android.graphics.Canvas(flat).apply { drawColor(android.graphics.Color.WHITE); drawBitmap(bmp, 0f, 0f, null) }
        bmp = flat
    }
    for (quality in intArrayOf(90, 80, 70, 60, 50)) {
        val jpg = java.io.ByteArrayOutputStream().also { bmp.compress(android.graphics.Bitmap.CompressFormat.JPEG, quality, it) }
        if (jpg.size() <= MAX_BYTES) return "image/jpeg" to jpg.toByteArray()
    }
    return null
}

private const val MAX_EDGE = WireConstants.IMAGE_EDGE_MAX
private const val MAX_BYTES = WireConstants.IMAGE_BYTES_MAX
private const val MAX_FILE = 20L * 1024 * 1024

// ----------------------------------------------------------------------------------------------------- hosts --

/** This phone's key, your hosts (added as user@host: a host's Tailscale name works, as ssh's), each one's key trusted
 * once you've compared it, and the members of their mesh, reached through them. */
@Composable
fun HostsScreen(back: () -> Unit) {
    val c = LocalBaude.current
    val ctx = LocalContext.current
    Mesh.tick.value
    var adding by remember { mutableStateOf(false) }
    var removing by remember { mutableStateOf<Host?>(null) }
    var renewing by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxSize().background(c.frame)) {
        Bar("Hosts", start = { IconButton(Adwaita.GoPrevious, Wording.Browser.back, onClick = back) },
            end = {
                IconButton(Adwaita.ListAdd, Wording.Sidebar.addHost) { adding = true }
                if (Mesh.key.value != null) Menu(listOf("New Key…" to { renewing = true }))
            })
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).navigationBarsPadding().padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)) {
            val key = Mesh.key.value
            Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.inside).padding(12.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("This phone's key", style = Type.title, color = c.fg)
                val error = Mesh.keyError.value
                if (key == null && error != null) {
                    Text(error, style = Type.body, color = c.destructive)
                    Button(Wording.Common.tryAgain, suggested = true) { Mesh.readKey() }
                } else if (key == null) {
                    Text("Make a key for this phone, then put it in each host's ~/.ssh/authorized_keys.",
                        style = Type.body, color = c.dim)
                    if (Mesh.making.value) Text("Making the key…", style = Type.caption, color = c.dim)
                    else Button("Make Key", suggested = true) { Mesh.makeKey() }
                } else {
                    Text("Put this line in each host's ~/.ssh/authorized_keys.", style = Type.body, color = c.dim)
                    SelectionContainer {
                        Text(key.line, style = Type.mono, color = c.fg, modifier = Modifier.fillMaxWidth()
                            .clip(RoundedCornerShape(Gap.codeRadius)).background(c.raised).padding(8.dp))
                    }
                    Button(Wording.Common.copy, suggested = true) {
                        ctx.getSystemService(ClipboardManager::class.java)
                            .setPrimaryClip(ClipData.newPlainText("Baude's key", key.line))
                        Toast.makeText(ctx, "Copied", Toast.LENGTH_SHORT).show()
                    }
                    if (Mesh.making.value) Text("Making the key…", style = Type.caption, color = c.dim)
                    error?.let { Text(it, style = Type.caption, color = c.destructive) }
                }
            }
            if (Mesh.hosts.isEmpty()) Text("No hosts yet. Add one as user@host.",
                style = Type.body, color = c.dim, modifier = Modifier.padding(horizontal = 4.dp))
            for (h in Mesh.hosts.toList()) {
                val conn = Mesh.conn(h.id)
                val (state, colour) = when (conn?.state) {
                    "up" -> "Connected" to c.success
                    "connecting" -> Wording.Hosts.looking(host = h.label) to c.dim
                    "key" -> (if (conn.shown?.changed == true) "Its key changed, so it isn't connected: tap to see it"
                              else "Its key isn't trusted yet: tap to check it") to
                        (if (conn.shown?.changed == true) c.destructive else c.warning)
                    "down" -> (conn.why?.let { Wording.Hosts.downBecause(host = h.label, why = it) } ?: Wording.Hosts.down(host = h.label)) to
                        c.destructive
                    else -> (if (key == null) "Make this phone's key first" else "Not connected") to c.dim
                }
                Row(
                    Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.inside)
                        .clickable {
                            if (conn?.state == "key") Mesh.declined.removeAll { it.startsWith(h.id + " ") } // (asked again)
                            else Mesh.connect(h)
                        }.padding(12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(Modifier.weight(1f)) {
                        Text(h.label, style = Type.bold, color = c.fg)
                        Text(h.dest, style = Type.caption, color = c.dim)
                        Text(state, style = Type.caption, color = colour)
                    }
                    IconButton(Adwaita.UserTrash, Wording.Common.remove) { removing = h }
                }
            }
            Members()
        }
    }
    if (adding) AddHost(done = { adding = false })
    removing?.let { h ->
        AlertDialog(onDismissRequest = { removing = null }, title = { Text(Wording.MeshVersions.removeHeading(host = h.label)) },
            text = { Text(Wording.MeshVersions.removeBody) },
            confirmButton = { Button(Wording.Common.remove, destructive = true) { Mesh.removeHost(h); removing = null } },
            dismissButton = { Button(Wording.Common.cancel) { removing = null } }, containerColor = c.dialog)
    }
    if (renewing) AlertDialog(onDismissRequest = { renewing = false }, title = { Text("Make a new key?") },
        text = { Text("Hosts that have the current key need the new one in their authorized_keys.") },
        confirmButton = { Button("Make Key", destructive = true) { Mesh.makeKey(); renewing = false } },
        dismissButton = { Button(Wording.Common.cancel) { renewing = false } }, containerColor = c.dialog)
}

/** The members of your hosts' mesh: each reached through a host of yours that's up (the one that reaches it in fewest
 * hops), without this phone's key on it. */
@Composable
private fun Members() {
    val c = LocalBaude.current
    Mesh.tick.value // (a connection's state changed: drawn again)
    val members = Mesh.members.values.sortedWith(compareBy({ it.first.hops?.toInt() ?: 99 }, { it.first.name.orEmpty().lowercase() }))
    if (members.isEmpty()) return
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text("Mesh", style = Type.folder, color = c.dim, modifier = Modifier.padding(start = 4.dp, top = 4.dp, bottom = 4.dp))
        Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.inside)) {
            members.forEachIndexed { i, (m, via) ->
                val id = m.id.orEmpty()
                val route = via?.ifBlank { null }
                val conn = Mesh.conn("mesh:$id")
                val through = m.through?.ifBlank { null }
                val path = listOfNotNull(route, through).joinToString(" and ")
                val named = m.name.orEmpty().ifBlank { m.host.orEmpty() }
                val (state, colour) = when {
                    conn?.state == "up" -> "Connected" to c.success
                    conn?.listed == false -> "Not in reach now" to c.dim
                    conn?.state == "down" -> (conn.why?.let { Wording.Hosts.downBecause(host = named, why = it) }
                        ?: Wording.Hosts.down(host = named)) to c.destructive
                    else -> Wording.Hosts.looking(host = named) to c.dim
                }
                if (i > 0) Box(Modifier.fillMaxWidth().padding(start = 12.dp).heightIn(min = 1.dp).background(c.rule))
                Row(Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(m.name.orEmpty().ifBlank { m.host.orEmpty() }, style = Type.bold, color = c.fg)
                        val where = m.host?.ifBlank { null }
                        Text(listOfNotNull(where, if (path.isNotEmpty()) Wording.Sidebar.through(via = path) else null).joinToString(" · "),
                            style = Type.caption, color = c.dim)
                        Text(state, style = Type.caption, color = colour)
                    }
                }
            }
        }
    }
}

/** A host's key, when it isn't one you've trusted, asked about wherever you are, as ssh asks: the first time, its
 * fingerprint, to trust or not; changed, the key trusted before and the one shown now, refused unless you trust the
 * new one on purpose. Not trusting leaves the host unconnected (tap it in Hosts to be asked again). */
@Composable
fun HostKeyAsk() {
    val c = LocalBaude.current
    Mesh.tick.value
    val conn = Mesh.hosts.firstNotNullOfOrNull { h ->
        Mesh.conn(h.id)?.takeIf { it.state == "key" && it.shown != null && "${h.id} ${it.shown!!.fingerprint}" !in Mesh.declined }
    } ?: return
    val host = conn.host ?: return
    val shown = conn.shown ?: return
    val decline = { Mesh.declined.add("${host.id} ${shown.fingerprint}"); Unit }
    AlertDialog(
        onDismissRequest = decline,
        title = { Text(if (shown.changed) "${host.label}'s key changed" else "Trust ${host.label}?") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (shown.changed) {
                    Text("${host.dest} showed a key that isn't the one you trusted. Someone may be pretending to be it, " +
                        "or its host key was replaced. This phone won't connect to it unless you trust the new key.")
                    KeyLines("Trusted before", shown.wasType ?: "?", shown.wasFingerprint ?: "?")
                    KeyLines("Shown now", shown.type, shown.fingerprint)
                } else {
                    Text("This phone hasn't connected to ${host.dest} before. Compare its key's fingerprint with the " +
                        "host's own (ssh-keygen -lf on its host key) before you trust it.")
                    KeyLines(null, shown.type, shown.fingerprint)
                }
            }
        },
        confirmButton = {
            if (shown.changed) Button("Don't Connect", suggested = true, onClick = decline)
            else Button("Trust", suggested = true) { Mesh.trust(host, shown) }
        },
        dismissButton = {
            if (shown.changed) Button("Trust the New Key", destructive = true) { Mesh.trust(host, shown) }
            else Button("Don't Trust", onClick = decline)
        },
        containerColor = c.dialog,
    )
}

@Composable
private fun KeyLines(label: String?, type: String, fingerprint: String) {
    val c = LocalBaude.current
    Column {
        Text(listOfNotNull(label, type).joinToString(" · "), style = Type.caption, color = c.dim)
        SelectionContainer { Text(fingerprint, style = Type.mono, color = c.fg) }
    }
}

@Composable
fun AddHost(done: () -> Unit, given: org.json.JSONObject? = null, edited: ((org.json.JSONObject) -> Unit)? = null,
            added: () -> Unit = {}) { // (given, edited: Meta's add_host form's values, filled by it and by you)
    val c = LocalBaude.current
    var dest by remember { mutableStateOf(given?.str("destination").orEmpty()) }
    var name by remember { mutableStateOf("") }
    var command by remember { mutableStateOf(given?.str("command") ?: "baude_headless.py") }
    LaunchedEffect(dest, command) { edited?.invoke(org.json.JSONObject().put("destination", dest).put("command", command)) }
    val host = Host.parse(dest, name, command)
    AlertDialog(
        onDismissRequest = done,
        title = { Text(Wording.NewSession.addHost) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Field(dest, { dest = it }, "user@host (or user@host:port)")
                Field(name, { name = it }, "Name (optional)")
                Field(command, { command = it }, Wording.Hosts.commandPlaceholder(command = "baude_headless.py"))
                Text("Over Tailscale, use the host's Tailscale name or address.", style = Type.caption, color = c.dim)
            }
        },
        confirmButton = { Button(Wording.Common.add, suggested = true) { if (host != null) { Mesh.addHost(host); added(); done() } } },
        dismissButton = { Button(Wording.Common.cancel, onClick = done) },
        containerColor = c.dialog,
    )
}

@Composable
fun Field(value: String, set: (String) -> Unit, hint: String) {
    val c = LocalBaude.current
    Box(Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).background(c.fg.copy(alpha = 0.08f))
        .padding(horizontal = 10.dp, vertical = 9.dp)) {
        if (value.isEmpty()) Text(hint, style = Type.body, color = c.dim)
        BasicTextField(value, set, singleLine = true, textStyle = Type.body.copy(color = c.fg),
            cursorBrush = SolidColor(c.accentBg), modifier = Modifier.fillMaxWidth())
    }
}
