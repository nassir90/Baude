package dev.baude.android

import android.net.LocalSocket
import android.net.LocalSocketAddress
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.Process
import android.os.SystemClock
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
import androidx.compose.ui.graphics.toArgb
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

/** A Bot's browser on this phone (PhoneBrowser's), as an Emission window is (emissiongtk.Chrome's bars on the frame,
 * the page set into it: widgets.inside), its pointer over the page and the glow, as Baude's Browser page shows a Bot's
 * Emission. You can use it too: what you do by hand is told to the Bot with its next request, as Emission tells it its
 * browser changed. In a process of its own, its profile the Bot's (WebView's data, by emissionlink.bot_profile_id's
 * id: its cookies, storage and sign-ins kept from one time to the next, as Emission's profile keeps them); it goes on
 * serving out of sight (BrowserService), and ends, that process with it, once it's closed (the phone's back, or the
 * notification's Close). */
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
    private var suspended = false // (its host stopped carrying the Bot's requests here a while: the Bot told)
    private var reopened: List<String>? = null // (the pages its last time had open, opened again: told to the Bot)
    private var byHand = false // (what's being done is yours, said as it's done: not a page's)
    private lateinit var pagesFile: File

    override fun onCreate(savedInstanceState: Bundle?) {
        val bot = intent.getStringExtra("bot")
        val profile = bot?.let(::botProfileId)
        val suffixed = profile != null && (here == profile || Build.VERSION.SDK_INT < 28 || try {
            WebView.setDataDirectorySuffix("emission-$profile")
            true
        } catch (_: IllegalStateException) { // (this process's WebView started for another Bot: not shown it)
            false
        })
        super.onCreate(savedInstanceState)
        if (bot == null || profile == null || !suffixed) return finish()
        here = profile
        made = true
        val name = intent.getStringExtra("name") ?: "Bot"
        pagesFile = File(filesDir, "emission-$profile.open")
        val had = try { // (as emissionlink.open_pages: the tabs it had last time, and the one in front)
            JSONObject(pagesFile.readText())
        } catch (_: Exception) {
            JSONObject()
        }
        val urls = had.optJSONArray("urls")?.let { a -> (0 until a.length()).map { a.optString(it) } }
            ?.filter { it.isNotBlank() }.orEmpty()
        reopened = urls.takeIf { it.isNotEmpty() }
        enableEdgeToEdge()
        BrowserService.keep(this, name)
        stage = Stage(this, ACCENT)
        stage.overlay.k = resources.displayMetrics.density
        browser = Browser(this, name, stage)
        control = Control(browser, assets.open("emission/page.js").reader().readText(),
            assets.open("emission/repl.js").reader().readText(), stage.overlay.pointer) { stage.overlay.kick() }
        browser.early = control.early
        browser.changed = { t, how -> // (a page's own: a tab a click opened, or one it closed)
            if (!byHand && busy == 0) hand += if (how == "opened") "a page opened ${t.id}" else "a page closed ${t.id}"
        }
        main.post(object : Runnable { // (its tabs written down as they change, for its next time: open_pages)
            override fun run() {
                if (isDestroyed) return
                keepPages()
                main.postDelayed(this, 1000)
            }
        })
        stage.touched = {
            touchedAt = SystemClock.uptimeMillis()
            browser.current?.let { hand += "touched the page in ${it.id}" }
        }
        // (its tabs as it left them, the one in front in front again, as Emission's are; none, a blank one)
        for (url in urls) asked[browser.open(url)] = url
        if (urls.isEmpty()) browser.open("about:blank")
        else browser.tabs.values.elementAtOrNull(had.optInt("selected"))?.let(browser::select)
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
                    } }, newTab = { mine {
                        val t = browser.open(Control.HOME_PAGE)
                        hand += "opened a new tab, ${t.id}"
                    } })
                    TabStrip(browser, pick = { id -> browser.tabs[id]?.let {
                        if (it !== browser.current) hand += "switched to ${it.id}"
                        browser.select(it)
                    } }, close = { id -> browser.tabs[id]?.let { mine {
                        if (browser.tabs.size == 1) browser.open("about:blank")
                        hand += "closed ${it.id}"
                        browser.close(it)
                    } } })
                    Column(Modifier.weight(1f).fillMaxWidth().padding(start = Gap.inside, end = Gap.inside,
                        bottom = Gap.inside).border(1.dp, c.rule).padding(1.dp).background(c.inside).clipToBounds()) {
                        PromptCards(browser) { answer -> // (where Emission's banner is: in the page's box, over it)
                            try {
                                val kind = browser.waiting[answer.optString("id")]?.about?.optString("kind") ?: "prompt"
                                browser.answerPrompt(answer)
                                hand += "answered the page's $kind"
                            } catch (_: Exception) {
                            }
                        }
                        AndroidView({ stage }, Modifier.weight(1f).fillMaxWidth())
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

    private var pagesSaid: String? = null
    private val asked = HashMap<Tab, String>() // (a tab opened again: its address till its page has one of its own)

    /** Its tabs (their order, the one in front) written down if they've changed, as Emission's are: opened again the
     * next time this Bot's browser opens here. None (its last closed, a blank one left): none to open again. */
    private fun keepPages() {
        val url = { t: Tab -> t.url.ifEmpty { asked[t] ?: "" } }
        val tabs = browser.tabs.values.filter { url(it).isNotEmpty() && url(it) != "about:blank" }
        val now = JSONObject().put("urls", JSONArray(tabs.map(url)))
            .put("selected", tabs.indexOf(browser.current).coerceAtLeast(0)).toString()
        if (now == pagesSaid) return
        pagesSaid = now
        try {
            if (tabs.isEmpty()) pagesFile.delete() else pagesFile.writeText(now)
        } catch (_: Exception) {
        }
    }

    private fun mine(fn: () -> Unit) {
        byHand = true
        try {
            fn()
        } finally {
            byHand = false
        }
    }

    override fun onNewIntent(intent: android.content.Intent) {
        super.onNewIntent(intent)
        if (intent.getBooleanExtra("close", false)) finish() // (the notification's Close)
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
                if (!on && serving == true) suspended = true
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
        reopened?.let {
            out += "This browser opened again the pages it had open last time, from their addresses, in new tabs " +
                "(what was typed in them, and their history, is gone): " + it.take(10).joinToString(", ") + "."
        }
        if (suspended) out += "Its connection to its host dropped for a while (Android may have held the phone back): " +
            "what you asked meanwhile went to that host's own browser."
        if (!noted) {
            val d = resources.displayMetrics.density
            out += "Your browser is on a phone now (Android's WebView, its pages laid out at the phone's size, " +
                "${(stage.width / d).toInt()} by ${(stage.height / d).toInt()}): its tabs (phone1, …) and refs " +
                "(ref_p1, …) are its own, not your desktop browser's. Its owner can use it too: what they do is said " +
                "here, with your next request."
        }
        if (hand.isNotEmpty()) out += "Its owner used this browser by hand since your last request: " +
            hand.joinToString(", ") + "; ${browser.current?.id ?: "no tab"} is in front now. What you read of it " +
            "before may have changed."
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
                    suspended = false
                    reopened = null
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

    override fun onStart() {
        super.onStart()
        if (made) send(JSONObject().put("op", "again")) // (back in front: served again if it's been let go of)
    }

    override fun onStop() {
        super.onStop()
        if (made) android.webkit.CookieManager.getInstance().flush()
    }

    override fun onDestroy() {
        super.onDestroy()
        if (!made) return
        val urls = browser.tabs.values.map { it.url }.filter { it.isNotEmpty() && it != "about:blank" }
        keepPages()
        for (t in browser.tabs.values.toList()) t.web.destroy()
        if (!isFinishing) { // (made again, by Android: its link let go of, this one's the new one's)
            try {
                link?.close()
            } catch (_: Exception) {
            }
            return
        }
        val said = if (hand.isNotEmpty()) "Before the phone closed it, its owner used it by hand: " +
            hand.joinToString(", ") + "." else ""
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
        android.webkit.CookieManager.getInstance().flush()
        BrowserService.drop(this)
        Process.killProcess(Process.myPid()) // (a process of its own each time: its WebView's profile this Bot's)
    }

    companion object {
        private var here: String? = null // (the profile this process's WebView is started for)
        val KEYBOARD_QUIET = setOf("click", "drag", "type", "key", "form_input", "javascript") // (what can focus a field)
        val ACCENT = Tokens.Accent.blue.toArgb() // (Baude's accent: the pointer's and the glow's colour)
        private val PROFILE_SPACE = UUID.fromString("6b1f0d3e-3c4e-4f51-9b0e-7a1e5e6d2c11") // (emissionlink's)

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
