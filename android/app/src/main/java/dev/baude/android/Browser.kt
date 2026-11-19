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
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.ArrowForward
import androidx.compose.material.icons.automirrored.filled.KeyboardReturn
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Backspace
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Keyboard
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.LockOpen
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Web
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
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
import org.json.JSONObject
import java.util.Base64

/** A Bot's browser watched, as Baude's Browser page's Stream does from another machine: a connection of its own to
 * the Bot's host (one watch a connection), emission_watch on it, each frame (a JPEG) shown and acknowledged, its
 * state (tabs, the page's size, what's asked) kept; its picture's size told as it changes. */
class Watch(private val hostId: String, private val bot: JSONObject) {
    val frame = mutableStateOf<ImageBitmap?>(null)
    val state = mutableStateOf<JSONObject?>(null)
    val status = mutableStateOf("connecting") // (connecting, watching, failed, stopped)
    val why = mutableStateOf<String?>(null)
    @Volatile var size = IntSize(1080, 1600)
    private var conn: Connection? = null

    fun start() {
        conn = Mesh.watch(hostId, ::heard) { c ->
            if (c.state == "up") {
                status.value = "connecting"
                c.send(JSONObject().put("op", "emission_watch").put("req", REQ).put("bot", bot)
                    .put("max_w", size.width.coerceAtLeast(200)).put("max_h", size.height.coerceAtLeast(200)))
            } else if (c.state == "down" && status.value == "watching") {
                status.value = "failed"
                why.value = "Can't connect to the browser."
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
        conn?.send(JSONObject().put("op", "emission_watch_say").put("line", JSONObject()
            .put("max_w", to.width.coerceAtLeast(200)).put("max_h", to.height.coerceAtLeast(200))))
    }

    /** (on its connection's thread) What the watch says. */
    private fun heard(c: Connection, msg: JSONObject) {
        if (msg.optInt("req", -1) != REQ) return
        when (msg.optString("op")) {
            "emission_watching" -> Mesh.post { status.value = "watching"; why.value = null }
            "error" -> Mesh.post { status.value = "failed"; why.value = msg.optString("error") }
            "emission_watch_end" -> Mesh.post { status.value = "stopped" }
            "emission_frame" -> {
                msg.optJSONObject("state")?.let { s -> Mesh.post { state.value = s } }
                if (msg.has("error")) Mesh.post { status.value = "failed"; why.value = msg.optString("error") }
                val jpeg = msg.optString("jpeg")
                if (jpeg.isEmpty()) return
                val bytes = Base64.getDecoder().decode(jpeg)
                val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap() ?: return
                val n = msg.optInt("frame")
                Mesh.post {
                    frame.value = bmp
                    status.value = "watching"
                    c.send(JSONObject().put("op", "emission_watch_say").put("line", JSONObject().put("ack", n)))
                }
            }
        }
    }

    companion object {
        const val REQ = 1 // (the watch's own: no request of the connection's is numbered so)
    }
}

/** The browser's calls (the emission op's), sent one at a time in turn, each answered before the next (Asks). */
private class Calls(val hostId: String, val bot: JSONObject, val failed: (String) -> Unit) {
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
        Mesh.ask(hostId, JSONObject().put("op", "emission").put("bot", bot).put("call", c)) { r ->
            going = false
            val error = if (r.optString("op") == "error") r.optString("error")
            else r.optJSONObject("result")?.optString("error")?.ifBlank { null }
            if (error != null) failed(if (error in setOf("the server isn't there", "the server's gone", "not connected"))
                "Can't connect to the browser." else "Couldn't ${DOING[c.optString("op")] ?: "do that"}.")
            next()
        }
    }

    companion object {
        val DOING = mapOf("click" to "click", "close_tab" to "close the tab", "drag" to "drag", "hover" to "hover",
            "key" to "press the key", "navigate" to "open the page", "new_tab" to "open a tab", "scroll" to "scroll",
            "select_tab" to "switch tabs", "type" to "type", "answer_prompt" to "answer", "resize" to "resize the page")
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
    val bot = remember(sid, name) { JSONObject().put("id", sid).put("name", name) }
    val watch = remember(hostId, sid) { Watch(hostId, bot) }
    var said by remember { mutableStateOf<String?>(null) }
    val calls = remember(hostId, sid) { Calls(hostId, bot) { said = it } }
    val prefs = ctx.getSharedPreferences("browser", Context.MODE_PRIVATE)
    var readOnly by remember(sid) { mutableStateOf(prefs.getBoolean("ro:$sid", false)) }
    var picture by remember { mutableStateOf(IntSize.Zero) }
    var typing by remember { mutableStateOf(false) }
    DisposableEffect(here, hostId, sid) {
        if (here) watch.start()
        onDispose { watch.stop() }
    }
    LaunchedEffect(picture) { // (its size told once it's settled)
        if (picture.width == 0) return@LaunchedEffect
        delay(250)
        watch.resized(picture)
    }
    val state = watch.state.value
    val tab = state?.optString("selected")?.ifBlank { null }
    val now = state?.optJSONObject("now")
    val agent = state?.optBoolean("agent") == true
    LaunchedEffect(agent, readOnly, picture) { // (the page made the picture's size, while the Bot's own browser's watched)
        if (!agent || picture.width == 0) return@LaunchedEffect
        delay(300)
        if (readOnly) calls.call(JSONObject().put("op", "resize").put("restore", true))
        else with(density) {
            calls.call(JSONObject().put("op", "resize").put("width", picture.width.toDp().value.toInt().coerceAtLeast(200))
                .put("height", picture.height.toDp().value.toInt().coerceAtLeast(200)).put("fit", true))
        }
    }
    fun call(o: JSONObject) {
        if (readOnly) return
        said = null
        if (tab != null && !o.has("tab") && o.optString("op") !in setOf("new_tab", "answer_prompt", "resize")) o.put("tab", tab)
        calls.call(o)
    }
    Mesh.tick.value
    val s = Mesh.session(hostId, sid)
    val acting = s?.optBoolean("acting") == true && s.optString("state") == "working"
    Column(Modifier.fillMaxSize()) {
        // the toolbar: back, forward, reload (stop while it loads), the address, a new tab, typing, read-only
        Row(Modifier.fillMaxWidth().padding(start = 6.dp, end = 6.dp, top = 4.dp, bottom = 4.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(2.dp)) {
            val on = !readOnly && watch.status.value == "watching"
            IconButton(Icons.AutoMirrored.Filled.ArrowBack, "Back", enabled = on && now?.optBoolean("back") != false) {
                call(JSONObject().put("op", "navigate").put("url", "back")) }
            IconButton(Icons.AutoMirrored.Filled.ArrowForward, "Forward", enabled = on && now?.optBoolean("forward") == true) {
                call(JSONObject().put("op", "navigate").put("url", "forward")) }
            val loading = now?.optBoolean("loading") == true
            IconButton(if (loading) Icons.Filled.Close else Icons.Filled.Refresh, if (loading) "Stop" else "Reload", enabled = on) {
                call(JSONObject().put("op", "navigate").put("url", "reload")) }
            Address(now?.optString("url").orEmpty(), on, Modifier.weight(1f)) { call(JSONObject().put("op", "navigate").put("url", it)) }
            IconButton(Icons.Filled.Add, "New tab", enabled = on) { call(JSONObject().put("op", "new_tab").put("url", "about:blank")) }
            IconButton(Icons.Filled.Keyboard, "Type into the page", enabled = on,
                background = if (typing) c.fg.copy(alpha = 0.10f) else null) { typing = !typing }
            IconButton(if (readOnly) Icons.Filled.Lock else Icons.Filled.LockOpen, "Read-only",
                background = if (readOnly) c.fg.copy(alpha = 0.10f) else null) {
                readOnly = !readOnly
                prefs.edit().putBoolean("ro:$sid", readOnly).apply()
            }
        }
        val tabs = state?.optJSONArray("tabs").list()
        if (tabs.size > 1) Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())
            .padding(start = 6.dp, end = 6.dp, bottom = 6.dp), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            for (t in tabs) {
                val id = t.optString("tab")
                val picked = id == tab
                Row(Modifier.clip(RoundedCornerShape(8.dp)).background(if (picked) c.fg.copy(alpha = 0.10f) else c.frame)
                    .border(1.dp, c.rule, RoundedCornerShape(8.dp)), verticalAlignment = Alignment.CenterVertically) {
                    val label = t.optString("title").ifBlank { t.optString("url") }.ifBlank { "New tab" }
                    Text(if (label.length > 28) label.take(27) + "…" else label, style = Type.caption, color = c.fg, maxLines = 1,
                        modifier = Modifier.clickable { call(JSONObject().put("op", "select_tab").put("tab", id)) }
                            .padding(start = 10.dp, end = 2.dp, top = 6.dp, bottom = 6.dp).widthIn(max = 180.dp))
                    IconButton(Icons.Filled.Close, "Close tab") { call(JSONObject().put("op", "close_tab").put("tab", id)) }
                }
            }
        }
        for (p in state?.optJSONArray("prompts").list()) PromptCard(p) { accept, text ->
            call(JSONObject().put("op", "answer_prompt").put("id", p.optString("id")).put("accept", accept).apply {
                if (text != null) put("text", text) })
        }
        if (typing) TypeRow(!readOnly) { call(it) }
        // the page: inside the frame, the picture letterboxed in it
        Box(Modifier.fillMaxSize().padding(start = Gap.inside, end = Gap.inside, bottom = Gap.inside)
            .border(if (acting) 2.dp else 1.dp, if (acting) c.accentBg else c.rule).background(c.inside)
            .padding(if (acting) 2.dp else 1.dp).onSizeChanged { picture = it }) {
            val bmp = watch.frame.value
            if (bmp == null) {
                when (watch.status.value) {
                    "failed" -> StatusPage(Icons.Filled.Web, "Couldn't open the browser", watch.why.value)
                    "stopped" -> StatusPage(Icons.Filled.Web, "The browser stopped", "It restarts when you open this page again.")
                    else -> StatusPage(Icons.Filled.Web, if (here) "Connecting…" else "No pages open")
                }
                return@Box
            }
            Image(bmp, now?.optString("title"), contentScale = ContentScale.Fit, modifier = Modifier.fillMaxSize()
                .pointerTaps(bmp, picture, state, { readOnly }) { call(it) })
            if (watch.status.value == "stopped") Text("The browser stopped. It restarts when you open this page again.",
                style = Type.caption, color = c.dim, modifier = Modifier.align(Alignment.BottomCenter)
                    .background(c.inside).padding(start = 8.dp, end = 8.dp, bottom = 4.dp))
        }
        said?.let { Text(it, style = Type.caption, color = c.dim, modifier = Modifier.padding(horizontal = 10.dp, vertical = 2.dp)) }
    }
}

/** Where a point on the picture is on the page, in its CSS pixels (as the pane works it out: the frame letterboxed in
 * the picture, then the page's own size over the frame's); null outside the frame. */
private fun css(p: Offset, bmp: ImageBitmap, picture: IntSize, state: JSONObject?): Pair<Double, Double>? {
    val fw = bmp.width.toDouble()
    val fh = bmp.height.toDouble()
    val s = minOf(picture.width / fw, picture.height / fh)
    val ox = (picture.width - fw * s) / 2
    val oy = (picture.height - fh * s) / 2
    val fx = (p.x - ox) / s
    val fy = (p.y - oy) / s
    if (fx < 0 || fy < 0 || fx > fw || fy > fh) return null
    val view = state?.optJSONArray("view")
    val vw = view?.optDouble(0, 0.0)?.takeIf { it > 0 } ?: fw
    val vh = view?.optDouble(1, 0.0)?.takeIf { it > 0 } ?: fh
    return Math.round(fx * vw / fw * 10) / 10.0 to Math.round(fy * vh / fh * 10) / 10.0
}

/** A tap clicks (twice, a double click; held, the right button), a drag scrolls the page (sent every 50 ms). */
private fun Modifier.pointerTaps(bmp: ImageBitmap, picture: IntSize, state: JSONObject?, readOnly: () -> Boolean,
                                 call: (JSONObject) -> Unit): Modifier = this
    .pointerInput(bmp.width, bmp.height, picture, state) {
        fun click(p: Offset, press: Int, button: String) {
            if (readOnly()) return
            val (x, y) = css(p, bmp, picture, state) ?: return
            call(JSONObject().put("op", "click").put("x", x).put("y", y).put("css", true).put("press", press)
                .put("quick", true).put("button", button))
        }
        detectTapGestures(onTap = { click(it, 1, "left") }, onDoubleTap = { click(it, 2, "left") },
            onLongPress = { click(it, 1, "right") })
    }
    .pointerInput(bmp.width, bmp.height, picture, state) {
        var at: Pair<Double, Double>? = null
        var dx = 0.0
        var dy = 0.0
        var last = 0L
        val fw = bmp.width.toDouble()
        val view = state?.optJSONArray("view")
        val k = (view?.optDouble(0, 0.0)?.takeIf { it > 0 } ?: fw) / fw / minOf(picture.width / fw, picture.height / bmp.height.toDouble())
        fun flush() {
            val p = at ?: return
            if (dx == 0.0 && dy == 0.0) return
            call(JSONObject().put("op", "scroll").put("x", p.first).put("y", p.second).put("css", true)
                .put("dx", Math.round(dx).toInt()).put("dy", Math.round(dy).toInt()).put("quick", true))
            dx = 0.0
            dy = 0.0
            last = System.currentTimeMillis()
        }
        detectDragGestures(onDragStart = { at = if (readOnly()) null else css(it, bmp, picture, state) },
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
        if (text.isEmpty()) Text("Search or type an address", style = Type.body, color = c.dim, maxLines = 1,
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
                when {
                    now.startsWith(text) && now.length > text.length -> {
                        val added = now.substring(text.length)
                        if (added == " ") call(JSONObject().put("op", "key").put("keys", "Space"))
                        else call(JSONObject().put("op", "type").put("text", added).put("anywhere", true))
                    }
                    text.startsWith(now) -> repeat(text.length - now.length) { call(JSONObject().put("op", "key").put("keys", "Backspace")) }
                }
                text = now
            }, enabled = enabled, singleLine = true, textStyle = Type.body.copy(color = c.fg), cursorBrush = SolidColor(c.accentBg),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { call(JSONObject().put("op", "key").put("keys", "Enter")); text = "" }),
                modifier = Modifier.fillMaxWidth().focusRequester(focus))
        }
        IconButton(Icons.Filled.Backspace, "Backspace", enabled = enabled) { call(JSONObject().put("op", "key").put("keys", "Backspace")) }
        IconButton(Icons.AutoMirrored.Filled.KeyboardReturn, "Enter", enabled = enabled) {
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
            "sign_in" -> "${p.optString("host").ifBlank { "A site" }} asks to sign in (“${p.optString("realm")}”)"
            "files" -> "The page asks for files to upload"
            else -> p.optString("message")
        }, style = Type.body, color = c.fg)
        if (kind == "prompt") Field(text, { text = it }, "")
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            if (kind != "alert") Button("Cancel") { answer(false, null) }
            if (kind != "files") Button("OK", suggested = true) { answer(true, if (kind == "prompt") text else null) }
        }
    }
}
