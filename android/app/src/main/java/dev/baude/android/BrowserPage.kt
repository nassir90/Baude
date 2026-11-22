package dev.baude.android

import android.content.Context
import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import kotlin.math.roundToInt
import org.json.JSONObject
import java.util.Base64

/** A Bot's browser watched, as Baude's Browser page's Stream does from another machine: a connection of its own to
 * the Bot's host (one watch a connection), emission_watch on it, each frame (a JPEG) shown and acknowledged, its
 * state (tabs, the page's size, what's asked) kept; its picture's size told as it changes. */
class Watch(private val hostId: String, private val bot: Msg.BotRef) {
    val frame = mutableStateOf<ImageBitmap?>(null)
    val state = mutableStateOf<Msg.WatchState?>(null)
    val status = mutableStateOf("connecting") // (connecting, watching, failed, stopped)
    val why = mutableStateOf<String?>(null)
    @Volatile var size = IntSize(1080, 1600)
    val round = mutableIntStateOf(0) // (one more each watch's first state: the page fitted again for each)
    @Volatile private var fresh = false
    private var conn: Connection? = null

    fun start() {
        conn = Mesh.watch(hostId, ::heard) { c ->
            if (c.state == "up") {
                status.value = "connecting"
                fresh = true
                c.send(Msg.EmissionWatch(req = REQ, bot = bot, maxW = size.width.coerceAtLeast(200),
                    maxH = size.height.coerceAtLeast(200)).json())
            } else if (c.state == "down" && status.value == "watching") {
                status.value = "failed"
                why.value = Wording.BotBrowser.cantConnect
            }
        }
    }

    fun stop() {
        conn?.stop()
        conn = null
    }

    /** The picture's new size (device pixels), for frames that fit it. */
    fun resized(to: IntSize) {
        if (to == size || to.width < 1) return
        size = to
        conn?.send(Msg.EmissionWatchSay(line = Msg.WatchSay(maxW = to.width.coerceAtLeast(200),
            maxH = to.height.coerceAtLeast(200))).json())
    }

    /** (on its connection's thread) What the watch says. */
    private fun heard(c: Connection, msg: JSONObject) {
        if (msg.optInt("req", -1) != REQ) return
        when (msg.optString("op")) {
            Op.EMISSION_WATCHING -> Mesh.post { status.value = "watching"; why.value = null }
            Op.ERROR -> Mesh.post { status.value = "failed"; why.value = Msg.Error.from(msg).said.orEmpty() }
            Op.EMISSION_WATCH_END -> Mesh.post { status.value = "stopped" }
            Op.EMISSION_FRAME -> {
                val f = Msg.EmissionFrame.from(msg)
                f.state?.let { s ->
                    val first = fresh
                    fresh = false
                    Mesh.post {
                        state.value = s
                        if (first) round.intValue++
                    }
                }
                f.error?.let { e -> Mesh.post { status.value = "failed"; why.value = e } }
                val jpeg = f.jpeg.orEmpty()
                if (jpeg.isEmpty()) return
                val bytes = Base64.getDecoder().decode(jpeg)
                val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap() ?: return
                val n = f.frame?.toInt() ?: 0
                Mesh.post {
                    frame.value = bmp
                    status.value = "watching"
                    c.send(Msg.EmissionWatchSay(line = Msg.WatchSay(ack = n)).json())
                }
            }
        }
    }

    companion object {
        const val REQ = 1 // (the watch's own: no request of the connection's is numbered so)
    }
}

/** The browser's calls (the emission op's), sent one at a time in turn, each answered before the next (Asks). */
private class Calls(val hostId: String, val bot: Msg.BotRef, val failed: (String) -> Unit) {
    private val queue = ArrayDeque<JSONObject>()
    private var going = false

    fun call(c: JSONObject) {
        queue.addLast(c.put("from_pane", true))
        next()
    }

    private fun next() {
        if (going) return
        val c = queue.removeFirstOrNull() ?: return
        going = true
        Mesh.ask(hostId, Msg.Emission(bot = bot, call = c).json()) { r ->
            going = false
            val error = if (r.optString("op") == Op.ERROR) Msg.Error.from(r).error.orEmpty()
            else Msg.EmissionReply.from(r).result?.optString("error")?.ifBlank { null }
            if (error != null) failed(if (error in setOf("the server isn't there", "the server's gone", "not connected"))
                Wording.BotBrowser.cantConnect else c.optString("op").let {
                    if (it == "resize") "Couldn't resize the page." else Wording.BotBrowser.couldntDo(op = it) })
            next()
        }
    }
}

/** A Bot's browser (Baude's Browser page): its own Emission on its host, watched while the page is shown and driven
 * from here (a tap clicks, a drag scrolls, the keyboard types into the page), with back, forward, reload, the address,
 * a new tab, its tabs and what its pages ask; Read-only keeps it from being driven. */
@Composable
fun BrowserPage(hostId: String, sid: String, name: String, here: Boolean) {
    val c = LocalBaude.current
    val ctx = LocalContext.current
    val density = LocalDensity.current
    val bot = remember(sid, name) { Msg.BotRef(id = sid, name = name) }
    val watch = remember(hostId, sid) { Watch(hostId, bot) }
    var said by remember { mutableStateOf<String?>(null) }
    val calls = remember(hostId, sid) { Calls(hostId, bot) { said = it } }
    val prefs = ctx.getSharedPreferences("browser", Context.MODE_PRIVATE)
    var readOnly by remember(sid) { mutableStateOf(prefs.getBoolean("ro:$sid", false)) }
    var picture by remember { mutableStateOf(IntSize.Zero) }
    var typing by remember { mutableStateOf(false) }
    Mesh.tick.value
    val s = Mesh.session(hostId, sid)
    val reachable = Mesh.conn(hostId) != null // (opened before its host's connection is made: watched once it is)
    val phoned = s?.onPhone == true // (its browser on a phone, as its host lists it: said, not watched)
    DisposableEffect(here, hostId, sid, reachable, phoned) {
        if (here && reachable && !phoned) watch.start()
        onDispose { watch.stop() }
    }
    LaunchedEffect(picture) { // (its size told once it's settled)
        if (picture.width == 0) return@LaunchedEffect
        delay(250)
        watch.resized(picture)
    }
    val state = watch.state.value
    val tab = state?.selected?.ifBlank { null }
    val now = state?.now
    val agent = state?.agent == true
    LaunchedEffect(agent, readOnly, picture, watch.round.intValue) { // (the page made the picture's size, while the
        if (!agent || picture.width == 0) return@LaunchedEffect          // Bot's own browser's watched: each watch's own)
        delay(300)
        if (readOnly) calls.call(JSONObject().put("op", "resize").put("restore", true))
        else calls.call(JSONObject().put("op", "resize").put("width", (picture.width / density.density).roundToInt()
            .coerceAtLeast(200)).put("height", (picture.height / density.density).roundToInt().coerceAtLeast(200))
            .put("fit", true))
    }
    fun call(o: JSONObject) {
        if (readOnly || phoned) return
        said = null
        if (tab != null && !o.has("tab") && o.optString("op") !in setOf("new_tab", "answer_prompt", "resize")) o.put("tab", tab)
        calls.call(o)
    }
    val acting = s?.acting == true && s.state == "working"
    Column(Modifier.fillMaxSize()) {
        // the toolbar: back, forward, reload (stop while it loads), the address, a new tab, typing, read-only
        Row(Modifier.fillMaxWidth().padding(start = 6.dp, end = 6.dp, top = 4.dp, bottom = 4.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(2.dp)) {
            val on = !readOnly && !phoned && watch.status.value == "watching"
            IconButton(Adwaita.GoPrevious, Wording.Browser.back, enabled = on && (now == null || now.back == true)) {
                call(JSONObject().put("op", "navigate").put("url", "back")) }
            IconButton(Adwaita.GoNext, Wording.Browser.forward, enabled = on && now?.forward == true) {
                call(JSONObject().put("op", "navigate").put("url", "forward")) }
            val loading = now?.loading == true
            IconButton(if (loading) Adwaita.WindowClose else Adwaita.ViewRefresh, Wording.Browser.reload, enabled = on) {
                call(JSONObject().put("op", "navigate").put("url", "reload")) }
            Address(now?.url.orEmpty(), on, Modifier.weight(1f)) { call(JSONObject().put("op", "navigate").put("url", it)) }
            IconButton(Adwaita.ListAdd, Wording.Browser.newTab, enabled = on) { call(JSONObject().put("op", "new_tab").put("url", "about:blank")) }
            IconButton(Adwaita.InputKeyboard, "Type into the page", enabled = on,
                background = if (typing) c.fg.copy(alpha = 0.10f) else null) { typing = !typing }
            IconButton(if (readOnly) Adwaita.ChangesPrevent else Adwaita.ChangesAllow, Wording.BotBrowser.readOnly,
                background = if (readOnly) c.fg.copy(alpha = 0.10f) else null) {
                readOnly = !readOnly
                prefs.edit().putBoolean("ro:$sid", readOnly).apply()
            }
        }
        val tabs = state?.tabs.orEmpty()
        if (tabs.size > 1) Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())
            .padding(start = 6.dp, end = 6.dp, bottom = 6.dp), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            for (t in tabs) {
                val id = t.tab.orEmpty()
                val picked = id == tab
                Row(Modifier.clip(RoundedCornerShape(8.dp)).background(if (picked) c.fg.copy(alpha = 0.10f) else c.frame)
                    .border(1.dp, c.rule, RoundedCornerShape(8.dp)), verticalAlignment = Alignment.CenterVertically) {
                    val label = t.title.orEmpty().ifBlank { t.url.orEmpty() }.ifBlank { Wording.Browser.newTab }
                    Text(if (label.length > 28) label.take(27) + "…" else label, style = Type.caption, color = c.fg, maxLines = 1,
                        modifier = Modifier.clickable { call(JSONObject().put("op", "select_tab").put("tab", id)) }
                            .padding(start = 10.dp, end = 2.dp, top = 6.dp, bottom = 6.dp).widthIn(max = 180.dp))
                    IconButton(Adwaita.WindowClose, Wording.BotBrowser.closeTab) { call(JSONObject().put("op", "close_tab").put("tab", id)) }
                }
            }
        }
        for (p in state?.prompts.orEmpty()) PromptCard(p) { accept, text ->
            call(JSONObject().put("op", "answer_prompt").put("id", p.optString("id")).put("accept", accept).apply {
                if (text != null) put("text", text) })
        }
        if (typing) TypeRow(!readOnly) { call(it) }
        // the page: inside the frame, the picture letterboxed in it
        Box(Modifier.fillMaxSize().padding(start = Gap.inside, end = Gap.inside, bottom = Gap.inside)
            .border(if (acting) 2.dp else 1.dp, if (acting) c.accentBg else c.rule).background(c.inside)
            .padding(1.dp).onSizeChanged { picture = it }) { // (the same size while the Bot acts: its border over the edge)
            val bmp = watch.frame.value
            if (phoned) {
                StatusPage(Adwaita.WebBrowser, Wording.BotBrowser.onPhoneTitle,
                    Wording.BotBrowser.onPhone(bot = name.ifBlank { Wording.BotBrowser.theBot }))
                return@Box
            }
            if (bmp == null) {
                when (watch.status.value) {
                    "failed" -> StatusPage(Adwaita.WebBrowser, Wording.BotBrowser.couldntOpen, watch.why.value)
                    "stopped" -> StatusPage(Adwaita.WebBrowser, Wording.BotBrowser.stopped, Wording.BotBrowser.restartsOnOpen)
                    else -> StatusPage(Adwaita.WebBrowser, if (here) Wording.BotBrowser.connecting else Wording.BotBrowser.noPages)
                }
                return@Box
            }
            val latest = androidx.compose.runtime.rememberUpdatedState(state)
            val send = androidx.compose.runtime.rememberUpdatedState<(JSONObject) -> Unit> { call(it) }
            Image(bmp, now?.let { it.title.orEmpty() }, contentScale = ContentScale.Fit, modifier = Modifier.fillMaxSize()
                .pointerTaps(bmp, picture, { latest.value }, { readOnly }) { send.value(it) })
            if (watch.status.value == "stopped") Text(Wording.BotBrowser.stoppedAgain(again = Wording.BotBrowser.restartsOnOpen),
                style = Type.caption, color = c.dim, modifier = Modifier.align(Alignment.BottomCenter)
                    .background(c.inside).padding(start = 8.dp, end = 8.dp, bottom = 4.dp))
        }
        said?.let { Text(it, style = Type.caption, color = c.dim, modifier = Modifier.padding(horizontal = 10.dp, vertical = 2.dp)) }
    }
}

/** Where a point on the picture is on the page, in its CSS pixels (as the pane works it out: the frame letterboxed in
 * the picture, then the page's own size over the frame's); null outside the frame. */
private fun css(p: Offset, bmp: ImageBitmap, picture: IntSize, state: Msg.WatchState?): Pair<Double, Double>? {
    val fw = bmp.width.toDouble()
    val fh = bmp.height.toDouble()
    val s = minOf(picture.width / fw, picture.height / fh)
    val ox = (picture.width - fw * s) / 2
    val oy = (picture.height - fh * s) / 2
    val fx = (p.x - ox) / s
    val fy = (p.y - oy) / s
    if (fx < 0 || fy < 0 || fx > fw || fy > fh) return null
    val view = state?.view
    val vw = view?.getOrNull(0)?.toDouble()?.takeIf { it > 0 } ?: fw
    val vh = view?.getOrNull(1)?.toDouble()?.takeIf { it > 0 } ?: fh
    return Math.round(fx * vw / fw * 10) / 10.0 to Math.round(fy * vh / fh * 10) / 10.0
}

/** A tap clicks (twice, a double click; held, the right button), a drag scrolls the page (sent every 50 ms). */
private fun Modifier.pointerTaps(bmp: ImageBitmap, picture: IntSize, state: () -> Msg.WatchState?, readOnly: () -> Boolean,
                                 call: (JSONObject) -> Unit): Modifier = this
    .pointerInput(bmp.width, bmp.height, picture) {
        fun click(p: Offset, press: Int, button: String) {
            if (readOnly()) return
            val (x, y) = css(p, bmp, picture, state()) ?: return
            call(JSONObject().put("op", "click").put("x", x).put("y", y).put("css", true).put("press", press)
                .put("quick", true).put("button", button))
        }
        detectTapGestures(onTap = { click(it, 1, "left") }, onDoubleTap = { click(it, 2, "left") },
            onLongPress = { click(it, 1, "right") })
    }
    .pointerInput(bmp.width, bmp.height, picture) {
        var at: Pair<Double, Double>? = null
        var dx = 0.0
        var dy = 0.0
        var last = 0L
        var k = 1.0
        val fw = bmp.width.toDouble()
        fun flush() {
            val p = at ?: return
            if (dx == 0.0 && dy == 0.0) return
            call(JSONObject().put("op", "scroll").put("x", p.first).put("y", p.second).put("css", true)
                .put("dx", Math.round(dx).toInt()).put("dy", Math.round(dy).toInt()).put("quick", true))
            dx = 0.0
            dy = 0.0
            last = System.currentTimeMillis()
        }
        detectDragGestures(onDragStart = {
            at = if (readOnly()) null else css(it, bmp, picture, state())
            val view = state()?.view // (the page's pixels a picture's pixel is)
            k = (view?.getOrNull(0)?.toDouble()?.takeIf { w -> w > 0 } ?: fw) / fw /
                minOf(picture.width / fw, picture.height / bmp.height.toDouble())
        },
            onDragEnd = { flush(); at = null }, onDragCancel = { at = null }) { change, d ->
            change.consume()
            dx -= d.x * k
            dy -= d.y * k
            if (System.currentTimeMillis() - last >= 50) flush()
        }
    }

/** The address entry (the selected tab's address, till it's focused: what you write's opened, or searched for). */
@Composable
private fun Address(url: String, enabled: Boolean, modifier: Modifier, go: (String) -> Unit) {
    val c = LocalBaude.current
    var text by remember { mutableStateOf(url) }
    var focused by remember { mutableStateOf(false) }
    LaunchedEffect(url, focused) { if (!focused) text = url }
    Box(modifier.widthIn(min = 74.dp).clip(RoundedCornerShape(8.dp)).background(c.fg.copy(alpha = 0.08f))
        .padding(horizontal = 8.dp, vertical = 7.dp)) {
        if (text.isEmpty()) Text(Wording.Browser.address, style = Type.body, color = c.dim, maxLines = 1,
            overflow = TextOverflow.Ellipsis)
        BasicTextField(text, { text = it }, enabled = enabled, singleLine = true, textStyle = Type.body.copy(color = c.fg),
            cursorBrush = SolidColor(c.accentBg), keyboardOptions = KeyboardOptions(imeAction = ImeAction.Go),
            keyboardActions = KeyboardActions(onGo = { if (text.isNotBlank()) go(text.trim()) }),
            modifier = Modifier.fillMaxWidth().onFocusChanged { focused = it.isFocused })
    }
}

/** Typing into the page: what's written here typed as it's written (taken back, Backspace), Enter and Backspace
 * keys beside it. */
@Composable
private fun TypeRow(enabled: Boolean, call: (JSONObject) -> Unit) {
    val c = LocalBaude.current
    var text by remember { mutableStateOf("") }
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { focus.requestFocus() }
    Row(Modifier.fillMaxWidth().padding(start = 6.dp, end = 6.dp, bottom = 6.dp), verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(2.dp)) {
        Box(Modifier.weight(1f).clip(RoundedCornerShape(8.dp)).background(c.fg.copy(alpha = 0.08f))
            .padding(horizontal = 8.dp, vertical = 7.dp)) {
            if (text.isEmpty()) Text("Type into the page", style = Type.body, color = c.dim)
            BasicTextField(text, { now ->
                val same = text.commonPrefixWith(now).length // (a correction: what changed taken back, then typed)
                repeat(text.length - same) { call(JSONObject().put("op", "key").put("keys", "Backspace")) }
                val added = now.substring(same)
                if (added == " ") call(JSONObject().put("op", "key").put("keys", "Space"))
                else if (added.isNotEmpty()) call(JSONObject().put("op", "type").put("text", added).put("anywhere", true))
                text = now
            }, enabled = enabled, singleLine = true, textStyle = Type.body.copy(color = c.fg), cursorBrush = SolidColor(c.accentBg),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { call(JSONObject().put("op", "key").put("keys", "Enter")); text = "" }),
                modifier = Modifier.fillMaxWidth().focusRequester(focus))
        }
        IconButton(Adwaita.EditClear, "Backspace", enabled = enabled) { call(JSONObject().put("op", "key").put("keys", "Backspace")) }
        IconButton(Adwaita.Return, "Enter", enabled = enabled) {
            call(JSONObject().put("op", "key").put("keys", "Enter")); text = "" }
    }
}

/** What a page asks (an alert, a confirm, a prompt, a sign-in, files to upload): Cancel and OK. */
@Composable
private fun PromptCard(p: JSONObject, answer: (Boolean, String?) -> Unit) {
    val c = LocalBaude.current
    val kind = p.optString("kind")
    var text by remember(p.optString("id")) { mutableStateOf(p.optString("default")) }
    Column(Modifier.fillMaxWidth().padding(6.dp).card(c), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(when (kind) {
            "sign_in" -> p.optString("host").ifBlank { Wording.Browser.aSite }.let { host ->
                p.optString("realm").let { if (it.isNotBlank()) Wording.Browser.signInRealm(host = host, realm = it)
                    else Wording.Browser.signIn(host = host) } }
            "files" -> Wording.BotBrowser.asksForFiles
            else -> p.optString("message")
        }, style = Type.body, color = c.fg)
        if (kind == "prompt") Field(text, { text = it }, "")
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            if (kind != "alert") Button(Wording.Common.cancel) { answer(false, null) }
            if (kind != "files") Button(Wording.Common.ok, suggested = true) { answer(true, if (kind == "prompt") text else null) }
        }
    }
}
