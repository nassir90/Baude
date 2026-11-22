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
import androidx.compose.foundation.layout.BoxWithConstraints
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
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.compositeOver
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.layout.onSizeChanged
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
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.isCtrlPressed
import androidx.compose.ui.input.key.isMetaPressed
import androidx.compose.ui.input.key.isShiftPressed
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.positionInRoot
import androidx.compose.ui.input.key.type
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.dp

// ------------------------------------------------------------------------------------------------------ a chat --

/** A chat's page: what it and you have said, what it did, and a box to write in (files too: pictures and PDFs as
 * Baude sends them, text files in the message). stop: the button that ends a turn while it runs and nothing's
 * written; foot: what's under the box. */
@Composable
fun ChatPage(chat: Chat, name: String, attach: Boolean = true, stop: (() -> Unit)? = null,
             foot: (@Composable () -> Unit)? = null, overBox: (@Composable () -> Unit)? = null, own: OwnRevert? = null,
             sendTip: String? = null, openPage: (String) -> Unit = {}, open: (String, String) -> Unit = { _, _ -> }) {
    val c = LocalBaude.current
    val ctx = LocalContext.current
    val sid = chat.sid
    val at = chat.hostId // (where it's reached now: a member's chat moves to a host of yours that's its machine)
    val session = Mesh.session(at, sid)
    val working = isWorking(chat)
    val readOnly = Settings.isReadOnly(sid)
    var text by androidx.compose.runtime.saveable.rememberSaveable(at, sid) { mutableStateOf("") }
    val files = chat.files
    val pick = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        take(ctx, chat, uris)
    }
    LaunchedEffect(chat.edit.value) { // (a queued message taken back: before what's written)
        val back = chat.edit.value ?: return@LaunchedEffect
        text = back + (if (text.isBlank()) "" else "\n\n" + text)
        chat.edit.value = null
    }
    LaunchedEffect(text) { chat.typed = text }
    LaunchedEffect(text, chat.queued.size, files.size) { // (the draft kept on disk as it changes)
        kotlinx.coroutines.delay(400)
        Queue.save(chat)
    }
    LaunchedEffect(chat, chat.draft.value) { // (shared from another app: after what's written)
        val shared = chat.draft.value ?: return@LaunchedEffect
        text = if (text.isBlank()) shared else text.trimEnd() + "\n" + shared
        chat.draft.value = null
    }
    val list = rememberLazyListState()
    var revealed by remember(chat) { mutableStateOf<String?>(null) } // (the message whose revert button's shown)
    var reverting by chat::reverting
    val canRevert = own?.can?.invoke() ?: (takes(at, "control") && chat.sender == null)
    val focus = remember { androidx.compose.ui.focus.FocusRequester() }
    var field by remember { mutableStateOf(androidx.compose.ui.text.input.TextFieldValue(text)) }
    if (field.text != text) { // (put in from outside: its bullets made as typed ones are)
        field = makeBullets(androidx.compose.ui.text.input.TextFieldValue(text, androidx.compose.ui.text.TextRange(text.length)))
        if (field.text != text) text = field.text
    }
    val host = chat.sender == null // (Baude's own commands, the / and @ lists: a host's chat's)
    val marks = remember(chat) { Marks() }
    val composer = remember(chat) { ComposerState() }
    val slashLit = remember(chat) { Lit() }
    val mentionLit = remember(chat) { Lit() }
    var marksVersion by remember(chat) { mutableStateOf(0) } // (a list left without the text changing)
    marksVersion
    val cwd = session?.cwd.orEmpty()
    val hasAt = host && '@' in field.text
    LaunchedEffect(hasAt, cwd, at) { if (hasAt) Catalogue.soon(at, cwd) }
    val refs = if (hasAt) MentionList.items(chat) else emptyList()
    val slash = if (host && !readOnly && marks.dismissed != field.text) SlashList.view(chat, field.text, field.selection.end, marks.typed, marks.typedIn) else null
    LaunchedEffect(slash != null) { if (slash != null) SlashCommands.ask(chat) }
    val mention = if (hasAt && slash == null) MentionList.word(field.text, marks.at, field.selection.end)
        ?.takeIf { !SlashList.inCode(field.text, it.first) } else null
    val found = mention?.let { MentionList.search(refs, it.second) }
    val mentionShown = if (mention != null && found!!.first.isEmpty() && " " in mention.second) { marks.at = -1; null } else mention
    val caretLine = field.text.substring(0, field.selection.end.coerceIn(0, field.text.length)).count { it == '\n' }
    val atRange = mentionShown?.let { it.first until field.selection.end }
    val look = remember(field.text, caretLine, atRange, refs.size, c) {
        ComposerLook(caretLine, MentionList.known(refs), atRange, c.fg, Tokens.Color.reference, Tokens.Color.referenceTint,
            Type.body.fontSize * Tokens.Markdown.h1.toFloat(), Type.body.fontSize * Tokens.Markdown.h2.toFloat(),
            androidx.compose.ui.text.SpanStyle(fontFamily = CodeFont.family, fontSize = Tokens.Font.mono))
    }
    var layout by remember { mutableStateOf<androidx.compose.ui.text.TextLayoutResult?>(null) }
    var fieldX by remember { mutableStateOf(0f) }
    var boxX by remember { mutableStateOf(0f) }
    var composerWidth by remember { mutableStateOf(0) }
    fun apply(v: androidx.compose.ui.text.input.TextFieldValue) {
        marks.edited(field.text, v.text)
        field = v
        text = v.text
    }
    fun forgetAt() { marks.at = -1; marksVersion++ }
    fun dismissSlash() { marks.dismissed = field.text; marksVersion++ }
    fun cancelRevert() {
        val r = reverting ?: return
        if (r.asked) return // (asked of claude: its answer's waited for)
        text = r.draft
        reverting = null
    }
    androidx.activity.compose.BackHandler(enabled = reverting != null) { cancelRevert() } // (consumed while asked too: Back does nothing then)
    LaunchedEffect(chat.clearBox.value) {
        if (chat.clearBox.value) {
            text = ""
            chat.clearBox.value = false
        }
    }
    LaunchedEffect(reverting?.uuid) { if (reverting != null) focus.requestFocus() }
    LaunchedEffect(list.isScrollInProgress) { if (list.isScrollInProgress) revealed = null }
    // (kept at the end while it's there: what it said last, or what it asks, a tall one's foot too, what it's writing
    // as it grows; scrolled up to read, left where it is)
    var follow by remember(chat) { mutableStateOf(true) }
    LaunchedEffect(list.isScrollInProgress) { if (!list.isScrollInProgress) follow = !list.canScrollForward }
    LaunchedEffect(chat.jump.value) { // (Made by this chat: the call it names, its group opened)
        val id = chat.jump.value ?: return@LaunchedEffect
        val found = itemOf(chat.lines, chat.calls, id)
        chat.jump.value = null
        if (found == null) return@LaunchedEffect
        found.second?.let { chat.calls.opened[it] = true }
        follow = false
        list.animateScrollToItem(found.first)
    }
    LaunchedEffect(chat.lines.size, chat.asks.size, chat.queued.size, chat.sent.size, chat.streaming.value.length / 120) {
        val n = list.layoutInfo.totalItemsCount
        if (n > 0 && follow) {
            list.animateScrollToItem(n - 1)
            list.scrollBy(1e6f)
            // (an ask opens its group and card, which grow after this: the end kept in view while they do)
            if (chat.asks.isNotEmpty()) repeat(10) {
                kotlinx.coroutines.delay(60)
                if (follow) list.scrollBy(1e6f)
            }
        }
    }
    Mesh.tick.value
    val density = androidx.compose.ui.platform.LocalDensity.current
    val openImage = remember(chat) { { key: String -> Viewer.openFrom(chat, key) } }
    val findKey = Find.key(chat)
    val finding = Find.open[findKey] == true && chat.sender == null
    val findQuery = if (finding) Find.query[findKey].orEmpty() else ""
    var findBarHeight by remember { mutableStateOf(0.dp) } // (what the bar covers at the top: the match scrolled below it)
    val matches = remember(findQuery, chat.lines.size) { findMatches(chat, findQuery) }
    val findAt = (Find.at[findKey] ?: 0).coerceIn(0, maxOf(0, matches.size - 1))
    LaunchedEffect(findQuery, findAt, matches.size) { // (stepped to: scrolled into view)
        matches.getOrNull(findAt)?.let { list.animateScrollToItem(itemIndex(chat, it.lineIndex), -with(density) { findBarHeight.toPx().toInt() }) }
    }
    androidx.compose.runtime.CompositionLocalProvider(LocalImageOpener provides openImage,
        LocalImageItem provides { key: String -> Viewer.item(chat, key) }, LocalFind provides findQuery,
        LocalFindCurrent provides matches.getOrNull(findAt)?.line) {
    Column(Modifier.fillMaxSize().inside(c)) {
        BoxWithConstraints(Modifier.weight(1f).fillMaxWidth().onGloballyPositioned { boxX = it.positionInRoot().x }) {
        val side = if (Settings.fullWidth.value) 0.dp else (maxWidth - chatColumn(maxWidth)) / 2
        LazyColumn(Modifier.fillMaxSize().onSizeChanged {
            CodeBlocks.maxHeight = CodeBlocks.forChat(with(density) { it.height.toDp() })
        }, state = list,
            contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = Gap.chatH + side, vertical = Gap.chatV),
            verticalArrangement = Arrangement.spacedBy(Gap.messageGap)) {
            val taken = reverting?.let { r -> chat.lines.indexOfFirst { it.kind == "you" && it.uuid == r.uuid }.takeIf { it >= 0 } }
            val shown = if (taken != null) chat.lines.take(taken) else chat.lines
            val offered = alwaysOffered(session?.let { it.model.orEmpty() }, at)
            val askName = if (session?.bot != null) name else "Claude" // (a Bot's question: its name's)
            drawn(shown, chat.calls, asking = { call -> chat.asks.firstOrNull { it.toolUseId == call.id } },
                answer = { AskButtons(chat, it, offered, askName) }, view = { chat.showAgent.value = it.id }) { line ->
                val uuid = line.uuid
                androidx.compose.runtime.CompositionLocalProvider(LocalFindLine provides line) {
                Said(line, uuid?.let { chat.senders[it] }.takeIf { line.kind == "you" },
                    if (canRevert && line.kind == "you" && uuid != null) RevertOffer(revealed == uuid, { revealed = uuid }) {
                        revealed = null
                        if (reverting?.asked == true) return@RevertOffer
                        reverting = Reverting(uuid, reverting?.draft ?: text)
                        text = line.text
                    } else null)
                }
            }
            if (taken == null) items(chat.sent.flatMap { it.lines }) { line -> Said(line) }
            if (taken == null && chat.streamingThought.value.isNotBlank()) item {
                SelectionContainer { Thinking(chat.streamingThought.value) }
            }
            if (taken == null && chat.streaming.value.isNotEmpty()) item { SelectionContainer { Prose(chat.streaming.value) } }
            items(chat.asks.filter { a -> a.toolUseId == null || chat.calls.byId[a.toolUseId] == null }, key = { it.rid }) { ask ->
                Asked(chat, ask, offered, askName) // (one with no card of its own: this phone's own agents', as claude's)
            }
            val held = session?.held.orEmpty().filter { it.id.orEmpty() !in chat.holding }
            items(held, key = { "held:" + it.id.orEmpty() }) { h -> Held(chat, h, working) }
            items(chat.queued.toList(), key = { "queued:" + System.identityHashCode(it) }) { QueuedRow(chat, it, isWorking(chat)) }
            if (session?.cut != null && chat.sender == null) item(key = "cut-short") { CutShortLine(chat) }
        }
        if (finding) FindBar(chat, matches, Modifier.align(Alignment.TopCenter).onSizeChanged { findBarHeight = with(density) { it.height.toDp() } })
        // the / list or the @ list over the box: its left side on the / or @ that started it, a gap above the box
        val listWidth = if (composerWidth > 0) minOf(Tokens.Picklist.width, with(density) { composerWidth.toDp() } - 10.dp) else Tokens.Picklist.width
        fun left(at: Int): androidx.compose.ui.unit.Dp {
            val x = layout?.let { l -> if (at in 0..l.layoutInput.text.length) l.getHorizontalPosition(look.mapped(at).coerceAtMost(l.layoutInput.text.length), true) else 0f } ?: 0f
            return with(density) { (x + fieldX - boxX).toDp() }.coerceIn(0.dp, (maxWidth - listWidth).coerceAtLeast(0.dp))
        }
        if (slash != null) {
            val keys = slash.matches.map { it.optString("name") }
            PickList(slash.matches.map { SlashList.row(it) }, slashLit.at(keys, slash.query),
                if (keys.isNotEmpty()) Wording.Meta.found(n = keys.size) else Wording.Meta.nothingFound, false, listWidth, true,
                Modifier.align(Alignment.BottomStart).padding(start = left(slash.start), bottom = Tokens.Picklist.gap)) { i ->
                apply(SlashList.accept(field, slash, keys[i]))
                focus.requestFocus()
            }
        } else if (mentionShown != null) {
            val (shown, matched) = found!!
            val keys = shown.map { it.surface }
            PickList(shown.map { MentionList.row(it) }, mentionLit.at(keys, mentionShown.second),
                if (matched > 0) Wording.Meta.found(n = matched) else Wording.Meta.nothingFound, Catalogue.busy(at, cwd), listWidth, false,
                Modifier.align(Alignment.BottomStart).padding(start = left(mentionShown.first), bottom = Tokens.Picklist.gap)) { i ->
                val from = marks.at
                forgetAt()
                apply(MentionList.accept(field, from, keys[i]))
                focus.requestFocus()
            }
        }
        }
        chat.said.value?.let { Text(it, style = Type.caption, color = c.dim, modifier = Modifier.padding(horizontal = Gap.chatH)) }
        if (chat.sender == null && exhausted(chat)) Text(Wording.ContextUsage.exhausted, style = Type.caption, color = c.dim,
            modifier = Modifier.padding(horizontal = Gap.chatH))
        overBox?.invoke()
        // the composer: rounded 12, a ring of the rule, its files over what's written, + and send/stop beside it
        if (!readOnly) Column(
            Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 8.dp).onSizeChanged { composerWidth = it.width }
                .clip(RoundedCornerShape(Gap.composerRadius)).background(c.inside)
                .border(1.dp, c.rule, RoundedCornerShape(Gap.composerRadius)).padding(6.dp),
        ) {
            if (files.isNotEmpty()) Row(Modifier.padding(bottom = 6.dp).horizontalScroll(rememberScrollState()),
                horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                for (f in files.toList()) Attached(f, { files.remove(f) }) { openImage("picked:${System.identityHashCode(f)}") }
            }
            val line = with(LocalDensity.current) { Tokens.Font.bodyLine.toDp() }
            val busy = isWorking(chat)
            val conn = Mesh.conn(chat.hostId)
            val gone = chat.sender == null && (conn?.state != "up" || Stopping.line(chat.hostId) != null)
            val ready = !chat.busy.value && chat.preparing.value == 0 && !gone && !chat.chrome.missing.value &&
                !(chat.sender == null && exhausted(chat)) && reverting?.asked != true
            val sendable = ready || (chat.sender == null && isLocal(text))
            val send = {
                val typed = text.trim()
                val local = host && localCommand(chat, typed, busy, { text = ""; marks.at = -1 }, openPage, open)
                if (!local) {
                    val r = reverting
                    val markdown = toMarkdown(text)
                    if (r != null && own != null) {
                        own.commit(r, markdown)
                        text = ""
                    } else if (r != null) commitRevert(chat, r, markdown, files.toList()) { ok -> if (ok) text = "" }
                    else {
                        Queue.submit(chat, busy, markdown, files.toList())
                        text = ""
                        files.clear()
                    }
                }
            }
            fun key(e: androidx.compose.ui.input.key.KeyEvent): Boolean { // (the lists' keys, the box's, then Ctrl+Enter sends)
                if (e.type != KeyEventType.KeyDown) return false
                val sends = e.key == Key.Enter && (e.isCtrlPressed || e.isMetaPressed)
                val steps = mapOf(Key.DirectionUp to -1, Key.DirectionDown to 1, Key.PageUp to -10, Key.PageDown to 10)
                val sideways = e.key == Key.DirectionLeft || e.key == Key.DirectionRight
                if (slash != null) {
                    val names = slash.matches.map { it.optString("name") }
                    when {
                        e.isShiftPressed && (e.key in steps || sideways) -> { dismissSlash(); return false }
                        e.key in steps -> { slashLit.move(names, slash.query, steps.getValue(e.key)); return true }
                        sends -> dismissSlash()
                        e.key == Key.Tab || e.key == Key.Enter -> if (names.isNotEmpty()) {
                            apply(SlashList.accept(field, slash, names[slashLit.at(names, slash.query)]))
                            return true
                        }
                        e.key == Key.Escape -> { dismissSlash(); return names.isNotEmpty() }
                    }
                } else if (mentionShown != null) {
                    val shown = found!!.first
                    val names = shown.map { it.surface }
                    when {
                        e.key == Key.Enter && (sends || e.isShiftPressed || !mentionLit.moved && " " in mentionShown.second.trim()) -> forgetAt()
                        e.key == Key.Escape -> { forgetAt(); return shown.isNotEmpty() }
                        e.isShiftPressed && (e.key in steps || sideways) -> { forgetAt(); return false }
                        e.key in steps -> { mentionLit.move(names, mentionShown.second, steps.getValue(e.key)); return true }
                        e.key == Key.Tab || e.key == Key.Enter -> if (names.isNotEmpty()) {
                            val from = marks.at
                            val i = mentionLit.at(names, mentionShown.second)
                            forgetAt()
                            apply(MentionList.accept(field, from, names[i]))
                            return true
                        }
                    }
                }
                composerKey(e, field, composer)?.let { apply(it); return true }
                if (sends && (text.isNotBlank() || files.isNotEmpty()) && sendable) send() // (while a turn runs it queues)
                return sends
            }
            Row(verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                Box(Modifier.weight(1f).heightIn(min = line * 2 + 14.dp, max = line * 9 + 14.dp)
                    .padding(vertical = 7.dp)) {
                    Column(Modifier.verticalScroll(rememberScrollState())) {
                        BasicTextField(field, { v -> // (made over as the desktop's box is: bullets, lists, wraps; a soft keyboard's Enter taken by a list)
                            val names = slash?.matches?.map { it.optString("name") }.orEmpty()
                            when {
                                isNewline(field, v) && names.isNotEmpty() ->
                                    apply(SlashList.accept(field, slash!!, names[slashLit.at(names, slash.query)]))
                                isNewline(field, v) && mentionShown != null && found!!.first.isNotEmpty() &&
                                    (mentionLit.moved || " " !in mentionShown.second.trim()) -> {
                                    val shown = found.first.map { it.surface }
                                    val from = marks.at
                                    val i = mentionLit.at(shown, mentionShown.second)
                                    forgetAt()
                                    apply(MentionList.accept(field, from, shown[i]))
                                }
                                else -> {
                                    if (isNewline(field, v) && mentionShown != null) forgetAt()
                                    apply(composerEdit(field, v, composer))
                                }
                            }
                        }, textStyle = Type.body.copy(color = c.fg), cursorBrush = SolidColor(c.accentBg),
                            visualTransformation = look, onTextLayout = { layout = it },
                            modifier = Modifier.fillMaxWidth().heightIn(min = line * 2)
                                .drawBehind { look.drawBlocks(this, layout, c.fg.copy(alpha = c.fg.alpha * 0.07f), Tokens.Radius.code, 2.dp) }
                                .padding(horizontal = 6.dp)
                                .onGloballyPositioned { fieldX = it.positionInRoot().x }
                                .focusRequester(focus)
                                .onPreviewKeyEvent { e -> key(e) }.onKeyEvent {
                                    if (it.key == androidx.compose.ui.input.key.Key.Escape && reverting != null) {
                                        if (it.type == androidx.compose.ui.input.key.KeyEventType.KeyUp) cancelRevert()
                                        true
                                    } else false
                                })
                    }
                }
                if (attach) IconButton(Adwaita.ListAdd, Wording.Attachments.attachTip) { pick.launch(arrayOf("*/*")) }
                if (busy && stop != null && text.isBlank() && files.isEmpty())
                    IconButton(Adwaita.MediaPlaybackStop, Wording.Composer.stop, tint = c.destructive,
                        background = c.destructive.copy(alpha = 0.2f), onClick = stop)
                else
                    IconButton(Adwaita.Return, Wording.Common.send, enabled = sendable, tint = c.fg,
                        background = c.fg.copy(alpha = 0.10f), tip = sendTip) { if (text.isNotBlank() || files.isNotEmpty()) send() }
            }
        }
        foot?.invoke()
    }
    }
}

/** A file attached in the box (attachment_widget): a picture a square thumbnail, centre-cropped, a tap on it opens the
 * viewer; any other a logo and its name (the middle left out past 22 characters); and a small × to take it off. */
@Composable
private fun Attached(f: Picked, remove: () -> Unit, view: () -> Unit) {
    val c = LocalBaude.current
    val px = with(LocalDensity.current) { Tokens.Composer.thumb.toPx() }.toInt()
    val key = "picked:" + System.identityHashCode(f)
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        if (f.mime.startsWith("image/")) {
            val bmp by androidx.compose.runtime.produceState<androidx.compose.ui.graphics.ImageBitmap?>(thumbnails.get(key), f) {
                if (value != null) return@produceState
                value = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Default) {
                    try {
                        val o = android.graphics.BitmapFactory.Options().apply { inJustDecodeBounds = true }
                        android.graphics.BitmapFactory.decodeByteArray(f.bytes, 0, f.bytes.size, o)
                        var n = 1
                        while (minOf(o.outWidth, o.outHeight) / (n * 2) >= px) n *= 2
                        android.graphics.BitmapFactory.decodeByteArray(f.bytes, 0, f.bytes.size,
                            android.graphics.BitmapFactory.Options().apply { inSampleSize = n })?.let {
                            it.asImageBitmap()
                        }?.also { thumbnails.put(key, it) }
                    } catch (_: Exception) { null }
                }
            }
            bmp?.let {
                androidx.compose.foundation.Image(it, f.name, contentScale = androidx.compose.ui.layout.ContentScale.Crop,
                    modifier = Modifier.size(Tokens.Composer.thumb).clip(RoundedCornerShape(6.dp)).clickable { view() })
            } ?: Box(Modifier.size(Tokens.Composer.thumb))
        } else {
            androidx.compose.material3.Icon(if (f.mime == "application/pdf") Adwaita.XOfficeDocument else Adwaita.TextXGeneric, null,
                tint = c.fg, modifier = Modifier.size(16.dp.icon))
            Text(if (f.name.length > 22) f.name.take(10) + "…" + f.name.takeLast(11) else f.name, style = Type.body, color = c.fg, maxLines = 1)
        }
        IconButton(Adwaita.WindowClose, Wording.Common.remove, onClick = remove)
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

/** What a Bot asks to do where its call has no card (Baude's own permission box): amber-edged, what it would run as a
 * call's input is shown, and Allow, Always allow (for Claude Code's models), Deny; claude's own questions
 * (AskUserQuestion) as a form. */
@Composable
fun Asked(chat: Chat, ask: Ask, alwaysOffered: Boolean, name: String = "Claude") {
    val c = LocalBaude.current
    val questions = ask.input.optJSONArray("questions")
    Column(Modifier.fillMaxWidth().border(1.dp, c.warning, RoundedCornerShape(Gap.cardRadius))
        .padding(horizontal = 12.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        if (ask.tool == "AskUserQuestion" && questions != null) {
            Text(Wording.Asks.hasQuestion(name = name), style = Type.bold, color = c.fg)
        } else {
            val said = summary(ask.tool, ask.input)
            SelectionContainer {
                Text(androidx.compose.ui.text.buildAnnotatedString {
                    withStyle(androidx.compose.ui.text.SpanStyle(fontWeight = androidx.compose.ui.text.font.FontWeight.Bold)) {
                        append(Wording.Asks.allowTool(tool = toolTitle(ask.tool)))
                    }
                    if (said.isNotEmpty()) withStyle(androidx.compose.ui.text.SpanStyle(color = c.fg.copy(alpha = c.fg.alpha * 0.6f))) {
                        append("  $said")
                    }
                }, style = Type.body, color = c.fg)
            }
            SelectionContainer { InputView(ask.tool, ask.input) }
        }
        AskButtons(chat, ask, alwaysOffered, name)
    }
}

/** The buttons of a permission (Baude's, under a call's card or in its box): Allow, Always allow (for Claude Code's
 * models), Deny; claude's questions (AskUserQuestion) as a form, answered or skipped. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun AskButtons(chat: Chat, ask: Ask, alwaysOffered: Boolean, name: String = "Claude") {
    val c = LocalBaude.current
    val questions = ask.input.optJSONArray("questions")
    if (ask.tool == "AskUserQuestion" && questions != null) {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Questions(questions, answer = { Mesh.answer(chat, ask, allow = true, answers = it) },
                skip = { Mesh.answer(chat, ask, allow = false) })
        }
        return
    }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Button(Wording.Common.allow, suggested = true) { Mesh.answer(chat, ask, allow = true) }
            // (a plan's agreed to each time)
            if (alwaysOffered && ask.tool != "ExitPlanMode") Button(Wording.Asks.alwaysAllow(tool = toolDisplay(ask.tool).first)) {
                Mesh.answer(chat, ask, allow = true, always = true)
            }
            Button(Wording.Common.deny, destructive = true) { Mesh.answer(chat, ask, allow = false) }
        }
    }
}

/** claude's questions (AskUserQuestion), as Baude's QuestionForm: each its options (one picked, or several if it
 * says so) and Other, to write your own. Answer sends {question: what's picked, comma-joined}; Skip, none. */
@Composable
fun Questions(questions: org.json.JSONArray, answer: (org.json.JSONObject) -> Unit, skip: () -> Unit) {
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
    fun ready() = qs.indices.all { picked[it].isNotEmpty() || !other[it].isNullOrBlank() }
    qs.forEachIndexed { i, q ->
        val multi = q.optBoolean("multiSelect")
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            val head = q.optString("header")
            Text(androidx.compose.ui.text.buildAnnotatedString { // (the header and the question, one wrapping line)
                if (head.isNotBlank()) {
                    withStyle(androidx.compose.ui.text.SpanStyle(color = c.fg.copy(alpha = c.fg.alpha * 0.6f))) { append("$head  ") }
                }
                withStyle(androidx.compose.ui.text.SpanStyle(fontWeight = Tokens.Font.semibold)) { append(q.optString("question")) }
            }, style = Type.body, color = c.fg)
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
            other[i]?.let { NameField(it, { v -> other[i] = v }, Wording.Asks.ownAnswer, { if (ready()) answer(answers()) }) }
        }
    }
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        Button(Wording.Asks.answer, suggested = true, enabled = ready()) { if (ready()) answer(answers()) }
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
                modifier = Modifier.size(14.dp.icon))
            else if (on) Box(Modifier.size(6.dp).clip(androidx.compose.foundation.shape.CircleShape).background(c.accentFg))
        }
        Spacer(Modifier.width(8.dp))
        Column {
            Text(label, style = Type.body, color = c.fg)
            if (about.isNotBlank()) Text(about, style = Type.body, color = c.dim)
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
val SAID_BY = Regex("""\A<baude-sender(?: [a-z_]+="[^"<>]*")*>[^<]*</baude-sender>(?:\n\n|\n|\z)""")

/** A line of the chat: from (a message of yours) who it's from instead, if its host's sender store says it's
 * another's: where yours are, grey, as Baude draws it, with "from <who>" small at its top, and what the store says is
 * shown of it in place of its text if it says (shown: a held answer sent, drawn as the held one was). */
@Composable
fun Said(line: Line, from: Msg.StoredSender? = null, revert: RevertOffer? = null) {
    val c = LocalBaude.current
    when {
        from != null -> Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
            Column(Modifier.padding(start = 48.dp).clip(RoundedCornerShape(Gap.bubbleRadius)).background(c.raised)
                .padding(horizontal = Gap.bubbleH, vertical = Gap.bubbleV), horizontalAlignment = Alignment.End) {
                Text(Wording.Common.fromWho(author = senderName(from)), style = Type.caption, color = c.dim)
                SelectionContainer {
                    val shown = from.shown
                    Prose(SAID_BY.replace(shown ?: line.text, "").trim(), under = c.raised.compositeOver(c.inside))
                }
            }
        }
        else -> SaidLine(line, revert)
    }
}

@Composable
private fun SaidLine(line: Line, revert: RevertOffer? = null) {
    val c = LocalBaude.current
    when (line.kind) {
        "you" -> UserTurn(line, revert)
        "thinking" -> SelectionContainer { Thinking(line.text) }
        "tool" -> Text(line.text, style = Type.caption, color = c.dim, maxLines = 2, overflow = TextOverflow.Ellipsis,
            modifier = Modifier.fillMaxWidth().card(c))
        "tasknote" -> TaskNoteLine(line.text)
        "compact" -> CompactMark(line)
        "compactsum" -> LoneSummary(line)
        "rows" -> MetaRows(line.text)
        "metacall" -> MetaCallLine(line.text)
        "action" -> MetaAction(line.text)
        "login" -> Logins.all[line.text]?.let { LoginCard(it) }
        "note", "error" -> Text(line.text, style = Type.caption, color = c.dim, textAlign = TextAlign.Center,
            modifier = Modifier.fillMaxWidth())  // (.sysnote: 0.85em, faded)
        else -> SelectionContainer { Prose(line.text) }
    }
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
