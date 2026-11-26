package dev.baude.android.screenshots

import android.os.Looper
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.Composable
import com.github.takahirom.roborazzi.captureRoboImage
import dev.baude.android.BaudeTheme
import dev.baude.android.ChatShell
import dev.baude.android.Connection
import dev.baude.android.Home
import dev.baude.android.Host
import dev.baude.android.Key
import dev.baude.android.Mesh
import dev.baude.android.Pipe
import dev.baude.android.Place
import dev.baude.android.Queue
import dev.baude.android.Settings
import dev.baude.android.Update
import dev.baude.android.Wording
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import org.robolectric.shadows.ShadowChoreographer
import org.robolectric.shadows.ShadowSystemClock
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.security.KeyPairGenerator
import java.security.interfaces.ECPublicKey
import java.time.Duration
import java.util.Base64
import java.util.concurrent.LinkedBlockingQueue
import kotlin.concurrent.thread

/** One way of a channel, as a socket's: bytes written at one end read at the other, in order; shut (EOF) or killed
 * (the OS dropping the socket: a reset) from either side. */
class Way {
    private val q = LinkedBlockingQueue<ByteArray>()
    private var have = ByteArray(0)
    private var at = 0
    @Volatile var dead: String? = null
        private set

    fun kill(why: String) {
        dead = why
        q.put(ByteArray(0))
    }

    val output = object : OutputStream() {
        override fun write(b: Int) = write(byteArrayOf(b.toByte()))
        override fun write(b: ByteArray, off: Int, len: Int) {
            dead?.let { throw IOException(it) }
            q.put(b.copyOfRange(off, off + len))
        }
    }

    val input = object : InputStream() {
        override fun read(): Int {
            val b = ByteArray(1)
            return if (read(b, 0, 1) < 0) -1 else b[0].toInt() and 0xff
        }
        override fun read(b: ByteArray, off: Int, len: Int): Int {
            while (at == have.size) {
                dead?.let { throw IOException(it) }
                have = q.take()
                at = 0
                if (have.isEmpty()) {
                    dead?.let { throw IOException(it) }
                    return -1
                }
            }
            val n = minOf(len, have.size - at)
            System.arraycopy(have, at, b, off, n)
            at += n
            return n
        }
    }
}

/** A host process as the lab has it: one session ("a"), its transcript (its size listed LAG_MS late, as a host's
 * listing lags its file), what it's been sent (each input once by its
 * uuid, as serversessions.take_input has it), a ping every second; reached only while refuse is null (refuse: why
 * ssh can't, as the phone's network is cut off in Doze); kill() drops its channel as the OS drops a phone's socket. */
class LabHost {
    @Volatile var refuse: String? = null
    @Volatile var slow = 0L // (ms a new channel takes to open: ssh's handshake)
    @Volatile var state = "idle"
    var title = "Tidy the release notes"
    val transcript = java.io.ByteArrayOutputStream()
    private val sizes = ArrayList<Pair<Long, Int>>() // (its transcript's size as it grew, and when)
    val inputs = ArrayList<String>() // (each input's text, as it came: twice if it was taken twice)
    private val taken = HashSet<String>()
    @Volatile var dropOnInput = false // (the link goes as the next input's taken, before it's answered)
    private var link: Pair<Way, Way>? = null
    val log = ArrayList<String>()
    val ops = java.util.Collections.synchronizedList(ArrayList<String>()) // (what the phone asked, in order)

    @Synchronized fun record(o: JSONObject) {
        transcript.write((o.toString() + "\n").toByteArray())
        sizes.add(System.currentTimeMillis() to transcript.size())
    }

    /** Its transcript's size as its listing says it: as it was LAG_MS ago. */
    @Synchronized private fun listedSize(): Int {
        val then = System.currentTimeMillis() - LAG_MS
        return sizes.lastOrNull { it.first <= then }?.second ?: 0
    }

    fun said(text: String, uuid: String = java.util.UUID.randomUUID().toString()) = record(JSONObject().put("type", "user")
        .put("uuid", uuid).put("message", JSONObject().put("role", "user").put("content", text)))

    fun answered(text: String) = record(JSONObject().put("type", "assistant").put("uuid", java.util.UUID.randomUUID().toString())
        .put("message", JSONObject().put("role", "assistant").put("content", JSONArray().put(JSONObject().put("type", "text").put("text", text)))))

    private fun session(): JSONObject {
        val now = System.currentTimeMillis() / 1000.0
        return JSONObject().put("id", "a").put("title", title).put("cwd", "/home/dev/garden").put("updated", now)
            .put("said", now).put("created", now - 600).put("state", state).put("model", "opus").put("effort", "high")
            .put("mode", "default").put("size", listedSize())
    }

    private fun send(to: Way, o: JSONObject) {
        try { to.output.write((o.toString() + "\n").toByteArray()) } catch (_: IOException) {}
    }

    /** The sessions, as told every subscriber when they change. */
    fun told() {
        link?.let { send(it.second, JSONObject().put("op", "sessions").put("sessions", JSONArray().put(session()))) }
    }

    fun kill(why: String = "Connection reset by peer") {
        val l = link ?: return
        link = null
        l.first.kill(why)
        l.second.kill(why)
        log.add("socket killed")
    }

    /** A new channel to it: (phone → host, host → phone). */
    fun open(): Pipe {
        refuse?.let { throw IOException(it) }
        Thread.sleep(slow)
        val up = Way()
        val down = Way()
        link = up to down
        thread(isDaemon = true, name = "lab-host") {
            try {
                up.input.bufferedReader().forEachLine { line ->
                    val m = try { JSONObject(line) } catch (_: Exception) { return@forEachLine }
                    val req = m.opt("req")
                    if (m.optString("op") != "ping") ops.add(m.optString("op"))
                    fun reply(o: JSONObject) = send(down, o.apply { if (req != null) put("req", req) })
                    when (m.optString("op")) {
                        "hello" -> reply(JSONObject().put("op", "hello").put("server", JSONObject().put("id", "m-workshop")
                            .put("name", "workshop").put("home", "/home/dev")))
                        "subscribe" -> told()
                        "attach" -> reply(JSONObject().put("op", "attached").put("session", m.optString("session")).put("asks", JSONArray()))
                        "transcript" -> {
                            val all = synchronized(this) { transcript.toByteArray() }
                            val from = m.optLong("offset").let { if (it > all.size) 0 else it.toInt() } // (past its end:
                            // from its start, as serversessions.read_lines has it)
                            reply(JSONObject().put("op", "transcript").put("session", "a").put("offset", from).put("next", all.size)
                                .put("size", all.size).put("data", Base64.getEncoder().encodeToString(all.copyOfRange(from, all.size))))
                        }
                        "input" -> {
                            val msg = m.getJSONObject("msg")
                            if (msg.optString("type") != "user") { // (a control request: its claude's answer isn't the lab's)
                                reply(JSONObject().put("op", "accepted").put("session", "a"))
                                return@forEachLine
                            }
                            val uuid = msg.optString("uuid")
                            val content = msg.getJSONObject("message").get("content")
                            val text = if (content is JSONArray) (0 until content.length()).map { content.getJSONObject(it) }
                                .filter { it.optString("type") == "text" }.joinToString("") { it.optString("text") } else content.toString()
                            if (taken.add(uuid)) {
                                inputs.add(text)
                                log.add("input taken: \"$text\"")
                                said(text, uuid)
                            } else log.add("input sent again, by the uuid it was taken by: not taken twice")
                            if (dropOnInput) {
                                dropOnInput = false
                                kill()
                                return@forEachLine
                            }
                            reply(JSONObject().put("op", "accepted").put("session", "a"))
                            told()
                        }
                        "ping", "detach" -> {}
                        else -> if (req != null) reply(JSONObject().put("op", "error").put("error", "unsupported operation"))
                    }
                }
            } catch (_: IOException) {
            } catch (e: Exception) {
                log.add("host broke: $e")
            }
        }
        thread(isDaemon = true, name = "lab-host-ping") {
            while (down.dead == null && link?.second === down) {
                send(down, JSONObject().put("op", "ping"))
                Thread.sleep(1_000)
            }
        }
        return object : Pipe {
            override val input = down.input
            override val output = up.output
            override val errors: InputStream = java.io.ByteArrayInputStream(ByteArray(0))
            override val closed get() = up.dead != null || down.dead != null
            override val exit get() = -1
            override fun connect(ms: Int) {}
            override fun disconnect() {
                if (link?.first === up) link = null
                up.kill("closed")
                down.kill("closed")
            }
        }
    }
}

/** A connection to the lab's host: Connection as the app runs it (its loop, its retries, its pings), its channel the
 * lab's instead of ssh's. */
const val LAG_MS = 600L

class LabConnection(ctx: android.content.Context, host: Host, private val lab: LabHost, heard: (Connection, JSONObject) -> Unit,
                    changed: (Connection) -> Unit) : Connection(ctx, Place.Direct(host), identify = { _, _ -> }, route = { null },
    heard = heard, changed = changed) {
    override fun open(): Pipe = lab.open()
}

/** The phone's link to a host driven through what Android does to it, on the JVM (Robolectric): the app put away
 * (onStop) and brought back (onStart), its socket killed as Doze or a network change kills it, the network cut off
 * and given back, the app's process killed and started again. Each scene saves its screens
 * (build/screenshots/<theme>/link-*.png) and what happened when (build/link-lab/<theme>/<scene>.txt): a lab, not a
 * test (nothing's checked). */
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35])
abstract class LinkLab(private val theme: String) {
    private lateinit var activity: ComponentActivity
    private lateinit var lab: LabHost
    private lateinit var conn: LabConnection
    private val host = Host(id = "h1", name = "workshop", user = "dev", address = "workshop.example")
    private val timeline = ArrayList<String>()
    private var t0 = 0L

    @Before fun up() {
        freshCompose()
        ShadowChoreographer.setPaused(true)
        ShadowChoreographer.setFrameDelay(Duration.ofMillis(16))
        activity = Robolectric.buildActivity(ComponentActivity::class.java).setup().get()
        Mesh.app = activity.applicationContext
        Settings.load(activity)
        Settings.accent.value = null
        Settings.theme.value = null
        Settings.themeColours.value = null
        dev.baude.android.Themes.listed.value = emptyList()
        Settings.appearance.value = "system"
        Settings.textScale.value = 1f
        Settings.globalScale.value = 1f
        Update.state.value = ""
        Settings.split.value = false
        Queue.loadDrafts(activity.filesDir)
        File(activity.filesDir, "link-cache").deleteRecursively()
        try { // (what's kept on the phone, where the app keeps it)
            val cache = Class.forName("dev.baude.android.LinkCache")
            cache.getMethod("start", File::class.java).invoke(cache.getField("INSTANCE").get(null), activity.filesDir)
        } catch (_: ClassNotFoundException) {
        }
        val pair = KeyPairGenerator.getInstance("EC").apply { initialize(256) }.generateKeyPair()
        Mesh.key.value = Key(pair.private, pair.public as ECPublicKey)
        Mesh.hosts.add(host)
        lab = LabHost()
        lab.said("Tidy the release notes for 0.9: group them by area and drop the internal ones.")
        lab.answered("Here's a tidier draft, grouped by area:\n\n**Sync**\n- Uploads resume after a dropped connection\n" +
            "- Faster first sync on large folders\n\n**Settings**\n- A larger default size on phones\n\nWant me to write it to `CHANGELOG.md`?")
        lab.said("Yes, and link each item to its PR.")
        lab.answered("Done: each item links to its PR, and `CHANGELOG.md` is written.")
        t0 = System.currentTimeMillis()
    }

    @After fun down() {
        try { conn.stop() } catch (_: Exception) {}
        FixtureConnection.down()
        Mesh.hosts.clear()
        Mesh.sessions.clear()
        Mesh.infos.clear()
        Mesh.key.value = null
        Mesh.drafts.clear()
        File(activity.filesDir, "drafts").deleteRecursively()
    }

    // ---- scenes

    /** The phone put away with a chat open, its socket killed and its network cut off (Doze) for 20 s, then brought
     * back: what's on screen as it comes back, and how long till the chat's live again. */
    @Test fun resume() = scene("resume") {
        connect()
        chat()
        typeIn("Also bump the version")
        shot("link-01-chat-up")
        background()
        lab.kill()
        lab.refuse = "Network is unreachable"
        note("Doze: the network's cut off")
        pump(20_000)
        lab.refuse = null
        lab.slow = 1_500
        note("Doze over")
        foreground()
        pump(300)
        shot("link-02-resume-at-once")
        val back = until(40_000) { conn.state == "up" }
        note("live again after ${back} ms")
        pump(1_500)
        shot("link-03-resume-back")
    }

    /** A message written and sent while the host can't be reached; then the host back. */
    @Test fun offlineSend() = scene("offline-send") {
        connect()
        chat()
        lab.kill()
        lab.refuse = "Network is unreachable"
        pump(1_500)
        typeIn("Also bump the version")
        shot("link-04-offline-typed")
        send()
        pump(500)
        shot("link-05-offline-sent")
        lab.refuse = null
        lab.slow = 1_500
        background()
        foreground()
        pump(300)
        shot("link-05b-offline-reconnecting")
        val back = until(40_000) { conn.state == "up" }
        note("live again after ${back} ms")
        pump(3_000)
        shot("link-06-offline-back")
        note("the host took: ${lab.inputs}")
    }

    /** A message sent as the link dies under it: the host took it, its answer never came. Sent again by hand, as
     * the app asks. */
    @Test fun droppedMidSend() = scene("dropped-mid-send") {
        connect()
        chat()
        lab.dropOnInput = true
        typeIn("Also bump the version")
        send()
        pump(1_500)
        shot("link-07-mid-send")
        until(40_000) { conn.state == "up" }
        pump(3_000)
        if (Mesh.open?.draft?.value != null || boxText().isNotBlank()) {
            note("the message is back in the box: sent again")
            send()
            pump(2_000)
        }
        shot("link-08-mid-send-after")
        note("the host took: ${lab.inputs}")
    }

    /** A turn that ends while the phone's away. */
    @Test fun turnEndedAway() = scene("turn-ended-away") {
        lab.said("Now write the migration notes.")
        lab.state = "working"
        connect()
        chat()
        shot("link-09-turn-running")
        background()
        lab.kill()
        lab.refuse = "Network is unreachable"
        pump(1_000)
        lab.answered("The migration notes are in `MIGRATING.md`: three steps, each with the command to run.")
        lab.state = "idle"
        note("the turn ended on the host")
        lab.refuse = null
        foreground()
        until(40_000) { conn.state == "up" }
        pump(2_000)
        shot("link-10-turn-ended-away")
    }

    /** An ordinary turn with the chat open, the host's listing a moment behind its transcript: the chat reads on as
     * the turn goes, never from nothing. */
    @Test fun liveTurn() = scene("live-turn") {
        connect()
        chat()
        typeIn("Also bump the version")
        send()
        lab.state = "working"
        lab.told()
        pump(800)
        lab.answered("Bumped to 0.9.1 in `build.gradle.kts`, and `CHANGELOG.md` has it at the top.")
        lab.told()
        pump(300)
        lab.answered("Anything else for the release?")
        lab.state = "idle"
        lab.told()
        pump(1_500)
        shot("link-15-live-turn")
        note("the host took: ${lab.inputs}")
    }

    /** The app's process killed while it was put away (Android does, to free memory), then opened again on the chat,
     * the host not reached yet. */
    @Test fun coldStart() = scene("cold-start") {
        connect()
        chat()
        pump(500)
        background()
        pump(2_500) // (what was being kept, written)
        note("process killed")
        conn.stop()
        restart()
        lab.refuse = "Network is unreachable"
        note("sessions known after restart: ${Mesh.sessions.keys.toList()}")
        conn = start()
        pump(300)
        note("sessions known as it's opened: ${Mesh.sessions.keys.toList()}")
        show { Home(open = { _, _ -> }, feedback = {}) }
        shot("link-11-cold-home")
        chat()
        shot("link-12-cold-chat")
    }

    /** The host gone for minutes, the app on screen. */
    @Test fun longOffline() = scene("long-offline") {
        connect()
        chat()
        lab.kill()
        lab.refuse = "Network is unreachable"
        pump(1_500)
        ShadowSystemClock.advanceBy(Duration.ofMinutes(3))
        pump(500)
        shot("link-13-long-offline")
        show { Home(open = { _, _ -> }, feedback = {}) }
        shot("link-14-long-offline-home")
    }

    // ---- the lab's hands

    private fun scene(name: String, body: () -> Unit) {
        try {
            body()
        } finally {
            timeline.addAll(lab.log.map { "  host: $it" })
            timeline.add("  host was asked: ${lab.ops.joinToString(" ")}")
            File("build/link-lab/$theme").mkdirs()
            File("build/link-lab/$theme/$name.txt").writeText(timeline.joinToString("\n") + "\n")
        }
    }

    private fun note(what: String) {
        timeline.add("%6d ms  %s".format(System.currentTimeMillis() - t0, what))
    }

    private fun mesh(name: String, vararg types: Class<*>) =
        Mesh::class.java.getDeclaredMethod(name, *types).apply { isAccessible = true }

    private fun start(): LabConnection {
        val heard = mesh("heard", Connection::class.java, JSONObject::class.java)
        val changed = mesh("changed", Connection::class.java)
        val main = android.os.Handler(Looper.getMainLooper())
        var c: LabConnection? = null
        c = LabConnection(activity, host, lab, { cn, m -> heard.invoke(Mesh, cn, m) }) { cn ->
            main.post { changed.invoke(Mesh, cn) }
        }
        @Suppress("UNCHECKED_CAST")
        val conns = Mesh::class.java.getDeclaredField("conns").apply { isAccessible = true }.get(Mesh) as HashMap<String, Connection>
        conns[host.id] = c
        Mesh.states[host.id] = c
        c.start()
        var was = ""
        val watch = { if (c.state != was) { was = c.state; note("link: ${c.state}${c.why?.let { " ($it)" } ?: ""}") } }
        watchers.add(watch)
        return c
    }

    private val watchers = ArrayList<() -> Unit>()

    private fun connect() {
        conn = start()
        until(10_000) { conn.state == "up" && Mesh.sessions["h1"] != null }
    }

    /** Mesh as a new process has it: nothing but what's kept on disk. */
    private fun restart() {
        FixtureConnection.down()
        Mesh.sessions.clear()
        Mesh.infos.clear()
        Mesh.drafts.clear()
        watchers.clear()
        Queue.loadDrafts(activity.filesDir)
        Mesh::class.java.declaredMethods.firstOrNull { it.name == "loadCache" }?.apply { isAccessible = true }?.invoke(Mesh)
    }

    private fun background() {
        Mesh.onScreen(false)
        note("app put away (onStop)")
    }

    private fun foreground() {
        Mesh.onScreen(true)
        note("app brought back (onStart)")
    }

    private fun chat() {
        show { ChatShell("h1", "a", back = {}, open = { _, _ -> }, feedback = {}, first = "chat") }
        var had = 0
        watchers.add {
            val n = Mesh.open?.lines?.size ?: 0
            if (had > 0 && n == 0) note("chat read again from nothing: its $had lines let go of")
            had = n
        }
        pump(1_000)
    }

    /** Real time let pass, what the link's threads post run as it does. */
    private fun pump(ms: Long) {
        val end = System.currentTimeMillis() + ms
        do {
            shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(16))
            watchers.forEach { it() }
            Thread.sleep(16)
        } while (System.currentTimeMillis() < end)
    }

    private fun until(most: Long, done: () -> Boolean): Long {
        val from = System.currentTimeMillis()
        while (!done() && System.currentTimeMillis() - from < most) pump(50)
        return System.currentTimeMillis() - from
    }

    private fun show(screen: @Composable () -> Unit) {
        activity.setContent { BaudeTheme { screen() } }
        settle()
    }

    private fun shot(name: String) {
        androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
        settle()
        activity.window.decorView.captureRoboImage("build/screenshots/$theme/$name.png")
        note("screen: $name")
    }

    private fun nodes(): List<androidx.compose.ui.semantics.SemanticsNode> {
        fun roots(v: android.view.View): List<androidx.compose.ui.node.RootForTest> = when (v) {
            is androidx.compose.ui.node.RootForTest -> listOf(v)
            is android.view.ViewGroup -> (0 until v.childCount).flatMap { roots(v.getChildAt(it)) }
            else -> emptyList()
        }
        fun all(n: androidx.compose.ui.semantics.SemanticsNode): List<androidx.compose.ui.semantics.SemanticsNode> =
            listOf(n) + n.children.flatMap { all(it) }
        return roots(activity.window.decorView).flatMap { all(it.semanticsOwner.rootSemanticsNode) }
    }

    private fun typeIn(typed: String) {
        val box = nodes().last { it.config.contains(androidx.compose.ui.semantics.SemanticsActions.SetText) }
        box.config[androidx.compose.ui.semantics.SemanticsActions.SetText].action!!.invoke(androidx.compose.ui.text.AnnotatedString(typed))
        androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
        settle()
    }

    private fun boxText(): String = nodes().lastOrNull { it.config.contains(androidx.compose.ui.semantics.SemanticsActions.SetText) }
        ?.config?.getOrElse(androidx.compose.ui.semantics.SemanticsProperties.EditableText) { androidx.compose.ui.text.AnnotatedString("") }?.text.orEmpty()

    /** The send button tapped, if it's on. */
    private fun send() {
        val act = androidx.compose.ui.semantics.SemanticsActions.OnClick
        val button = nodes().lastOrNull { n ->
            n.config.contains(act) && n.config.getOrElse(androidx.compose.ui.semantics.SemanticsProperties.ContentDescription) { emptyList() }
                .contains(Wording.Common.send)
        }
        if (button == null || button.config.contains(androidx.compose.ui.semantics.SemanticsProperties.Disabled)) {
            note("send tapped: the button is off, nothing sent")
            return
        }
        button.config[act].action!!.invoke()
        androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
        note("send tapped")
        settle()
    }
}

@RunWith(RobolectricTestRunner::class)
@Config(qualifiers = "w411dp-h891dp-notnight-xxhdpi")
class LightLinkLab : LinkLab("light")

@RunWith(RobolectricTestRunner::class)
@Config(qualifiers = "w411dp-h891dp-night-xxhdpi")
class DarkLinkLab : LinkLab("dark")
