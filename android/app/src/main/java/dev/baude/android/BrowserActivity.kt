package dev.baude.android

import android.net.LocalSocket
import android.net.LocalSocketAddress
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.Process
import android.view.WindowManager
import android.webkit.WebView
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Public
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import org.json.JSONObject
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import java.nio.ByteBuffer
import java.security.MessageDigest
import java.util.UUID
import kotlin.concurrent.thread

/** A Bot's browser on this phone (PhoneBrowser's), on screen while it's used: its pages, its pointer over them and the
 * glow, as Baude's Browser page shows a Bot's Emission. In a process of its own, so its pages are kept apart from
 * every other Bot's (WebView's data, by the Bot's profile: emissionlink.bot_profile_id's); ended, and that process
 * with it, once it's closed or the phone leaves it (another app in front, or the screen locked). Watch only till you
 * let the Bot act: what changes a page (a click, keys, a script) is refused till then. */
class BrowserActivity : ComponentActivity() {
    private val main = Handler(Looper.getMainLooper())
    private lateinit var browser: Browser
    private lateinit var control: Control
    private lateinit var stage: Stage
    private var link: LocalSocket? = null
    private var noted = false
    private var sessionActing = false
    private var busy by mutableIntStateOf(0)
    private var serving by mutableStateOf<Boolean?>(null) // (whether its host carries the Bot's requests here)
    private var why by mutableStateOf<String?>(null)
    private var acts by mutableStateOf(false)

    override fun onCreate(savedInstanceState: Bundle?) {
        val bot = intent.getStringExtra("bot") ?: return finishAndRemoveTask()
        val name = intent.getStringExtra("name") ?: "Bot"
        val profile = botProfileId(bot)
        if (!KEEP) forget(this, profile) // (before WebView's started here: nothing of a session before this one)
        if (Build.VERSION.SDK_INT >= 28) try {
            WebView.setDataDirectorySuffix("emission-$profile")
        } catch (_: IllegalStateException) { // (this process's WebView started for another Bot: not shown it)
            return finishAndRemoveTask()
        }
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        window.addFlags(WindowManager.LayoutParams.FLAG_ALT_FOCUSABLE_IM) // (no on-screen keyboard: a field the Bot
        // focuses doesn't bring one up over its page, nor change the page's size under it; its keys come as a keyboard's)
        stage = Stage(this, ACCENT)
        stage.overlay.k = resources.displayMetrics.density
        browser = Browser(this, name, stage)
        control = Control(browser, assets.open("emission/page.js").reader().readText(),
            assets.open("emission/repl.js").reader().readText(), stage.overlay.pointer) { stage.overlay.kick() }
        browser.open("about:blank")
        connect(intent.getStringExtra("socket") ?: "", intent.getStringExtra("host") ?: "", bot, name)
        val where = intent.getStringExtra("where") ?: "its host"
        setContent {
            BaudeTheme {
                val c = LocalBaude.current
                Column(Modifier.fillMaxSize().background(c.frame)) {
                    Bar(name, subtitle = "Its browser on this phone", start = {
                        IconButton(Icons.Filled.Close, "Close") { finish() }
                    }, lead = { Avatar(name, null) })
                    Row(Modifier.fillMaxWidth().background(c.inside).padding(horizontal = 12.dp, vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Filled.Public, null, tint = c.dim, modifier = Modifier.size(14.dp))
                        Spacer(Modifier.width(6.dp))
                        Text(shown(browser.shownUrl.value), style = Type.caption, color = c.dim, maxLines = 1,
                            overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                        val n = browser.count.value
                        if (n > 1) Text("$n tabs", style = Type.caption, color = c.dim)
                    }
                    for (asked in browser.asking) GreyBanner(asked)
                    Box(Modifier.weight(1f).fillMaxWidth().background(c.inside)) {
                        AndroidView({ stage }, Modifier.fillMaxSize())
                    }
                    Row(Modifier.fillMaxWidth().background(c.frame).navigationBarsPadding()
                        .padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                        val status = when {
                            serving == null -> "Connecting to $where…"
                            serving == false -> why ?: "Can't reach $where"
                            busy > 0 -> "$name is using this browser."
                            acts -> "$name can use this browser: go to pages, click and type."
                            else -> "$name can look at pages here, not click or type."
                        }
                        Text(status, style = Type.caption, color = c.fg, modifier = Modifier.weight(1f))
                        Spacer(Modifier.width(8.dp))
                        if (serving == true) Button(if (acts) "Watch only" else "Let it act", suggested = !acts) { acts = !acts }
                    }
                }
            }
        }
    }

    private fun shown(url: String) = url.removePrefix("https://").removePrefix("http://").ifEmpty { "about:blank" }
        .let { if (it.startsWith("data:")) "a page of its own" else it }

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
                serving = msg.optBoolean("on")
                why = msg.optString("why").ifBlank { null }
            }
            "acting" -> {
                sessionActing = msg.optBoolean("on")
                acting()
            }
            "close" -> finish()
            "call" -> call(msg.optInt("id"), msg.optJSONObject("call") ?: JSONObject())
        }
    }

    private fun acting() {
        stage.overlay.pointer.acting = sessionActing || busy > 0
        stage.overlay.kick()
    }

    /** A Bot's tools' request: done as Emission does it, its answer sent back (refused, if it'd change a page and you
     * haven't let the Bot act). The first one's answer says the browser's this phone's. */
    private fun call(id: Int, call: JSONObject) {
        val op = call.optString("op")
        val reply = { r: JSONObject ->
            busy -= 1
            acting()
            val out = JSONObject().put("op", "answer").put("id", id)
            if (r.has("error")) out.put("error", r.optString("error")) else {
                if (!noted) {
                    noted = true
                    r.put("note", "Your browser is on a phone now (Android's WebView, its pages laid out at the phone's " +
                        "size, ${(stage.width / resources.displayMetrics.density).toInt()} by " +
                        "${(stage.height / resources.displayMetrics.density).toInt()}): its tabs aren't the ones you " +
                        "had" + (if (KEEP) "." else ", and it keeps nothing once it's closed."))
                }
                out.put("result", r)
            }
            send(out)
        }
        busy += 1
        acting()
        if (!acts && op in ACTS) return reply(JSONObject().put("error", "On this phone you can look, not act (click, " +
            "drag, type, keys, form_input, scripts, prompts): its owner can let you from its screen."))
        control.run(op, call, reply)
    }

    override fun onStop() {
        super.onStop()
        if (!isChangingConfigurations) finish() // (left, or the screen locked: the Bot's browser is Emission's again)
    }

    override fun onDestroy() {
        super.onDestroy()
        if (!isFinishing) return
        for (t in browser.tabs.values.toList()) t.web.destroy()
        if (KEEP) android.webkit.CookieManager.getInstance().flush()
        Process.killProcess(Process.myPid()) // (its WebView's profile this Bot's: a process of its own each time; its
        // link closed with it, and what it kept forgotten then by the app's process: PhoneBrowser)
    }

    companion object {
        /** Whether a Bot's profile here keeps what it had (cookies, sign-ins, storage) from one time it's opened to the
         * next: not, till you've chosen. */
        const val KEEP = false
        val ACTS = setOf("click", "drag", "type", "key", "form_input", "javascript", "answer_prompt") // (what changes a page)
        const val ACCENT = 0xFF3584E4.toInt() // (Baude's accent: the pointer's and the glow's colour)
        private val PROFILE_SPACE = UUID.fromString("6b1f0d3e-3c4e-4f51-9b0e-7a1e5e6d2c11") // (emissionlink's)

        /** What's kept of a Bot's profile here (WebView's folders for it), gone: while no WebView of it is running. */
        fun forget(ctx: android.content.Context, profile: String) {
            val suffix = if (Build.VERSION.SDK_INT >= 28) "_emission-$profile" else ""
            File(ctx.dataDir, "app_webview$suffix").deleteRecursively()
            File(ctx.cacheDir, "webview$suffix").deleteRecursively()
            if (suffix.isEmpty()) File(ctx.cacheDir, "WebView").deleteRecursively()
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
