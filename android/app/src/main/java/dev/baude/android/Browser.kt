package dev.baude.android

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.os.Message
import android.os.SystemClock
import android.view.InputDevice
import android.view.KeyCharacterMap
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.ViewGroup
import android.webkit.ConsoleMessage
import android.webkit.CookieManager
import android.webkit.GeolocationPermissions
import android.webkit.HttpAuthHandler
import android.webkit.JsPromptResult
import android.webkit.JsResult
import android.webkit.PermissionRequest
import android.webkit.RenderProcessGoneDetail
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.min
import kotlin.math.roundToInt

/** A tab: its page (a WebView of its own), what it's said in its console, the sites it's been to. */
class Tab(val id: String, val web: WebView) {
    private val random = java.security.SecureRandom()
    fun nonce() = ByteArray(12).also { random.nextBytes(it) }.joinToString("") { "%02x".format(it) }
    var key = nonce()    // (where em's kept in its page now: made again for each page it loads)
    var secret = nonce() // (what em's answers carry, so one that isn't em's is seen)
    var loading = false
    var progress = 0
    var loads = 0 // (pages started in it: a script's answer lost to one is seen)
    var closed = false
    val console = ArrayList<JSONObject>()
    val visited = LinkedHashSet<String>() // (the origins it's been to: what sites lists, if they've cookies)
    val title: String get() = web.title?.takeIf { it != web.url && it != "about:blank" } ?: ""
    val url: String get() = web.url ?: ""
    val label: String get() = web.title?.ifBlank { null } ?: web.url?.ifBlank { null } ?: "New Tab" // (its tab's, as Emission's)
}

/** A tab as its strip shows it. */
data class Shown(val id: String, val label: String, val loading: Boolean)

/** A page's prompt waiting on the Bot (prompts, answer_prompt): what it is, and its answer given what's said. */
class Waiting(val about: JSONObject, val tab: Tab?, val answer: (JSONObject) -> Unit)

/** Where the Bot's pages are drawn: its tabs' WebViews, the one in front shown, its pointer over them; at the Bot's
 * own size (resize), shown shrunk to fit, else the screen's. */
class Stage(ctx: Context, accent: Int) : FrameLayout(ctx) {
    val pages = FrameLayout(ctx)
    val overlay = PointerOverlay(ctx, accent)
    var touched: () -> Unit = {} // (a person's touch on the page: not the Bot's, which goes straight to its WebView)
    var size: Pair<Int, Int>? = null // (the page's size the Bot asked for, CSS pixels: none, the screen's)
        set(value) {
            field = value
            requestLayout()
        }

    init {
        pages.addView(overlay, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT))
        addView(pages, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT))
    }

    override fun dispatchTouchEvent(ev: android.view.MotionEvent): Boolean {
        if (ev.actionMasked == android.view.MotionEvent.ACTION_DOWN) touched()
        return super.dispatchTouchEvent(ev)
    }

    fun show(web: WebView) {
        if (web.parent == null) pages.addView(web, pages.childCount - 1,
            LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT))
        for (i in 0 until pages.childCount) {
            val v = pages.getChildAt(i)
            if (v is WebView) v.visibility = if (v === web) View.VISIBLE else View.INVISIBLE
        }
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val w = MeasureSpec.getSize(widthMeasureSpec)
        val h = MeasureSpec.getSize(heightMeasureSpec)
        val want = size
        val d = resources.displayMetrics.density
        val pw = want?.let { (it.first * d).toInt() } ?: w
        val ph = want?.let { (it.second * d).toInt() } ?: h
        pages.measure(MeasureSpec.makeMeasureSpec(pw, MeasureSpec.EXACTLY), MeasureSpec.makeMeasureSpec(ph, MeasureSpec.EXACTLY))
        val fit = if (want == null) 1f else min(1f, min(w.toFloat() / pw, h.toFloat() / ph))
        pages.pivotX = 0f
        pages.pivotY = 0f
        pages.scaleX = fit
        pages.scaleY = fit
        setMeasuredDimension(w, h)
    }

    override fun onLayout(changed: Boolean, l: Int, t: Int, r: Int, b: Int) {
        val shownW = pages.measuredWidth * pages.scaleX
        val left = ((r - l - shownW) / 2).roundToInt().coerceAtLeast(0) // (shrunk: in the middle)
        pages.layout(left, 0, left + pages.measuredWidth, pages.measuredHeight)
    }
}

/** The Bot's browser on this phone: its tabs (phone1, phone2, …: their own ids, apart from a desktop Emission's), what
 * its pages wait on, the downloads they've asked for. early: what's put in each page as it starts loading. */
class Browser(private val ctx: Context, val profile: String, val stage: Stage) {
    var early: ((Tab) -> String)? = null // (made for each page as it loads: its own em, key and secret)
    var changed: (Tab, String) -> Unit = { _, _ -> } // (a tab opened or closed: by a page, or anyone)
    val tabs = LinkedHashMap<String, Tab>()
    var current: Tab? = null
    val shownUrl = mutableStateOf("")       // (the tab in front's address, for the bar)
    val count = mutableStateOf(0)           // (how many tabs)
    val strip = mutableStateListOf<Shown>() // (its tabs, as their strip shows them)
    val selected = mutableStateOf<String?>(null)
    val canBack = mutableStateOf(false)
    val canForward = mutableStateOf(false)
    val loading = mutableStateOf(false)     // (the tab in front's: its reload a stop, its progress shown)
    val progress = mutableStateOf(0f)
    val waiting = LinkedHashMap<String, Waiting>()
    val asking = mutableStateListOf<String>() // (what pages wait on the Bot for, said on screen)
    val downloads = ArrayList<JSONObject>()
    private var made = 0
    private val main = Handler(Looper.getMainLooper())

    init {
        main.post(object : Runnable { // (the bars as the pages change: Emission's POLL_MS)
            override fun run() {
                shown()
                main.postDelayed(this, 200)
            }
        })
    }

    private fun shown() {
        val t = current
        shownUrl.value = t?.url ?: ""
        canBack.value = t?.web?.canGoBack() ?: false
        canForward.value = t?.web?.canGoForward() ?: false
        loading.value = t?.loading ?: false
        progress.value = (t?.progress ?: 0) / 100f
        selected.value = t?.id
        val now = tabs.values.map { Shown(it.id, it.label, it.loading) }
        if (now != strip.toList()) {
            strip.clear()
            strip.addAll(now)
        }
    }

    fun open(url: String?, transport: WebView.WebViewTransport? = null): Tab {
        made += 1
        val t = Tab("phone$made", WebView(ctx))
        setUp(t)
        tabs[t.id] = t
        count.value = tabs.size
        select(t)
        if (transport != null) transport.webView = t.web
        else if (url != null) t.web.loadUrl(url)
        return t
    }

    fun select(t: Tab) {
        current = t
        stage.show(t.web)
        shown()
        stage.overlay.shownTab = t.id
        stage.overlay.invalidate()
        shownUrl.value = t.url
    }

    fun close(t: Tab) {
        val at = tabs.values.indexOf(t)
        t.closed = true
        tabs.remove(t.id)
        count.value = tabs.size
        for ((id, w) in waiting.toList()) if (w.tab === t) {
            waiting.remove(id)
            w.answer(JSONObject().put("accept", false))
        }
        said()
        (t.web.parent as? ViewGroup)?.removeView(t.web)
        t.web.destroy()
        if (current === t) { // (its neighbour in front, as Adw.TabView has it: the one after it, or before if it was last)
            val left = tabs.values.toList()
            (left.getOrNull(at) ?: left.lastOrNull())?.let { select(it) } ?: run { current = null }
        }
        changed(t, "closed")
    }

    fun prompts(t: Tab?): List<JSONObject> = waiting.values.filter { t == null || it.tab === t }.map { it.about }

    fun answerPrompt(msg: JSONObject) {
        val id = msg.optString("id")
        val w = waiting.remove(id) ?: (if (waiting.size == 1 && id.isEmpty()) waiting.remove(waiting.keys.first()) else null)
            ?: throw IllegalArgumentException("no such prompt waiting: prompts lists them")
        said()
        w.answer(msg)
    }

    private fun waits(t: Tab?, kind: String, answer: (JSONObject) -> Unit, about: JSONObject.() -> Unit): String {
        val pid = "prompt${waiting.size + 1}-${SystemClock.uptimeMillis() % 100000}"
        val a = JSONObject().put("id", pid).put("tab", t?.id ?: JSONObject.NULL).put("kind", kind).apply(about)
        waiting[pid] = Waiting(a, t, answer)
        said()
        return pid
    }

    private fun said() {
        asking.clear()
        for (w in waiting.values) asking += w.about.optString("kind").let {
            when (it) {
                "sign_in" -> "A site asks to sign in: ${w.about.optString("host")}"
                "files" -> "A page asks for files"
                else -> "The page's $it: ${w.about.optString("message").take(120)}"
            }
        }
    }

    /** The sites its tabs have been to that it has cookies for now (signed in to, as like as not). */
    fun sites(): List<String> {
        val out = sortedSetOf<String>()
        val cookies = CookieManager.getInstance()
        for (t in tabs.values) for (origin in t.visited) {
            if (cookies.getCookie(origin).isNullOrBlank()) continue
            val host = Uri.parse(origin).host?.lowercase() ?: continue
            val parts = host.split(".")
            out += if (parts.size > 2 && parts[parts.size - 2].length <= 3) parts.takeLast(3).joinToString(".")
            else parts.takeLast(2).joinToString(".")
        }
        return out.toList()
    }

    private fun tabOf(web: WebView?): Tab? = tabs.values.firstOrNull { it.web === web }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setUp(t: Tab) {
        val web = t.web
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            setSupportZoom(false) // (the page at its own scale: what the Bot's pixels are, its pointer at them)
            builtInZoomControls = false
            useWideViewPort = false
            loadWithOverviewMode = false
            setSupportMultipleWindows(true)
            javaScriptCanOpenWindowsAutomatically = false
            allowFileAccess = false
            allowContentAccess = false
            setGeolocationEnabled(false)
        }
        web.importantForAutofill = View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS
        web.isFocusable = true
        web.isFocusableInTouchMode = true
        web.setDownloadListener { url, _, disposition, mime, _ ->
            downloads += JSONObject().put("name", URLUtil.guessFileName(url, disposition, mime)).put("path", JSONObject.NULL)
                .put("error", "downloads aren't kept on the phone")
        }
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean =
                request.url.scheme?.lowercase() !in listOf("http", "https", "data", "about", "blob", "javascript")

            override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
                t.key = t.nonce()
                t.secret = t.nonce()
                early?.let { view.evaluateJavascript(it(t), null) }
                t.loading = true
                t.loads += 1
                url?.let { u -> Uri.parse(u).takeIf { it.scheme in listOf("http", "https") }?.let {
                    t.visited += "${it.scheme}://${it.authority}" } }
            }

            override fun onPageFinished(view: WebView, url: String?) {
                t.loading = false
                view.evaluateJavascript("try { performance.setResourceTimingBufferSize(2000) } catch (e) {}", null)
            }

            override fun onReceivedHttpAuthRequest(view: WebView, handler: HttpAuthHandler, host: String, realm: String) {
                waits(t, "sign_in", { m ->
                    if (m.optBoolean("accept", true)) handler.proceed(m.optString("user"), m.optString("password"))
                    else handler.cancel()
                }) { put("host", host).put("realm", realm).put("failed_before", false) }
            }

            override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
                main.post { if (!t.closed) close(t) } // (its page's process gone: the tab with it, not the app)
                return true
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView, progress: Int) {
                t.progress = progress
                t.loading = progress < 100
            }

            override fun onConsoleMessage(m: ConsoleMessage): Boolean {
                val level = when (m.messageLevel()) {
                    ConsoleMessage.MessageLevel.ERROR -> "error"
                    ConsoleMessage.MessageLevel.WARNING -> "warn"
                    ConsoleMessage.MessageLevel.DEBUG, ConsoleMessage.MessageLevel.TIP -> "debug"
                    else -> "log"
                }
                val where = if (level == "error" && !m.sourceId().isNullOrBlank()) " (${m.sourceId()}:${m.lineNumber()})" else ""
                t.console += JSONObject().put("level", level).put("text", (m.message() + where).take(2000))
                    .put("at", System.currentTimeMillis())
                if (t.console.size > 300) t.console.removeAt(0)
                return true
            }

            override fun onJsAlert(view: WebView, url: String?, message: String, result: JsResult): Boolean {
                waits(t, "alert", { result.confirm() }) { put("message", message) }
                return true
            }

            override fun onJsConfirm(view: WebView, url: String?, message: String, result: JsResult): Boolean {
                waits(t, "confirm", { m -> if (m.optBoolean("accept", true)) result.confirm() else result.cancel() }) {
                    put("message", message)
                }
                return true
            }

            override fun onJsPrompt(view: WebView, url: String?, message: String, default: String?, result: JsPromptResult): Boolean {
                waits(t, "prompt", { m -> if (m.optBoolean("accept", true)) result.confirm(m.optString("text")) else result.cancel() }) {
                    put("message", message).put("default", default ?: "")
                }
                return true
            }

            override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean {
                waits(t, "files", { callback.onReceiveValue(null) }) {
                    put("multiple", params.mode == FileChooserParams.MODE_OPEN_MULTIPLE).put("folders", false)
                        .put("note", "files can't be uploaded from the phone: answer it to cancel")
                }
                return true
            }

            override fun onCreateWindow(view: WebView, isDialog: Boolean, isUserGesture: Boolean, resultMsg: Message): Boolean {
                val transport = resultMsg.obj as? WebView.WebViewTransport ?: return false
                val made = open(null, transport)
                resultMsg.sendToTarget()
                changed(made, "opened")
                return true
            }

            override fun onCloseWindow(window: WebView) {
                tabOf(window)?.let { close(it) }
            }

            override fun onPermissionRequest(request: PermissionRequest) = request.deny()

            override fun onGeolocationPermissionsShowPrompt(origin: String, callback: GeolocationPermissions.Callback) =
                callback.invoke(origin, false, false)
        }
    }
}

/** Real input to a WebView, as Android delivers it from a mouse and a keyboard plugged into the phone: a mouse's
 * presses, moves and wheel (its pointer's events, isTrusted, as a person's), a keyboard's keys. x, y: the view's
 * pixels. */
object Input {
    const val PRESS_MS = 40L  // (a click's press held this long)
    const val CLICKS_MS = 90L // (between a double click's)
    private val main = Handler(Looper.getMainLooper())

    private fun mouse(action: Int, x: Float, y: Float, buttons: Int, meta: Int, down: Long): MotionEvent {
        val props = MotionEvent.PointerProperties().apply { id = 0; toolType = MotionEvent.TOOL_TYPE_MOUSE }
        val coords = MotionEvent.PointerCoords().apply {
            this.x = x
            this.y = y
            pressure = if (buttons != 0) 1f else 0f
            size = 1f
        }
        return MotionEvent.obtain(down, SystemClock.uptimeMillis(), action, 1, arrayOf(props), arrayOf(coords), meta,
            buttons, 1f, 1f, 0, 0, InputDevice.SOURCE_MOUSE, 0)
    }

    private fun send(web: View, e: MotionEvent, generic: Boolean) {
        if (generic) web.dispatchGenericMotionEvent(e) else web.dispatchTouchEvent(e)
        e.recycle()
    }

    private val setActionButton by lazy {
        try {
            MotionEvent::class.java.getMethod("setActionButton", Int::class.javaPrimitiveType)
        } catch (_: Exception) {
            null
        }
    }

    /** A mouse button pressed (down) or let go, as Android delivers one: the pointer's ACTION_DOWN or UP, and the
     * button's own press or release (which is what Chromium takes a page's mousedown and mouseup from). */
    private fun button(web: View, x: Float, y: Float, button: Int, meta: Int, downAt: Long, down: Boolean) {
        val held = if (down) button else 0
        if (down) send(web, mouse(MotionEvent.ACTION_DOWN, x, y, held, meta, downAt), false)
        val e = mouse(if (down) MotionEvent.ACTION_BUTTON_PRESS else MotionEvent.ACTION_BUTTON_RELEASE, x, y, held, meta, downAt)
        setActionButton?.invoke(e, button)
        send(web, e, true)
        if (!down) send(web, mouse(MotionEvent.ACTION_UP, x, y, 0, meta, downAt), false)
    }

    fun meta(mods: List<String>): Int = mods.fold(0) { m, k ->
        m or when (k) {
            "shift" -> KeyEvent.META_SHIFT_ON or KeyEvent.META_SHIFT_LEFT_ON
            "ctrl", "cmd" -> KeyEvent.META_CTRL_ON or KeyEvent.META_CTRL_LEFT_ON // (cmd: Control, as on Linux)
            "alt" -> KeyEvent.META_ALT_ON or KeyEvent.META_ALT_LEFT_ON
            else -> 0
        }
    }

    fun move(web: View, x: Float, y: Float) {
        val now = SystemClock.uptimeMillis()
        send(web, mouse(MotionEvent.ACTION_HOVER_MOVE, x, y, 0, 0, now), true)
    }

    /** count clicks of button at x, y, done() once they're let go. */
    fun click(web: View, x: Float, y: Float, button: String, count: Int, mods: List<String>, done: () -> Unit) {
        val b = when (button) {
            "right" -> MotionEvent.BUTTON_SECONDARY
            "middle" -> MotionEvent.BUTTON_TERTIARY
            else -> MotionEvent.BUTTON_PRIMARY
        }
        val m = meta(mods)
        move(web, x, y)
        fun one(left: Int) {
            val down = SystemClock.uptimeMillis()
            button(web, x, y, b, m, down, true)
            main.postDelayed({
                button(web, x, y, b, m, down, false)
                if (left > 1) main.postDelayed({ one(left - 1) }, CLICKS_MS) else done()
            }, PRESS_MS)
        }
        one(count.coerceIn(1, 3))
    }

    /** A press at path's first point, moved along it, let go at its last. */
    fun drag(web: View, path: List<Pair<Float, Float>>, done: () -> Unit) {
        val down = SystemClock.uptimeMillis()
        val (x0, y0) = path[0]
        move(web, x0, y0)
        button(web, x0, y0, MotionEvent.BUTTON_PRIMARY, 0, down, true)
        val points = ArrayList<Pair<Float, Float>>()
        for ((a, b) in path.zipWithNext()) { // (each leg in steps of about 10 pixels, as a hand moves one)
            val n = (kotlin.math.hypot(b.first - a.first, b.second - a.second) / 10f).roundToInt().coerceIn(1, 60)
            for (i in 1..n) points += (a.first + (b.first - a.first) * i / n) to (a.second + (b.second - a.second) * i / n)
        }
        fun step(i: Int) {
            if (i >= points.size) {
                val (x, y) = path.last()
                button(web, x, y, MotionEvent.BUTTON_PRIMARY, 0, down, false)
                return done()
            }
            val (x, y) = points[i]
            send(web, mouse(MotionEvent.ACTION_MOVE, x, y, MotionEvent.BUTTON_PRIMARY, 0, down), false)
            main.postDelayed({ step(i + 1) }, 12)
        }
        main.postDelayed({ step(0) }, 30)
    }

    /** The wheel turned over x, y: dx, dy the page's pixels (k view pixels each). */
    fun wheel(web: View, x: Float, y: Float, dx: Int, dy: Int, k: Float) {
        val factor = ViewConfiguration.get(web.context).let { it.scaledVerticalScrollFactor to it.scaledHorizontalScrollFactor }
        move(web, x, y)
        val now = SystemClock.uptimeMillis()
        val props = MotionEvent.PointerProperties().apply { id = 0; toolType = MotionEvent.TOOL_TYPE_MOUSE }
        val coords = MotionEvent.PointerCoords().apply {
            this.x = x
            this.y = y
            setAxisValue(MotionEvent.AXIS_VSCROLL, -dy * k / factor.first) // (up's positive, as a wheel turns)
            setAxisValue(MotionEvent.AXIS_HSCROLL, dx * k / factor.second)
        }
        send(web, MotionEvent.obtain(now, now, MotionEvent.ACTION_SCROLL, 1, arrayOf(props), arrayOf(coords), 0, 0, 1f, 1f,
            0, 0, InputDevice.SOURCE_MOUSE, 0), true)
    }

    private val KEYS = mapOf("Enter" to KeyEvent.KEYCODE_ENTER, "Tab" to KeyEvent.KEYCODE_TAB,
        "Escape" to KeyEvent.KEYCODE_ESCAPE, "Backspace" to KeyEvent.KEYCODE_DEL, "Delete" to KeyEvent.KEYCODE_FORWARD_DEL,
        "Space" to KeyEvent.KEYCODE_SPACE, "Up" to KeyEvent.KEYCODE_DPAD_UP, "Down" to KeyEvent.KEYCODE_DPAD_DOWN,
        "Left" to KeyEvent.KEYCODE_DPAD_LEFT, "Right" to KeyEvent.KEYCODE_DPAD_RIGHT, "Home" to KeyEvent.KEYCODE_MOVE_HOME,
        "End" to KeyEvent.KEYCODE_MOVE_END, "PageUp" to KeyEvent.KEYCODE_PAGE_UP, "PageDown" to KeyEvent.KEYCODE_PAGE_DOWN) +
        (1..12).associate { "F$it" to KeyEvent.KEYCODE_F1 + it - 1 }

    private val keyboard by lazy { KeyCharacterMap.load(KeyCharacterMap.VIRTUAL_KEYBOARD) }

    private fun key(web: View, code: Int, meta: Int) {
        val now = SystemClock.uptimeMillis()
        for (action in listOf(KeyEvent.ACTION_DOWN, KeyEvent.ACTION_UP)) web.dispatchKeyEvent(
            KeyEvent(now, SystemClock.uptimeMillis(), action, code, 0, meta, KeyCharacterMap.VIRTUAL_KEYBOARD, 0,
                KeyEvent.FLAG_FROM_SYSTEM, InputDevice.SOURCE_KEYBOARD))
    }

    /** A key pressed with mods held (a name, or a character a key makes). */
    fun press(web: View, k: String, mods: List<String>) {
        web.requestFocus()
        val m = meta(mods)
        KEYS[k]?.let { return key(web, it, m) }
        if (k.length == 1) {
            val events = keyboard.getEvents(k.lowercase().toCharArray())
            val code = events?.firstOrNull { it.action == KeyEvent.ACTION_DOWN && !KeyEvent.isModifierKey(it.keyCode) }?.keyCode
            if (code != null) return key(web, code, m or (if (k[0].isUpperCase()) KeyEvent.META_SHIFT_ON else 0))
        }
        throw IllegalArgumentException("no key $k: Enter, Tab, Escape, Backspace, Delete, Up, Down, Left, Right, Home, End, " +
            "PageUp, PageDown, F1 to F12, or a character")
    }

    /** A character typed as a keyboard types it: false if no key makes it. */
    fun type(web: View, ch: Char): Boolean {
        web.requestFocus()
        val events = keyboard.getEvents(charArrayOf(ch)) ?: return false
        for (e in events) web.dispatchKeyEvent(KeyEvent(e.downTime, SystemClock.uptimeMillis(), e.action, e.keyCode, 0,
            e.metaState, KeyCharacterMap.VIRTUAL_KEYBOARD, 0, KeyEvent.FLAG_FROM_SYSTEM, InputDevice.SOURCE_KEYBOARD))
        return true
    }

    /** What the WebView shows now, its view's x, y, w by h pixels, as an outW by outH picture (on white: a JPEG's). Its
     * own drawing (WebView's), so what's over it (the Bot's pointer) isn't in it. */
    fun picture(web: WebView, x: Float, y: Float, w: Float, h: Float, outW: Int, outH: Int): Bitmap {
        val bm = Bitmap.createBitmap(outW, outH, Bitmap.Config.ARGB_8888)
        val c = Canvas(bm)
        c.drawColor(android.graphics.Color.WHITE)
        c.scale(outW / w, outH / h)
        c.translate(-x - web.scrollX, -y - web.scrollY)
        web.draw(c)
        return bm
    }
}

fun JSONArray?.strings(): List<String> = if (this == null) emptyList() else (0 until length()).map { optString(it) }
