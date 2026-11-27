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
import androidx.compose.foundation.layout.aspectRatio
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
import androidx.compose.foundation.shape.CircleShape
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
    private val asked = java.util.Collections.synchronizedMap(java.util.WeakHashMap<Connection, Boolean>()) // (the links asked for their asks: once each time one's up)
    private val forms = HashMap<String, SnapshotStateMap<String, Any>>() // ("host/ask": its form's values, text or on/off)
    val pictures = mutableStateMapOf<String, AskPicture>() // ("host/ask/version/name")
    private val fetching = HashSet<String>()
    val failed = mutableStateMapOf<String, Boolean>() // (pictures that couldn't be fetched)
    val versions = mutableStateMapOf<String, Int>() // ("host/ask": the version ⋯ → History shows, by index; none: the last)
    var shown: Pair<String, String>? = null // (the ask open, or last opened: host, ask id)

    fun key(host: String, id: String?) = "$host/${id.orEmpty()}"

    /** Everything forgotten (the screenshot rig's, between its scenes). */
    fun clear() {
        asks.clear(); forms.clear(); pictures.clear(); fetching.clear(); failed.clear(); versions.clear(); shown = null
    }

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

    /** (a link went down) Its asks asked for again once it's up: it comes back as the same connection, and what changed
     * while it was away isn't told. */
    fun down(c: Connection) {
        asked.remove(c)
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

    /** A picture's bytes drawn no bigger than the display needs (its sides halved while both stay over it), so a huge
     * one can't run the app out of memory; null if they aren't a picture. */
    fun decoded(bytes: ByteArray): android.graphics.Bitmap? {
        val o = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, o)
        if (o.outWidth <= 0 || o.outHeight <= 0) return null
        val screen = android.content.res.Resources.getSystem().displayMetrics
        val px = maxOf(screen.widthPixels, screen.heightPixels)
        var sample = 1
        while (minOf(o.outWidth, o.outHeight) / (sample * 2) >= px) sample *= 2
        return BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = sample })
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
            val bmp = bytes?.let(::decoded)
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

/** Home's Inbox row (Sidebar.region.json's), right over Search sessions, drawn as New session is: the envelope and
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
 * a stopped one's title struck through), then the ask as it's opened, its body cut a few lines down. */
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
            if (!stopped) { // (a stopped one's "Not answered" is on its from line: the phone's too narrow for it beside the title)
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

/** The pictures of the ask version a picture's key names ("ask:host/ask/version/name"), as the large view has them:
 * those its body shows, in its order, then its picture buttons', each named by its alt text or its button's label. */
private fun askItems(key: String): List<ViewItem> {
    val k = key.removePrefix("ask:")
    val version = k.substringBeforeLast("/")
    val ask = version.substringBeforeLast("/")
    val n = version.substringAfterLast("/").toIntOrNull() ?: 0
    val v = Inbox.asks.firstNotNullOfOrNull { (h, l) -> l.firstOrNull { Inbox.key(h, it.id) == ask } }?.versions?.getOrNull(n - 1)
    val named = LinkedHashMap<String, String>()
    if (v != null) {
        for (m in MD_IMAGE.findAll(v.body.orEmpty())) named.putIfAbsent(m.groupValues[2], m.groupValues[1].ifBlank { m.groupValues[2] })
        for (b in v.buttons.orEmpty()) b.picture?.ifBlank { null }?.let { named.putIfAbsent(it, b.label.orEmpty().ifBlank { it }) }
    }
    if (k.substringAfterLast("/") !in named) named[k.substringAfterLast("/")] = k.substringAfterLast("/")
    return named.mapNotNull { (name, title) ->
        Inbox.pictures["$version/$name"]?.let { ViewItem("ask:$version/$name", it.bytes, null, null, title, null) }
    }
}

private val MD_IMAGE = Regex("!\\[([^\\]]*)\\]\\(([^)\\s]+)\\)")

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

private val CELL_MOST = 80.dp
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
        FromRow(host, a, all, at)
        val body = if (stopped) strikeFirstHeading(v.body.orEmpty()) else v.body.orEmpty()
        CompositionLocalProvider(LocalMarkdownPictures provides pictures) {
            Box(Modifier.alpha(if (stopped) 0.55f else 1f)) { Cut(inList, under) { MarkdownView(body, under = under) } }
        }
        if (gone) Text(Wording.Inbox.gone, style = Type.body, color = c.dim)
        a.proposal?.takeIf { live || it.status == "answering" && !stopped }?.let { Proposal(host, id, it, v) }
        if (!v.form.isNullOrEmpty()) FormRows(v, values, enabled = live)
        val suggested = a.proposal?.takeIf { it.status == "ready" }?.button?.toInt()
        val press = { i: Int ->
            Mesh.ask(host, Msg.InboxPress(ask = id, button = i, version = v.n, values = Inbox.sent(v, values)).json()) { r ->
                refused(r)?.let(::toast) ?: answered()
            }
        }
        val (withPictures, plain) = v.buttons.orEmpty().withIndex().partition { !it.value.picture.isNullOrBlank() }
        if (withPictures.isNotEmpty()) BoxWithConstraints(Modifier.fillMaxWidth()) {
            val cols = ((maxWidth + PICTURE_GAP) / (PICTURE_BUTTON + PICTURE_GAP)).toInt().coerceIn(1, PICTURE_ROW)
            val cell = (maxWidth - PICTURE_GAP * (cols - 1)) / cols
            Column(verticalArrangement = Arrangement.spacedBy(PICTURE_GAP)) {
                for (row in withPictures.chunked(cols)) Row(horizontalArrangement = Arrangement.spacedBy(PICTURE_GAP)) {
                    for ((i, b) in row) PictureButton(host, id, at + 1, v, b, enabled = live, suggested = live && suggested == i,
                        modifier = Modifier.width(cell)) { press(i) }
                }
            }
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp),
            itemVerticalAlignment = Alignment.CenterVertically) {
            for ((i, b) in plain) AskButton(b, enabled = live, suggested = live && suggested == i) { press(i) }
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
 * "Not answered — the task stopped" or "Sent to <Bot>", then ⋮: History (each
 * version and when it came, the one shown ticked) and, on a stopped ask, Remove. */
@Composable
private fun FromRow(host: String, a: Msg.InboxAsk, all: List<Msg.InboxVersion>, at: Int) {
    val c = LocalBaude.current
    val contact = Mesh.contact(host, a.session.orEmpty())
    LaunchedEffect(contact?.pictureKey) { contact?.let { Mesh.picture(it) } }
    val name = a.bot?.ifBlank { null } ?: contact?.name ?: Wording.Sidebar.bot
    var menu by remember { mutableStateOf(false) }
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Avatar(name, contact?.let { Mesh.pictures[it.pictureKey] }, size = 20.dp.icon)
        val end = when {
            a.status == "stopped" -> Wording.Inbox.notAnswered
            a.sentToName != null -> Wording.Inbox.sentTo(bot = a.sentToName)
            else -> null
        }
        // (what's at its end follows it on the line, wrapping under it where the phone's too narrow for both)
        Text(Wording.Inbox.fromTask(task = a.taskName.orEmpty(), host = Mesh.label(host)) + (end?.let { " · $it" } ?: ""),
            style = Type.caption, color = c.dim, maxLines = 3, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
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

/** One of the ask's buttons, sized to its label (wide: as wide as what it's in): its style's colours; suggested (a
 * model would press it), ringed in the accent. */
@Composable
private fun AskButton(b: Msg.InboxButton, enabled: Boolean, suggested: Boolean, wide: Boolean = false, onClick: () -> Unit) {
    val c = LocalBaude.current
    val (fill, text) = buttonColours(b.style)
    val shape = RoundedCornerShape(8.dp)
    val ring = if (suggested) Modifier.border(2.dp, c.accent, RoundedCornerShape(10.dp)).padding(3.dp) else Modifier
    val width = if (wide) Modifier.fillMaxWidth() else Modifier
    Box(width.then(ring).alpha(if (enabled) 1f else 0.4f)) {
        Box(width.clip(shape).background(fill)
            .clickable(enabled = enabled, onClick = onClick).padding(horizontal = 14.dp, vertical = 8.dp), contentAlignment = Alignment.Center) {
            Text(b.label.orEmpty(), style = Type.bold, color = text, textAlign = TextAlign.Center)
        }
    }
}

/** A picture button, as wide as it's given: its picture (a tap opens it in the large view, with the ask's other
 * pictures either side; a magnifier in its corner says so) over the button with its label, which presses it. */
@Composable
private fun PictureButton(host: String, id: String, version: Int, v: Msg.InboxVersion, b: Msg.InboxButton, enabled: Boolean,
                          suggested: Boolean, modifier: Modifier, onClick: () -> Unit) {
    val c = LocalBaude.current
    val pic = b.picture.orEmpty()
    val k = "${Inbox.key(host, id)}/$version/$pic"
    LaunchedEffect(k) { Inbox.fetch(host, id, version, pic) }
    val got = Inbox.pictures[k]
    val m = v.pictures.orEmpty().firstOrNull { it.name == pic }
    val ratio = (got?.let { it.bitmap.height.toFloat() / it.bitmap.width }
        ?: m?.let { p -> p.width?.toFloat()?.takeIf { it > 0 }?.let { w -> p.height?.toFloat()?.takeIf { it > 0 }?.div(w) } } ?: 1f)
        .coerceIn(0.2f, PICTURE_TALL)
    val frameShape = RoundedCornerShape(12.dp)
    Column(modifier, verticalArrangement = Arrangement.spacedBy(6.dp)) {
        val frame = Modifier.fillMaxWidth().aspectRatio(1f / ratio).clip(frameShape).background(c.raised)
            .border(1.dp, c.fg.copy(alpha = 0.12f), frameShape)
        if (got == null) Box(frame)
        else ImageTouch("ask:$k") {
            Box(frame) {
                Image(got.bitmap, b.label.orEmpty().ifBlank { Wording.Inbox.picture }, contentScale = ContentScale.Fit,
                    modifier = Modifier.fillMaxSize())
                Box(Modifier.align(Alignment.TopEnd).padding(6.dp).clip(CircleShape).background(Color.Black.copy(alpha = 0.55f))
                    .padding(4.dp)) {
                    Icon(Adwaita.SystemSearch, null, tint = Color.White, modifier = Modifier.size(14.dp))
                }
            }
        }
        AskButton(b, enabled = enabled, suggested = suggested, wide = true, onClick = onClick)
    }
}

private val PICTURE_BUTTON = 150.dp // (a picture button, at least, wide: as many to a row as fit, PICTURE_ROW at most)
private const val PICTURE_ROW = 2
private val PICTURE_GAP = 10.dp
private const val PICTURE_TALL = 1.25f // (a picture button's picture, at most, this many times as tall as it's wide)

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
                    Avatar(b.name, Mesh.pictures[b.pictureKey], size = 20.dp.icon)
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
