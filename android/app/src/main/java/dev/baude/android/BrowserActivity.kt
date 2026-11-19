package dev.baude.android

import android.net.LocalSocket
import android.net.LocalSocketAddress
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.Process
import android.os.SystemClock
import android.view.WindowManager
import android.view.inputmethod.InputMethodManager
import android.webkit.WebView
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Text
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import java.nio.ByteBuffer
import java.security.MessageDigest
import java.util.UUID
import kotlin.concurrent.thread

/** A Bot's browser on this phone (PhoneBrowser's), on screen while it's used, as an Emission window is (emission.Chrome's
 * bars on the frame, the page set into it: widgets.inside), its pointer over the page and the glow, as Baude's Browser
 * page shows a Bot's Emission. You can use it too: what you do by hand is told to the Bot with its next request, as
 * Emission tells it its browser changed. In a process of its own, so its pages are kept apart from every other Bot's
 * (WebView's data, by the Bot's profile: emissionlink.bot_profile_id's); ended, and that process with it, once it's
 * closed (the phone's back) or the phone leaves it (another app in front, or the screen locked). */
class BrowserActivity : ComponentActivity() {
    private val main = Handler(Looper.getMainLooper())
    private lateinit var browser: Browser
    private lateinit var control: Control
    private lateinit var stage: Stage
    private var link: LocalSocket? = null
    private var noted = false // (the Bot told its browser's this phone's, since it's been served here this time)
    private val hand = LinkedHashSet<String>() // (what you've done by hand since the Bot's last answer)
    private var touchedAt = 0L
    private var sessionActing = false
    private var busy by mutableIntStateOf(0)
    private var serving by mutableStateOf<Boolean?>(null) // (whether its host carries the Bot's requests here)
    private var why by mutableStateOf<String?>(null)
    private var made = false

    override fun onCreate(savedInstanceState: Bundle?) {
        val bot = intent.getStringExtra("bot")
        val profile = bot?.let(::botProfileId)
        if (profile != null && !KEEP) forget(this, profile) // (before WebView's started here: nothing of before)
        val suffixed = profile != null && (Build.VERSION.SDK_INT < 28 || try {
            WebView.setDataDirectorySuffix("emission-$profile")
            true
        } catch (_: IllegalStateException) { // (this process's WebView started for another Bot: not shown it)
            false
        })
        super.onCreate(savedInstanceState)
        if (bot == null || !suffixed) return finish()
        made = true
        val name = intent.getStringExtra("name") ?: "Bot"
        enableEdgeToEdge()
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        stage = Stage(this, ACCENT)
        stage.overlay.k = resources.displayMetrics.density
        browser = Browser(this, name, stage)
        control = Control(browser, assets.open("emission/page.js").reader().readText(),
            assets.open("emission/repl.js").reader().readText(), stage.overlay.pointer) { stage.overlay.kick() }
        browser.early = control.early
        stage.touched = {
            touchedAt = SystemClock.uptimeMillis()
            browser.current?.let { hand += "touched the page in ${it.id}" }
        }
        browser.open("about:blank")
        connect(intent.getStringExtra("socket") ?: "", intent.getStringExtra("host") ?: "", bot, name)
        val where = intent.getStringExtra("where") ?: "its host"
        setContent {
            BaudeTheme {
                val c = LocalBaude.current
                Column(Modifier.fillMaxSize().background(c.frame).navigationBarsPadding().imePadding()) {
                    EmissionBar(browser, name, go = { text -> browser.current?.let { t ->
                        val url = Control.address(text)
                        hand += "went to $url in ${t.id}"
                        t.web.loadUrl(url)
                        t.web.requestFocus()
                    } }, nav = { how -> browser.current?.let { t ->
                        when (how) {
                            "back" -> t.web.goBack()
                            "forward" -> t.web.goForward()
                            "stop" -> t.web.stopLoading()
                            else -> t.web.reload()
                        }
                        hand += mapOf("back" to "went back", "forward" to "went forward", "stop" to "stopped a page loading")
                            .getOrElse(how) { "reloaded" } + " in ${t.id}"
                    } }, newTab = {
                        val t = browser.open("about:blank")
                        hand += "opened a new tab, ${t.id}"
                    })
                    TabStrip(browser, pick = { id -> browser.tabs[id]?.let {
                        if (it !== browser.current) hand += "switched to ${it.id}"
                        browser.select(it)
                    } }, close = { id -> browser.tabs[id]?.let {
                        if (browser.tabs.size == 1) browser.open("about:blank")
                        hand += "closed ${it.id}"
                        browser.close(it)
                    } })
                    PromptCards(browser) { answer ->
                        try {
                            val kind = browser.waiting[answer.optString("id")]?.about?.optString("kind") ?: "prompt"
                            browser.answerPrompt(answer)
                            hand += "answered the page's $kind"
                        } catch (_: Exception) {
                        }
                    }
                    Box(Modifier.weight(1f).fillMaxWidth().padding(start = Gap.inside, end = Gap.inside,
                        bottom = Gap.inside).border(1.dp, c.rule).padding(1.dp).background(c.inside).clipToBounds()) {
                        AndroidView({ stage }, Modifier.fillMaxSize())
                    }
                    val said = when (serving) {
                        null -> "Connecting to $where…"
                        false -> why ?: "Can't reach $where"
                        else -> null
                    }
                    if (said != null) Text(said, style = Type.caption, color = c.dim,
                        modifier = Modifier.padding(start = 8.dp, end = 8.dp, bottom = 4.dp))
                }
            }
        }
    }

    /** This process's link to the app's (PhoneBrowser's): told which Bot's browser this is, then its requests. */
    private fun connect(socket: String, host: String, bot: String, name: String) {
        thread(name = "browser-link", isDaemon = true) {
            val sock = LocalSocket()
            try {
                sock.connect(LocalSocketAddress(socket, LocalSocketAddress.Namespace.ABSTRACT))
            } catch (_: Exception) {
                main.post { finish() }
                return@thread
            }
            link = sock
            send(JSONObject().put("op", "hello").put("host", host).put("bot", bot).put("name", name))
            try {
                BufferedReader(InputStreamReader(sock.inputStream, Charsets.UTF_8), 1 shl 16).use { r ->
                    while (true) {
                        val line = r.readLine() ?: break
                        val msg = try {
                            JSONObject(line)
                        } catch (_: Exception) {
                            continue
                        }
                        main.post { heard(msg) }
                    }
                }
            } catch (_: Exception) {
            }
            main.post { if (!isFinishing) finish() } // (the app's process gone: nothing reaches this one)
        }
    }

    private val writes = java.util.concurrent.Executors.newSingleThreadExecutor()

    private fun send(msg: JSONObject) {
        val line = (msg.toString() + "\n").toByteArray(Charsets.UTF_8)
        writes.execute {
            try {
                link?.outputStream?.let {
                    it.write(line)
                    it.flush()
                }
            } catch (_: Exception) {
            }
        }
    }

    private fun heard(msg: JSONObject) {
        when (msg.optString("op")) {
            "serving" -> {
                val on = msg.optBoolean("on")
                if (on && serving != true) noted = false // (served again: the Bot told again, its last were a host's)
                serving = on
                why = msg.optString("why").ifBlank { null }
            }
            "acting" -> {
                sessionActing = msg.optBoolean("on")
                acting()
            }
            "close" -> finish()
            "call" -> call(msg.optString("id"), msg.optJSONObject("call") ?: JSONObject())
        }
    }

    private fun acting() {
        stage.overlay.pointer.acting = sessionActing || busy > 0
        stage.overlay.kick()
    }

    /** What the Bot's told with its next answer: that its browser's this phone's (once each time it's served here),
     * and what you've done by hand since its last; or "" if nothing. */
    private fun note(): String {
        val out = ArrayList<String>()
        if (!noted) {
            val d = resources.displayMetrics.density
            out += "Your browser is on a phone now (Android's WebView, its pages laid out at the phone's size, " +
                "${(stage.width / d).toInt()} by ${(stage.height / d).toInt()}): its tabs (phone1, …) and refs " +
                "(ref_p1, …) are its own, not your desktop browser's" + (if (KEEP) "." else ", and it keeps nothing " +
                "once it's closed.") + " Its owner can use it too: what they do is said here, with your next request."
        }
        if (hand.isNotEmpty()) out += "Its owner used this browser by hand since your last request: " +
            hand.joinToString(", ") + ". What you read of it before may have changed."
        return out.joinToString(" ")
    }

    /** A Bot's tools' request: done as Emission does it, its answer sent back, with what it's to be told. */
    private fun call(id: String, call: JSONObject) {
        val op = call.optString("op")
        val started = SystemClock.uptimeMillis()
        val reply = { r: JSONObject ->
            busy -= 1
            acting()
            if (op in KEYBOARD_QUIET && touchedAt < started) { // (a field the Bot focused: no on-screen keyboard over
                getSystemService(InputMethodManager::class.java)  // its page, unless you've touched it since)
                    .hideSoftInputFromWindow(stage.windowToken, 0)
            }
            val out = JSONObject().put("op", "answer").put("id", id)
            if (r.has("error")) out.put("error", r.optString("error")) else {
                val said = note()
                if (said.isNotEmpty()) {
                    r.put("note", said)
                    noted = true
                    hand.clear()
                }
                out.put("result", r)
            }
            send(out)
        }
        busy += 1
        acting()
        control.run(op, call, reply)
    }

    override fun onStop() {
        super.onStop()
        if (!isChangingConfigurations) finish() // (left, or the screen locked: the Bot's browser is Emission's again)
    }

    override fun onDestroy() {
        super.onDestroy()
        if (!isFinishing || !made) return
        val said = if (hand.isNotEmpty()) "Before the phone closed it, its owner used it by hand: " +
            hand.joinToString(", ") + "." else ""
        val urls = browser.tabs.values.map { it.url }.filter { it.isNotEmpty() && it != "about:blank" }
        val bye = (JSONObject().put("op", "bye").put("note", said).put("urls", JSONArray(urls)).toString() + "\n")
            .toByteArray(Charsets.UTF_8)
        thread { // (said before this process ends: the app's tells the Bot's host, which tells the Bot)
            try {
                link?.outputStream?.let {
                    it.write(bye)
                    it.flush()
                }
            } catch (_: Exception) {
            }
        }.join(500)
        for (t in browser.tabs.values.toList()) t.web.destroy()
        if (KEEP) android.webkit.CookieManager.getInstance().flush()
        Process.killProcess(Process.myPid()) // (its WebView's profile this Bot's: a process of its own each time; its
        // link closed with it, and what it kept forgotten then by the app's process: PhoneBrowser)
    }

    companion object {
        /** Whether a Bot's profile here keeps what it had (cookies, sign-ins, storage) from one time it's opened to the
         * next: not, till you've chosen. */
        const val KEEP = false
        val KEYBOARD_QUIET = setOf("click", "drag", "type", "key", "form_input", "javascript") // (what can focus a field)
        const val ACCENT = 0xFF3584E4.toInt() // (Baude's accent: the pointer's and the glow's colour)
        private val PROFILE_SPACE = UUID.fromString("6b1f0d3e-3c4e-4f51-9b0e-7a1e5e6d2c11") // (emissionlink's)

        /** What's kept of a Bot's profile here, gone: every folder of WebView's named for it (its data, its caches),
         * while no WebView of it is running. */
        fun forget(ctx: android.content.Context, profile: String) {
            val mark = "emission-$profile"
            for (dir in listOf(ctx.dataDir, ctx.cacheDir, ctx.codeCacheDir, ctx.noBackupFilesDir))
                dir.listFiles()?.filter { mark in it.name }?.forEach { it.deleteRecursively() }
            if (Build.VERSION.SDK_INT < 28) { // (one WebView folder for all, then: its own, every time)
                File(ctx.dataDir, "app_webview").deleteRecursively()
                File(ctx.cacheDir, "WebView").deleteRecursively()
            }
        }

        /** A Bot's own profile's id (emissionlink.bot_profile_id: uuid5 of "bot:" and its id). */
        fun botProfileId(bot: String): String {
            val md = MessageDigest.getInstance("SHA-1")
            md.update(ByteBuffer.allocate(16).putLong(PROFILE_SPACE.mostSignificantBits)
                .putLong(PROFILE_SPACE.leastSignificantBits).array())
            md.update("bot:$bot".toByteArray(Charsets.UTF_8))
            val h = md.digest().copyOf(16)
            h[6] = ((h[6].toInt() and 0x0f) or 0x50).toByte()
            h[8] = ((h[8].toInt() and 0x3f) or 0x80).toByte()
            val bb = ByteBuffer.wrap(h)
            return UUID(bb.long, bb.long).toString()
        }
    }
}
