package dev.baude.android

import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshots.SnapshotStateMap
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Base64
import java.util.Calendar
import java.util.Date
import java.util.Locale

/** A picture of an ask's, fetched (inbox_picture): drawn, and its bytes for the large view. */
class AskPicture(val bitmap: ImageBitmap, val bytes: ByteArray)

/** The Inbox as this app has it (Inbox.page.json): every host's asks of a person (inbox, once a link's up; then
 * inbox_changed), the form each ask's filled with so far, its pictures as they're fetched, and the ask open (or last
 * opened), which Meta reads and fills (MetaTools' read_ask, fill_ask). Compose's state, changed on the main thread. */
object Inbox {
    val asks = mutableStateMapOf<String, List<Msg.InboxAsk>>() // (a connection's id: its host's asks, waiting or stopped)
    private val asked = java.util.WeakHashMap<Connection, Boolean>() // (the links asked for their asks: once each)
    private val forms = HashMap<String, SnapshotStateMap<String, Any>>() // ("host/ask": its form's values, text or on/off)
    val pictures = mutableStateMapOf<String, AskPicture>() // ("host/ask/version/name")
    private val fetching = HashSet<String>()
    val failed = mutableStateMapOf<String, Boolean>() // (pictures that couldn't be fetched)
    val versions = mutableStateMapOf<String, Int>() // ("host/ask": the version ⋯ → History shows, by index; none: the last)
    var shown: Pair<String, String>? = null // (the ask open, or last opened: host, ask id)

    fun key(host: String, id: String?) = "$host/${id.orEmpty()}"

    private fun kept(a: Msg.InboxAsk) = a.status == "waiting" || a.status == "stopped"

    /** (a link listed its sessions) Its host asked for its asks, once each link; one that says no has none. */
    fun listed(c: Connection) {
        if (asked[c] == true || !takes(c.info, Op.INBOX)) return
        asked[c] = true
        val id = c.id
        c.request(Msg.Inbox().json()) { r ->
            Mesh.post {
                if (Mesh.conn(id) !== c) return@post
                if (r.optString("op") != Op.INBOX_REPLY) asks.remove(id)
                else asks[id] = Msg.InboxReply.from(r).asks.orEmpty().filter(::kept)
            }
        }
    }

    /** An ask as its host says it is now: answered, withdrawn or removed, out; else put in (or in its place). A model's
     * answer that's just come fills the form with its values, for the person to check. */
    fun changed(host: String, ev: Msg.InboxChanged) {
        val a = ev.ask ?: return
        val before = asks[host].orEmpty().firstOrNull { it.id == a.id }
        val rest = asks[host].orEmpty().filter { it.id != a.id }
        asks[host] = if (ev.removed != true && kept(a)) rest + a else rest
        val p = a.proposal
        if (p?.status == "ready" && before?.proposal?.status != "ready") {
            val v = a.versions?.lastOrNull() ?: return
            val values = p.values ?: return
            fill(host, a, v, values)
        }
    }

    fun find(host: String, id: String): Msg.InboxAsk? = asks[host]?.firstOrNull { it.id == id }

    /** Every host's asks, newest first: (host, ask). */
    fun all(): List<Pair<String, Msg.InboxAsk>> =
        asks.flatMap { (h, l) -> l.map { h to it } }.sortedByDescending { it.second.at?.toDouble() ?: 0.0 }

    /** How many asks are waiting on you, on every host. */
    fun waiting(): Int = asks.values.sumOf { l -> l.count { it.status == "waiting" } }

    /** The ask's form's values (its last version's fields, each its default till it's filled). */
    fun form(host: String, a: Msg.InboxAsk, v: Msg.InboxVersion): SnapshotStateMap<String, Any> {
        val m = forms.getOrPut(key(host, a.id)) { mutableStateMapOf() }
        for (f in v.form.orEmpty()) {
            val n = f.name ?: continue
            if (n in m) continue
            m[n] = when (f.type) {
                "bool" -> f.default == true
                "choice" -> defaultText(f).ifBlank { f.choices.orEmpty().firstOrNull().orEmpty() }
                else -> defaultText(f)
            }
        }
        return m
    }

    /** The form's values as they're sent: a number as the number it is (none while it's empty), on/off as true or false. */
    fun sent(v: Msg.InboxVersion, values: Map<String, Any>): JSONObject = JSONObject().apply {
        for (f in v.form.orEmpty()) {
            val n = f.name ?: continue
            val x = values[n] ?: continue
            when (f.type) {
                "bool" -> put(n, x == true)
                "number" -> numberOf(x.toString())?.let { put(n, it) }
                else -> put(n, x.toString())
            }
        }
    }

    /** values put in the ask's form where they fit its fields: what didn't, said (none: all of it did). */
    fun fill(host: String, a: Msg.InboxAsk, v: Msg.InboxVersion, values: JSONObject): List<String> {
        val m = form(host, a, v)
        val wrong = ArrayList<String>()
        for (k in values.keys()) {
            val f = v.form.orEmpty().firstOrNull { it.name == k }
            val x = values.opt(k)
            if (f == null) { wrong.add("$k: no such field"); continue }
            when (f.type) {
                "bool" -> when {
                    x is Boolean -> m[k] = x
                    x.toString().lowercase() in setOf("true", "false") -> m[k] = x.toString().lowercase() == "true"
                    else -> wrong.add("$k: on or off (true or false), not $x")
                }
                "number" -> if (numberOf(x.toString()) != null) m[k] = x.toString() else wrong.add("$k: a number, not $x")
                "choice" -> if (x.toString() in f.choices.orEmpty()) m[k] = x.toString()
                    else wrong.add("$k: one of ${f.choices.orEmpty().joinToString(", ")}, not $x")
                else -> m[k] = if (x == null || x == JSONObject.NULL) "" else x.toString()
            }
        }
        return wrong
    }

    /** One of an ask's pictures, fetched once (inbox_picture) and kept. */
    fun fetch(host: String, id: String, version: Int, name: String) {
        val k = "${key(host, id)}/$version/$name"
        if (k in pictures || k in fetching || failed[k] == true) return
        fetching.add(k)
        Mesh.ask(host, Msg.InboxPictureGet(ask = id, version = version, name = name).json()) { r ->
            fetching.remove(k)
            val bytes = if (r.optString("op") != Op.INBOX_PICTURE_DATA) null
                else try { Base64.getDecoder().decode(Msg.InboxPictureReply.from(r).data.orEmpty()) } catch (_: Exception) { null }
            val bmp = bytes?.let { BitmapFactory.decodeByteArray(it, 0, it.size) }
            if (bytes == null || bmp == null) failed[k] = true else pictures[k] = AskPicture(bmp.asImageBitmap(), bytes)
        }
    }

    /** The ask as Meta reads it (read_ask): its title, body, buttons by index, form and its values now, task and run. */
    fun described(host: String, a: Msg.InboxAsk): String {
        val v = a.versions?.lastOrNull() ?: return "(it has no version)"
        val values = form(host, a, v)
        return buildString {
            append("<ask id=\"${a.id}\" status=\"${a.status}\" task=\"${a.taskName}\" task_id=\"${a.task}\" run=\"${a.run}\" ")
            append("bot=\"${a.bot.orEmpty()}\" host=\"${Mesh.label(host)}\" version=\"${a.versions.orEmpty().size}\">\n")
            v.title?.let { append("Title: $it\n") }
            append(v.body.orEmpty().trim()).append("\n\n")
            append(Wording.Inbox.buttonsSaid).append("\n")
            v.buttons.orEmpty().forEachIndexed { i, b -> append("$i. ${b.label}\n") }
            if (!v.form.isNullOrEmpty()) {
                append(Wording.Inbox.formSaid).append("\n")
                for (f in v.form.orEmpty()) append("- ${f.name} (${f.type}${f.choices?.let { ": " + it.joinToString(", ") } ?: ""})" +
                    (f.label?.let { " \"$it\"" } ?: "") + " = ${values[f.name.orEmpty()] ?: ""}\n")
            }
            a.proposal?.let { p -> append("A model's answer (${p.name}, ${p.status}): button ${p.button}, ${p.text.orEmpty()}\n") }
            a.sentToName?.let { append(Wording.Inbox.sentTo(bot = it)).append("\n") }
            append("</ask>")
        }
    }
}

// ------------------------------------------------------------------------------------------------- the home row --

/** Home's Inbox row (Sidebar.region.json's), right under Search sessions, drawn as New session is: the envelope and
 * "Inbox", then how many asks are waiting on every host and the waiting dot; with none, neither. */
@Composable
fun InboxRow(onClick: () -> Unit) {
    val c = LocalBaude.current
    val n = Inbox.waiting()
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(6.dp)).tip(Wording.Inbox.tip, onClick)
        .padding(start = 8.dp, end = 8.dp, top = 7.dp, bottom = 7.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Icon(Adwaita.MailUnread, null, tint = c.fg, modifier = Modifier.size(16.dp.icon))
        Text(Wording.Inbox.title, style = Type.body, color = c.fg, maxLines = 1, modifier = Modifier.weight(1f))
        if (n > 0) {
            Text("$n", style = Type.body, color = c.dim)
            StatusDot("", true)
        }
    }
}

// ------------------------------------------------------------------------------------------------- the Inbox --

/** When an ask was raised, as its head says it: HH:MM today, else the weekday and time this week, else the date. */
fun askWhen(at: Double?): String {
    at ?: return ""
    val d = Date((at * 1000).toLong())
    val day = SimpleDateFormat("yyyyMMdd", Locale.ROOT)
    val week = Calendar.getInstance().apply { add(Calendar.DAY_OF_YEAR, -6) }.time
    val pattern = when {
        day.format(d) == day.format(Date()) -> "HH:mm"
        d.after(week) -> "EEE HH:mm"
        else -> "d MMM"
    }
    return SimpleDateFormat(pattern, Locale.ENGLISH).format(d)
}

/** The chats every host lists that wait on a permission (Pending requests), and those at a turn (Running): newest first. */
private fun chatsWhere(keep: (Msg.Session) -> Boolean): List<Item> = Mesh.sessions.flatMap { (h, l) ->
    l.filter { it.archived != true && it.draft != true && keep(it) }.map { Item(h, it, 0) }
}.sortedByDescending { (it.s.said ?: it.s.updated)?.toDouble() ?: 0.0 }

/** The Inbox (Inbox.page.json, its phone override): a screen of its own from home's Inbox row, Back returns. The asks
 * tasks have raised, newest first, each a card cut a few lines down; then Pending requests and Running, each left out
 * when it's empty; "Nothing is waiting on you" when all are. */
@Composable
fun InboxScreen(back: () -> Unit, open: (String, String) -> Unit, ask: (String, String) -> Unit) {
    val c = LocalBaude.current
    Mesh.tick.value
    val asks = Inbox.all()
    val pending = chatsWhere { !it.asks.isNullOrEmpty() }
    val running = chatsWhere { it.state == "working" && it.asks.isNullOrEmpty() }
    AskPictures {
    Box(Modifier.fillMaxSize().background(c.frame)) {
        Column(Modifier.fillMaxSize()) {
            Bar(Wording.Inbox.title, start = { IconButton(Adwaita.GoPrevious, Wording.Browser.back, onClick = back) })
            if (asks.isEmpty() && pending.isEmpty() && running.isEmpty()) Box(Modifier.weight(1f).fillMaxWidth().background(c.inside),
                contentAlignment = Alignment.Center) {
                Text(Wording.Inbox.nothing, style = Type.body, color = c.dim, textAlign = TextAlign.Center)
            } else {
                LazyColumn(Modifier.weight(1f).fillMaxWidth().background(c.inside).navigationBarsPadding(),
                    contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 10.dp)) {
                    items(asks, key = { "a:" + Inbox.key(it.first, it.second.id) }) { (h, a) ->
                        AskCard(h, a) { ask(h, a.id.orEmpty()) }
                    }
                    if (pending.isNotEmpty()) {
                        item(key = "h:pending") { GroupHead(Wording.Inbox.pendingRequests) }
                        items(pending, key = { "p:" + it.hostId + "/" + it.id }) { item ->
                            val tool = item.s.asks?.firstOrNull()?.tool.orEmpty()
                            Box(Modifier.padding(horizontal = Gap.inside)) {
                                ChatRow(item, Wording.Asks.allowTool(tool = toolTitle(tool).ifBlank { Wording.Asks.aTool })) {
                                    open(item.hostId, item.id)
                                }
                            }
                        }
                    }
                    if (running.isNotEmpty()) {
                        item(key = "h:running") { GroupHead(Wording.Signal.running) }
                        items(running, key = { "r:" + it.hostId + "/" + it.id }) { item ->
                            Box(Modifier.padding(horizontal = Gap.inside)) { ChatRow(item, null) { open(item.hostId, item.id) } }
                        }
                    }
                }
            }
        }
        ViewerOverlay()
    }
    }
}

/** A chat's row as home draws it: a Bot's with its picture and name, another's its title; tag: what it asks. */
@Composable
private fun ChatRow(item: Item, tag: String?, onClick: () -> Unit) {
    if (item.bot != null && !dead(item.hostId)) BotRow(item, false, tag, onClick) else SessionRow(item, emptySet(), false, tag, onClick)
}

/** A group's heading (Pending requests, Running), as home's sections' are. */
@Composable
private fun GroupHead(text: String) {
    val c = LocalBaude.current
    Text(text, style = Type.folder.copy(fontSize = Type.body.fontSize * 0.93f), color = c.fg, maxLines = 1,
        modifier = Modifier.alpha(0.6f).padding(start = Gap.inside + 4.dp, end = Gap.inside, top = 14.dp, bottom = 4.dp))
}

/** An ask in the Inbox: a card as wide as the screen with the chat's gutters, its head (title, when, the waiting dot;
 * a stopped one's "Not answered — the task stopped"), then the ask as it's opened, its body cut a few lines down. */
@Composable
private fun AskCard(host: String, a: Msg.InboxAsk, open: () -> Unit) {
    val c = LocalBaude.current
    val stopped = a.status == "stopped"
    val v = a.versions?.lastOrNull() ?: return
    Column(Modifier.fillMaxWidth().padding(horizontal = Gap.inside, vertical = 5.dp).clip(RoundedCornerShape(12.dp))
        .background(c.card).border(1.dp, c.rule, RoundedCornerShape(12.dp)).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FadedLine(askTitle(v), Type.bold.copy(textDecoration = if (stopped) androidx.compose.ui.text.style.TextDecoration.LineThrough else null),
                if (stopped) c.dim else c.fg, c.card, Modifier.weight(1f).clickable(onClick = open))
            if (stopped) Text(Wording.Inbox.notAnswered, style = Type.caption, color = c.dim, maxLines = 1)
            else {
                Text(askWhen(a.at?.toDouble()), style = Type.caption, color = c.dim, maxLines = 1)
                StatusDot("", true)
            }
        }
        AskView(host, a, inList = true, under = c.card)
    }
}

/** What an ask's called: its title, else its body's first line (its heading's marks off). */
fun askTitle(v: Msg.InboxVersion): String = v.title?.ifBlank { null }
    ?: v.body.orEmpty().lineSequence().map { it.trim().trimStart('#', ' ') }.firstOrNull { it.isNotEmpty() }.orEmpty()

/** A line of text on one line, fading out into under where it's cut. */
@Composable
private fun FadedLine(text: String, style: androidx.compose.ui.text.TextStyle, color: Color, under: Color, modifier: Modifier) {
    var cut by remember(text) { mutableStateOf(false) }
    Text(text, style = style, color = color, maxLines = 1, softWrap = false, overflow = TextOverflow.Clip,
        onTextLayout = { cut = it.hasVisualOverflow }, modifier = modifier.drawWithContent {
            drawContent()
            if (cut) {
                val w = 28.dp.toPx().coerceAtMost(size.width)
                drawRect(Brush.horizontalGradient(listOf(under.copy(alpha = 0f), under), startX = size.width - w, endX = size.width),
                    topLeft = Offset(size.width - w, 0f), size = Size(w, size.height))
            }
        })
}

// ------------------------------------------------------------------------------------------------ an ask opened --

/** An ask opened (InboxAsk.page.json, its phone override): a screen of its own from the Inbox, titled with its task's
 * name, Back returns. The ask whole, and the composer at its foot writing to the asking task's Bot. One that stops
 * waiting while it's open (answered elsewhere): "This ask is no longer waiting", its buttons off. */
@Composable
fun AskScreen(host: String, id: String, back: () -> Unit) {
    val c = LocalBaude.current
    val now = Inbox.find(host, id)
    var last by remember { mutableStateOf(now) }
    if (now != null && now !== last) last = now
    val a = now ?: last
    DisposableEffect(host, id) { Inbox.shown = host to id; onDispose { } }
    AskPictures {
    Box(Modifier.fillMaxSize().background(c.frame)) {
        Column(Modifier.fillMaxSize().imePadding()) {
            Bar(a?.taskName?.ifBlank { null } ?: Wording.Inbox.title,
                start = { IconButton(Adwaita.GoPrevious, Wording.Browser.back, onClick = back) })
            Column(Modifier.weight(1f).fillMaxWidth().background(c.inside)) {
                if (a == null) Box(Modifier.weight(1f).fillMaxWidth(), contentAlignment = Alignment.Center) {
                    Text(Wording.Inbox.gone, style = Type.body, color = c.dim)
                } else {
                    Column(Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState())
                        .padding(horizontal = Gap.chatH, vertical = 12.dp)) {
                        AskView(host, a, inList = false, under = c.inside, gone = now == null, answered = back)
                    }
                    val bot = a.bot?.ifBlank { null } ?: Mesh.contact(host, a.session.orEmpty())?.name ?: Wording.Sidebar.bot
                    Box(Modifier.navigationBarsPadding()) {
                        AskBox("", compact = true, sendLabel = Wording.Inbox.sendTo(bot = bot)) { text ->
                            Mesh.ask(host, Msg.InboxSay(ask = id, text = text).json()) { r -> refused(r)?.let(::toast) }
                        }
                    }
                }
            }
        }
        ViewerOverlay()
    }
    }
}

/** Why a request about an ask wasn't done, for a person (null: it was). */
private fun refused(r: JSONObject): String? = if (r.optString("op") == Op.ERROR) Msg.Error.from(r).said.orEmpty() else null

/** The ask's pictures where its body names them, and the large view for them (a tap; a long-press, their menu). */
@Composable
private fun AskPictures(content: @Composable () -> Unit) {
    val opener = { k: String -> openAskPicture(k) }
    CompositionLocalProvider(LocalImageOpener provides opener, LocalImageItem provides { k: String -> askItems(k).firstOrNull { it.key == k } }) {
        content()
    }
}

/** The pictures of the ask version a picture's key names ("ask:host/ask/version/name"), as the large view has them. */
private fun askItems(key: String): List<ViewItem> {
    val k = key.removePrefix("ask:")
    val prefix = k.substringBeforeLast("/") + "/"
    return Inbox.pictures.filterKeys { it.startsWith(prefix) }.map { (pk, p) ->
        ViewItem("ask:$pk", p.bytes, null, null, pk.substringAfterLast("/"), null)
    }
}

private fun openAskPicture(key: String) {
    val items = askItems(key)
    Viewer.open(items, items.indexOfFirst { it.key == key }.coerceAtLeast(0), ImageSpots.rects[key])
}

/** The pictures an ask's version names, drawn where its body names them (MarkdownPictures): on a line of its own as
 * wide as the text, its height as its shape has it, rounded; alone in a table's cell, a small one; never a path. */
private class VersionPictures(val host: String, val id: String, val version: Int, val v: Msg.InboxVersion) : MarkdownPictures {
    fun meta(src: String) = v.pictures.orEmpty().firstOrNull { it.name == src }

    override fun cellWidth(src: String): Dp = meta(src)?.width?.toInt()?.let { minOf((it / 2).dp, CELL_MOST) } ?: CELL_MOST

    @Composable override fun Line(alt: String, src: String) {
        BoxWithConstraints(Modifier.fillMaxWidth()) { Picture(alt, src, maxWidth, Tokens.Markdown.imageRadius) }
    }

    @Composable override fun Cell(alt: String, src: String) = Picture(alt, src, cellWidth(src), 4.dp)

    @Composable fun Picture(alt: String, src: String, width: Dp, radius: Dp) {
        val c = LocalBaude.current
        val k = "${Inbox.key(host, id)}/$version/$src"
        LaunchedEffect(k) { Inbox.fetch(host, id, version, src) }
        val got = Inbox.pictures[k]
        val m = meta(src)
        val ratio = got?.let { it.bitmap.height.toFloat() / it.bitmap.width } ?: m?.let { p ->
            p.width?.toFloat()?.takeIf { it > 0 }?.let { w -> p.height?.toFloat()?.takeIf { it > 0 }?.div(w) } } ?: 0.5f
        if (Inbox.failed[k] == true || (m == null && got == null)) {
            Text(Wording.Inbox.noPicture, style = Type.caption, color = c.dim)
            return
        }
        val shape = RoundedCornerShape(radius)
        if (got == null) Box(Modifier.width(width).height(width * ratio).clip(shape).background(c.raised))
        else ImageTouch("ask:$k") {
            Image(got.bitmap, alt.ifBlank { Wording.Inbox.picture }, contentScale = ContentScale.FillBounds,
                modifier = Modifier.width(width).height(width * ratio).clip(shape))
        }
    }
}

private val CELL_MOST = 120.dp
private val CUT_AT = 160.dp

/** An ask as it's drawn, in the Inbox's list (inList: its body cut a few lines down) and opened: who it's from and ⋮,
 * its Markdown with its pictures, a model's answer, its form and its buttons with Route to…. gone: it's stopped
 * waiting while it's open; answered: a button of it pressed here, and taken. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun AskView(host: String, a: Msg.InboxAsk, inList: Boolean, under: Color, gone: Boolean = false, answered: () -> Unit = {}) {
    val c = LocalBaude.current
    val id = a.id.orEmpty()
    val all = a.versions.orEmpty()
    if (all.isEmpty()) return
    val k = Inbox.key(host, id)
    val at = (Inbox.versions[k] ?: all.lastIndex).coerceIn(0, all.lastIndex)
    val v = all[at]
    val latest = at == all.lastIndex
    val stopped = a.status == "stopped"
    val live = a.status == "waiting" && !gone && latest
    val values = Inbox.form(host, a, all.last())
    val pictures = remember(k, at, v) { VersionPictures(host, id, at + 1, v) }
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        FromRow(host, a, inList, all, at)
        val body = if (stopped) strikeFirstHeading(v.body.orEmpty()) else v.body.orEmpty()
        CompositionLocalProvider(LocalMarkdownPictures provides pictures) {
            Cut(inList, under) { MarkdownView(body, color = if (stopped) c.dim else c.fg, under = under) }
        }
        if (gone) Text(Wording.Inbox.gone, style = Type.body, color = c.dim)
        a.proposal?.takeIf { live || it.status == "answering" && !stopped }?.let { Proposal(host, id, it, v) }
        if (!v.form.isNullOrEmpty()) FormRows(v, values, enabled = live)
        val suggested = a.proposal?.takeIf { it.status == "ready" }?.button?.toInt()
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp),
            itemVerticalAlignment = Alignment.CenterVertically) {
            v.buttons.orEmpty().forEachIndexed { i, b ->
                AskButton(host, id, at + 1, v, b, enabled = live, suggested = live && suggested == i) {
                    Mesh.ask(host, Msg.InboxPress(ask = id, button = i, values = Inbox.sent(v, values)).json()) { r ->
                        refused(r)?.let(::toast) ?: answered()
                    }
                }
            }
            Box(Modifier.weight(1f), contentAlignment = Alignment.CenterEnd) { RouteTo(host, a, enabled = live) }
        }
    }
}

/** The body's first heading struck through (a stopped ask's). */
private fun strikeFirstHeading(md: String): String {
    val lines = md.split("\n").toMutableList()
    val i = lines.indexOfFirst { Regex("^#{1,6}\\s+\\S").containsMatchIn(it) }
    if (i >= 0) lines[i] = Regex("^(#{1,6}\\s+)(.*?)\\s*$").replace(lines[i]) { m -> "${m.groupValues[1]}~~${m.groupValues[2]}~~" }
    return lines.joinToString("\n")
}

/** Who it's from: the asking Bot's picture, "from Task “<task>” on <host>" (dim, small); at its end, a stopped ask's
 * "Not answered — the task stopped" (opened; in the list its head says it) or "Sent to <Bot>", then ⋮: History (each
 * version and when it came, the one shown ticked) and, on a stopped ask, Remove. */
@Composable
private fun FromRow(host: String, a: Msg.InboxAsk, inList: Boolean, all: List<Msg.InboxVersion>, at: Int) {
    val c = LocalBaude.current
    val contact = Mesh.contact(host, a.session.orEmpty())
    LaunchedEffect(contact?.pictureKey) { contact?.let { Mesh.picture(it) } }
    val name = a.bot?.ifBlank { null } ?: contact?.name ?: Wording.Sidebar.bot
    var menu by remember { mutableStateOf(false) }
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Avatar(name, contact?.let { Mesh.pictures[it.pictureKey] }, size = 20.dp)
        Text(Wording.Inbox.fromTask(task = a.taskName.orEmpty(), host = Mesh.label(host)), style = Type.caption, color = c.dim,
            maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
        val end = when {
            a.status == "stopped" && !inList -> Wording.Inbox.notAnswered
            a.sentToName != null -> Wording.Inbox.sentTo(bot = a.sentToName)
            else -> null
        }
        end?.let { Text(it, style = Type.caption, color = c.dim, maxLines = 1) }
        Box {
            IconButton(Adwaita.ViewMore, Wording.Inbox.more, side = 28.dp) { menu = true }
            Popover(menu, { menu = false }, ticks = Ticks.END) {
                MenuHeading(Wording.Inbox.history)
                for (i in all.indices.reversed()) {
                    MenuChoice(Wording.Inbox.versionAt(n = i + 1, `when` = askWhen(all[i].at?.toDouble())), i == at) {
                        menu = false
                        if (i == all.lastIndex) Inbox.versions.remove(Inbox.key(host, a.id)) else Inbox.versions[Inbox.key(host, a.id)] = i
                    }
                }
                if (a.status == "stopped") {
                    MenuDivider()
                    MenuChoice(Wording.Common.remove, false) {
                        menu = false
                        Mesh.ask(host, Msg.InboxRemove(ask = a.id).json()) { r -> refused(r)?.let(::toast) }
                    }
                }
            }
        }
    }
}

/** content cut CUT_AT down (cut: while it's taller) with a fade into under, "Show more" under it to show it whole
 * ("Show less" then); not cut, it's drawn whole. */
@Composable
private fun Cut(cut: Boolean, under: Color, content: @Composable () -> Unit) {
    var tall by remember { mutableStateOf(false) }
    var whole by remember { mutableStateOf(false) }
    val c = LocalBaude.current
    val folded = cut && tall && !whole
    Column {
        Layout(content, Modifier.clipToBounds().drawWithContent {
            drawContent()
            if (folded) {
                val h = 48.dp.toPx().coerceAtMost(size.height)
                drawRect(Brush.verticalGradient(listOf(under.copy(alpha = 0f), under), startY = size.height - h, endY = size.height),
                    topLeft = Offset(0f, size.height - h), size = Size(size.width, h))
            }
        }) { ms, k ->
            val ps = ms.map { it.measure(k.copy(minHeight = 0, maxHeight = Constraints.Infinity)) }
            val h = ps.maxOfOrNull { it.height } ?: 0
            val most = CUT_AT.roundToPx()
            if (cut && (h > most) != tall) tall = h > most
            val shown = if (cut && h > most && !whole) most else h
            layout(ps.maxOfOrNull { it.width } ?: 0, shown) { ps.forEach { it.place(0, 0) } }
        }
        if (cut && tall) Text(if (whole) Wording.Inbox.showLess else Wording.Git.showMore, style = Type.bold, color = c.fg,
            modifier = Modifier.padding(top = 4.dp).clip(RoundedCornerShape(6.dp)).clickable { whole = !whole }
                .padding(horizontal = 8.dp, vertical = 6.dp))
    }
}

/** The ask's form as setting rows (TaskField's types, as Run's form has them): text, an entry the row's width with its
 * label in it; number, an entry at the row's end; on/off, a switch; choice, a drop-down. */
@Composable
private fun FormRows(v: Msg.InboxVersion, values: SnapshotStateMap<String, Any>, enabled: Boolean) {
    val c = LocalBaude.current
    Box(Modifier.alpha(if (enabled) 1f else 0.5f)) {
        BoxedList(v.form.orEmpty(), "", inset = 0.dp) { f ->
            val n = f.name.orEmpty()
            val label = f.label?.ifBlank { null } ?: n
            when (f.type) {
                "bool" -> SwitchRow(label, null, values[n] == true, enabled = enabled, titleLines = Int.MAX_VALUE) { values[n] = it }
                "choice" -> Choice(label, f.choices.orEmpty(), values[n]?.toString().orEmpty(), { it }, enabled = enabled,
                    titleLines = Int.MAX_VALUE) { values[n] = it }
                "number" -> {
                    val text = values[n]?.toString().orEmpty()
                    val bad = text.isNotBlank() && numberOf(text) == null
                    ActionRow(label, null, titleLines = Int.MAX_VALUE, suffix = {
                        Box(Modifier.width(96.dp).clip(RoundedCornerShape(8.dp)).background(c.fg.copy(alpha = 0.08f))
                            .padding(horizontal = 10.dp, vertical = 8.dp)) {
                            BasicTextField(text, { values[n] = it }, enabled = enabled, singleLine = true,
                                textStyle = Type.body.copy(color = if (bad) c.destructive else c.fg), cursorBrush = SolidColor(c.accentBg),
                                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number), modifier = Modifier.fillMaxWidth())
                        }
                    })
                }
                else -> EntryRow(label, values[n]?.toString().orEmpty(), false, false, {}) { if (enabled) values[n] = it }
            }
        }
    }
}

/** A button's colours: (fill, text) by its style; yellow's text dark. */
@Composable
private fun buttonColours(style: String?): Pair<Color, Color> {
    val c = LocalBaude.current
    return when (style) {
        "primary" -> c.accentBg to c.accentFg
        "blue" -> Tokens.Accent.blue to Color.White
        "teal" -> Tokens.Accent.teal to Color.White
        "green" -> Tokens.Accent.green to Color.White
        "yellow" -> Tokens.Accent.yellow to Color(0xCC000000)
        "orange" -> Tokens.Accent.orange to Color.White
        "red" -> Tokens.Accent.red to Color.White
        "pink" -> Tokens.Accent.pink to Color.White
        "purple" -> Tokens.Accent.purple to Color.White
        "slate" -> Tokens.Accent.slate to Color.White
        else -> c.fg.copy(alpha = 0.10f) to c.fg
    }
}

/** One of the ask's buttons, sized to its label: its style's colours; a picture button, its picture over its label;
 * suggested (a model would press it), ringed in the accent. */
@Composable
private fun AskButton(host: String, id: String, version: Int, v: Msg.InboxVersion, b: Msg.InboxButton, enabled: Boolean,
                      suggested: Boolean, onClick: () -> Unit) {
    val c = LocalBaude.current
    val (fill, text) = buttonColours(b.style)
    val shape = RoundedCornerShape(8.dp)
    val ring = if (suggested) Modifier.border(2.dp, c.accent, RoundedCornerShape(10.dp)).padding(3.dp) else Modifier
    val pic = b.picture?.ifBlank { null }
    Box(ring.alpha(if (enabled) 1f else 0.4f)) {
        if (pic == null) Box(Modifier.clip(shape).background(fill).clickable(enabled = enabled, onClick = onClick)
            .padding(horizontal = 14.dp, vertical = 8.dp), contentAlignment = Alignment.Center) {
            Text(b.label.orEmpty(), style = Type.bold, color = text)
        } else Column(Modifier.width(PICTURE_BUTTON + 12.dp).clip(shape).background(fill).clickable(enabled = enabled, onClick = onClick)
            .padding(6.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(6.dp)) {
            val k = "${Inbox.key(host, id)}/$version/$pic"
            LaunchedEffect(k) { Inbox.fetch(host, id, version, pic) }
            val got = Inbox.pictures[k]
            val m = v.pictures.orEmpty().firstOrNull { it.name == pic }
            val ratio = got?.let { it.bitmap.height.toFloat() / it.bitmap.width }
                ?: m?.let { p -> p.width?.toFloat()?.takeIf { it > 0 }?.let { w -> p.height?.toFloat()?.takeIf { it > 0 }?.div(w) } } ?: 1f
            val box = Modifier.width(PICTURE_BUTTON).height(PICTURE_BUTTON * ratio).clip(RoundedCornerShape(6.dp))
            if (got != null) Image(got.bitmap, null, contentScale = ContentScale.FillBounds, modifier = box)
            else Box(box.background(c.raised))
            Text(b.label.orEmpty(), style = Type.caption.copy(fontWeight = FontWeight.Bold), color = text, textAlign = TextAlign.Center)
        }
    }
}

private val PICTURE_BUTTON = 92.dp

/** Route to…: a flat menu button; its menu "Have a model answer" (the ask's host's models, as the model picker lists
 * them) and "Send to a Bot" (that host's Bots, with their pictures). */
@Composable
private fun RouteTo(host: String, a: Msg.InboxAsk, enabled: Boolean) {
    val c = LocalBaude.current
    var open by remember { mutableStateOf(false) }
    Box {
        Row(Modifier.clip(RoundedCornerShape(6.dp)).alpha(if (enabled) 1f else 0.4f)
            .tip(Wording.Inbox.routeTip) { if (enabled) open = true }.padding(horizontal = 8.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(Wording.Inbox.routeTo, style = Type.bold, color = c.fg, maxLines = 1)
            Icon(Adwaita.PanDown, null, tint = c.fg, modifier = Modifier.size(16.dp.icon))
        }
        Popover(open, { open = false }) {
            fun route(model: String? = null, effort: String? = null, bot: String? = null) {
                open = false
                Mesh.ask(host, Msg.InboxRoute(ask = a.id, model = model, effort = effort, bot = bot).json()) { r -> refused(r)?.let(::toast) }
            }
            MenuHeading(Wording.Inbox.haveModelAnswer)
            for (m in models(host)) MenuLine(onClick = { route(model = m.value, effort = m.defaultEffort) }) {
                val logo = modelLogo(m.value, m.name)
                if (logo != null) Icon(logo, null, tint = c.fg, modifier = Modifier.size(16.dp.icon))
                else Spacer(Modifier.size(16.dp.icon))
                Text(m.name, style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            }
            val bots = Mesh.contacts.filter { it.hostId == host }.sortedBy { it.name.lowercase() }
            if (bots.isNotEmpty()) {
                MenuDivider()
                MenuHeading(Wording.Inbox.sendToBot)
                for (b in bots) MenuLine(onClick = { route(bot = b.id) }) {
                    LaunchedEffect(b.pictureKey) { Mesh.picture(b) }
                    Avatar(b.name, Mesh.pictures[b.pictureKey], size = 22.dp)
                    Text(b.name, style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                }
            }
        }
    }
}

/** A model's answer over the buttons: while it's asked, "<model> is answering…" with a spinner; ready, "<model> would
 * press “<button>”" (bold) and its reason, with Dismiss; failed, "<model> couldn't answer: <why>", with Dismiss. */
@Composable
private fun Proposal(host: String, id: String, p: Msg.InboxProposal, v: Msg.InboxVersion) {
    val c = LocalBaude.current
    val name = p.name?.ifBlank { null } ?: p.model.orEmpty()
    fun dismiss() = Mesh.ask(host, Msg.InboxRoute(ask = id).json()) { r -> refused(r)?.let(::toast) }
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).background(c.quiet).padding(horizontal = 12.dp, vertical = 10.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp)) {
        when (p.status) {
            "answering" -> Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                CircularProgressIndicator(Modifier.size(16.dp), color = c.fg.copy(alpha = 0.55f), strokeWidth = 2.dp)
                Text(Wording.Inbox.modelAnswering(model = name), style = Type.body, color = c.fg)
            }
            "ready" -> {
                val label = p.button?.toInt()?.let { v.buttons.orEmpty().getOrNull(it)?.label }.orEmpty()
                Text(Wording.Inbox.modelWouldPress(model = name, button = label), style = Type.bold, color = c.fg)
                p.text?.ifBlank { null }?.let { Text(it, style = Type.body, color = c.fg) }
                ProposalDismiss(::dismiss)
            }
            else -> {
                Text(Wording.Inbox.modelFailed(model = name, why = p.text.orEmpty()), style = Type.body, color = c.fg)
                ProposalDismiss(::dismiss)
            }
        }
    }
}

@Composable
private fun ProposalDismiss(onClick: () -> Unit) {
    val c = LocalBaude.current
    Text(Wording.CodeApart.dismiss, style = Type.bold, color = c.fg, modifier = Modifier.padding(top = 2.dp)
        .clip(RoundedCornerShape(6.dp)).background(c.fg.copy(alpha = 0.10f)).clickable(onClick = onClick)
        .padding(horizontal = 12.dp, vertical = 6.dp))
}
