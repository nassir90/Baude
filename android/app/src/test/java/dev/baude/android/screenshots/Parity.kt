package dev.baude.android.screenshots

import android.view.View
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.Composable
import androidx.compose.ui.platform.ViewRootForTest
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsNode
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.state.ToggleableState
import com.github.takahirom.roborazzi.captureScreenRoboImage
import dev.baude.android.Ask
import dev.baude.android.AppVersion
import dev.baude.android.BaudeTheme
import dev.baude.android.Change
import dev.baude.android.ChangelogScreen
import dev.baude.android.ChatShell
import dev.baude.android.Home
import dev.baude.android.Host
import dev.baude.android.Key
import dev.baude.android.FeedbackScreen
import dev.baude.android.Mesh
import dev.baude.android.Queued
import dev.baude.android.Meta
import dev.baude.android.Msg
import dev.baude.android.Settings
import dev.baude.android.SettingsScreen
import dev.baude.android.Update
import dev.baude.android.Wording
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import org.robolectric.shadows.ShadowChoreographer
import java.io.File
import java.security.KeyPairGenerator
import java.security.interfaces.ECPublicKey
import java.time.Duration

/** The phone's side of the parity rig (tests/parity/): each scene of tests/parity/scenes.json drawn from
 * tests/parity/fixtures, as tests/parity/out/phone/<theme>/<scene>.png, and what's on screen in it as
 * tests/parity/out/phone/<scene>.json, in the shape tests/parity/dump.py writes for Baude (the text, buttons, inputs,
 * switches and images a user sees, top to bottom; read from Compose's semantics). A scene the phone has no screen for
 * is written {"missing": …}. A menu or dialog in a window of its own is in the dump but not in the picture (Roborazzi's
 * screen capture leaves those windows out here, and drawing one by hand comes out empty). A render rig, not a test: nothing's checked. In android/:
 *     ./gradlew --offline testDebugUnitTest --tests '*Parity*' */
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35])
abstract class Parity(private val theme: String, private val scene: String) {
    private lateinit var activity: ComponentActivity
    private val root = File("../../tests/parity")
    private val out = File(root, "out/phone")
    private val sessions = JSONObject(File(root, "fixtures/sessions.json").readText())
    private val host = sessions.getJSONObject("host")
    private val home = host.getString("home")

    private lateinit var conn: FixtureConnection

    @Before fun up() {
        val sc = scenes().first { it.getString("name") == scene }
        files = sc.optString("files").ifBlank { null }
        val opened = sessions.getJSONArray("sessions").getJSONObject(sc.optInt("session", 0))
        shown = (if (sc.has("transcript")) transcript(sc.getString("transcript"), sc.optInt("lines", -1))
            else if (opened.isNull("transcript")) standIn(opened)
            else transcript(opened.getString("transcript"), sc.optInt("lines", -1))).toByteArray()
        freshCompose()
        ShadowChoreographer.setPaused(true)
        ShadowChoreographer.setFrameDelay(Duration.ofMillis(16))
        activity = Robolectric.buildActivity(ComponentActivity::class.java).setup().get()
        Mesh.app = activity.applicationContext
        // (the running version, as Settings' Version and the Changelog say it: the fixture's, not this build's)
        val log = JSONObject(File(root, "fixtures/changelog.json").readText())
        AppVersion.short = log.getString("commit")
        AppVersion.made = log.getLong("made")
        AppVersion.read = log.getJSONArray("commits").let { a ->
            (0 until a.length()).map { a.getJSONArray(it) }.map { Change(it.getString(0), it.getLong(1), it.getString(2)) }
        }
        Settings.load(activity)
        val set = JSONObject(File(root, "fixtures/settings.json").readText())
        Settings.accent.value = if (set.isNull("accent")) null else set.getString("accent")
        Settings.appearance.value = "system"
        Settings.textScale.value = set.optDouble("text_scale", 1.0).toFloat()
        Settings.iconScale.value = set.optDouble("icon_scale", 1.0).toFloat()
        Settings.globalScale.value = set.optDouble("zoom_default", 1.0).toFloat()
        Settings.fullWidth.value = set.optBoolean("full_width", true)
        Settings.readOnly.clear()
        Settings.metaModel.value = null
        Settings.metaEffort.value = null
        Settings.metaCommands.value = true
        Settings.codeFont.value = null
        Settings.notify.value = set.optBoolean("notifications", true)
        Settings.meteredUpdates.value = false
        Update.state.value = ""
        val pair = KeyPairGenerator.getInstance("EC").apply { initialize(256) }.generateKeyPair()
        Mesh.key.value = Key(pair.private, pair.public as ECPublicKey)
        val h = Host(id = host.getString("id"), name = host.getString("name"), user = "dev", address = "workshop.example")
        Mesh.hosts.add(h)
        Meta.host.value = h.id
        conn = FixtureConnection.up(activity, h, home) { msg -> answer(msg) }
        val now = System.currentTimeMillis() / 1000.0
        val list = sessions.getJSONArray("sessions")
        val first = list.getJSONObject(0)
        Mesh.sessions[h.id] = (0 until list.length()).map { list.getJSONObject(it) }.mapIndexed { i, s ->
            Msg.Session(id = s.getString("id"), title = s.getString("title"), cwd = "$home/${s.getString("folder")}",
                updated = now - s.getDouble("ago"), said = now - s.getDouble("ago"),
                created = now - s.getDouble("ago") - 600,
                state = (if (i == 0) sc.optString("state").ifBlank { null } else null) ?: s.getString("state"), model = s.getString("model"),
                effort = s.getString("effort"), mode = s.getString("mode"),
                cut = if (i == 0 && sc.optBoolean("cut")) now else null,
                size = if (s.getString("id") == opened.getString("id")) shown.size else 0)
        }
    }

    /** The scene's transcript, as the host has it: the first `lines` lines of the fixture's (all, without). */
    private var shown = ByteArray(0)

    /** The scene's `files`: a folder of fixtures whose contents join the chat's folder (as the desktop rig copies them in). */
    private var files: String? = null

    /** What the fixture host answers, as the desktop's host (baudelib/server.py, hostfiles.py) would: the open chat's
     * transcript, and the folder's files (fixtures/garden, not a git repository, with no Signal glossary). */
    private fun answer(msg: JSONObject): JSONObject? {
        val op = msg.optString("op")
        fun reply(vararg more: Pair<String, Any?>) = JSONObject().put("op", op).put("req", msg.opt("req")).also { r ->
            more.forEach { (k, v) -> r.put(k, v) }
        }
        // (the scene's files first, then fixtures/garden: the desktop rig's folder holds both)
        fun local(path: String): File? {
            val rel = path.removePrefix("$home/garden").trimStart('/')
            if (path != "$home/garden" && !path.startsWith("$home/garden/")) return null
            val roots = listOfNotNull(files, "garden").map { File(root, "fixtures/$it") }
            val at = roots.map { if (rel.isEmpty()) it else File(it, rel) }
            return at.firstOrNull { it.exists() } ?: at.last()
        }
        when (op) {
            "transcript" -> {
                val from = msg.optLong("offset", 0).coerceIn(0, shown.size.toLong()).toInt()
                val part = shown.copyOfRange(from, shown.size)
                return reply("session" to msg.optString("session"), "offset" to from, "next" to shown.size, "size" to shown.size,
                    "data" to java.util.Base64.getEncoder().encodeToString(part))
            }
            "list_dir" -> {
                val path = msg.optString("path")
                val rel = path.removePrefix("$home/garden").trimStart('/')
                val dirs = listOfNotNull(files, "garden").map { File(root, "fixtures/$it").let { g -> if (rel.isEmpty()) g else File(g, rel) } }
                    .filter { it.isDirectory }
                if (local(path) == null || dirs.isEmpty()) return reply("op" to "dir", "path" to path, "entries" to JSONArray(),
                    "error" to "No such file or directory")
                val entries = dirs.flatMap { it.listFiles().orEmpty().toList() }.filter { it.name != ".git" }.distinctBy { it.name }
                    .sortedWith(compareBy({ !it.isDirectory }, { it.name.lowercase() }))
                return reply("op" to "dir", "path" to path, "more" to false, "entries" to JSONArray(entries.map { e ->
                    JSONObject().put("name", e.name).put("dir", e.isDirectory).put("ignored", false)
                        .apply { if (!e.isDirectory) put("size", e.length()) }
                }))
            }
            "read_file" -> {
                val path = msg.optString("path")
                val f = local(path)?.takeIf { it.isFile } ?: return reply("op" to "file", "path" to path, "error" to "No such file or directory")
                return reply("op" to "file", "path" to path, "size" to f.length(), "truncated" to false,
                    "data" to java.util.Base64.getEncoder().encodeToString(f.readBytes()))
            }
            "git" -> return reply("what" to msg.optString("what"), "error" to Wording.HostErrors.notARepo)
            "signal_names" -> return reply("glossary" to "$home/garden/.baude", "refs" to JSONArray())
            "catalogue" -> return reply("cwd" to msg.optString("cwd"), "items" to JSONArray(), "busy" to false)
        }
        return null
    }

    @After fun down() {
        Meta.host.value = null
        FixtureConnection.down()
        Mesh.hosts.clear()
        Mesh.sessions.clear()
        Mesh.infos.clear()
        Mesh.states.clear()
        Mesh.key.value = null
    }

    @Test fun draw() {
        val sc = scenes().first { it.getString("name") == scene }
        val made = runCatching { draw(sc) }
        if (made.isFailure) {
            if (theme == "light") write(scene, JSONObject().put("scene", scene).put("app", "phone")
                .put("missing", "failed: ${made.exceptionOrNull()}"))
            return
        }
        if (made.getOrNull() == false) {
            if (theme == "light") write(scene, JSONObject().put("scene", scene).put("app", "phone")
                .put("missing", "no phone screen"))
            return
        }
        File(out, theme).mkdirs()
        captureScreenRoboImage(File(out, "$theme/$scene.png").path)
        if (theme == "light") write(scene, JSONObject().put("scene", scene).put("app", "phone")
            .put("size", JSONArray(listOf(411, 891))).put("items", items()))
    }

    /** The scene drawn: true, or false when the phone has no screen for it. */
    private fun draw(sc: JSONObject): Boolean {
        val name = sc.getString("name")
        val first = sessions.getJSONArray("sessions").getJSONObject(sc.optInt("session", 0))
        when {
            name.startsWith("home") -> {
                show { Home(open = { _, _ -> }, feedback = {}) }
                if (name == "home-row-menu") hold(first.getString("title"))
                if (name == "home-host-menu" || name == "home-add-host") hold(host.getString("name"))
                if (name == "home-add-host") tap(Wording.Sidebar.addHost)
            }
            name.startsWith("chat") || name.startsWith("panel") || name == "menu" -> {
                if (sc.optBoolean("open")) openAll(first.getString("id"))
                if (sc.optBoolean("down")) {
                    conn.state = "down"
                    Mesh.tick.value++
                }
                if (sc.has("draft")) Mesh.openChat(host.getString("id"), first.getString("id")).draft.value = sc.getString("draft")
                show { ChatShell(host.getString("id"), first.getString("id"), back = {}, open = { _, _ -> }, feedback = {},
                    first = sc.optString("page").ifBlank { "chat" }) }
                if (name == "menu") tap(Wording.ChatHeader.menu)
                if (sc.has("typed")) type(sc.getString("typed"))
                if (sc.optBoolean("view")) {
                    dev.baude.android.Viewer.flyMs = 0
                    val shot = roots().flatMap { all(it.semanticsOwner.rootSemanticsNode) }.firstOrNull { n ->
                        n.config.getOrNull(SemanticsProperties.Role) == Role.Image && n.config.contains(SemanticsActions.OnClick)
                    }
                    shot?.config?.get(SemanticsActions.OnClick)?.action?.invoke()
                    androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
                    settle()
                }
                if (sc.optBoolean("open")) toEnd()
                if (sc.has("ask")) ask(sc.getString("ask"))
                if (sc.has("queued")) {
                    Mesh.open!!.queued.add(Queued(sc.getString("queued"), emptyList()))
                    androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
                    settle()
                    hold(sc.getString("queued"))
                }
            }
            name == "feedback" -> show { FeedbackScreen(listOf("Chat: Fix the flaky upload"), back = {}) }
            name == "settings" -> show { SettingsScreen({}, {}) }
            name == "changelog" -> show { ChangelogScreen(null, {}) }
            else -> return false
        }
        return true
    }

    /** What the desktop rig writes for a session with no transcript of its own: its prompt, a reply, its title. */
    private fun standIn(s: JSONObject): String {
        fun rec(type: String, vararg more: Pair<String, Any>) = JSONObject().put("type", type).also { o ->
            more.forEach { (k, v) -> o.put(k, v) }
        }
        val cwd = "$home/${s.getString("folder")}"
        val id = s.getString("id")
        return listOf(
            rec("user", "cwd" to cwd, "sessionId" to id, "message" to JSONObject().put("role", "user").put("content", s.getString("title"))),
            rec("assistant", "cwd" to cwd, "sessionId" to id, "message" to JSONObject().put("role", "assistant")
                .put("content", JSONArray().put(JSONObject().put("type", "text").put("text", "On it.")))),
            rec("custom-title", "customTitle" to s.getString("title"), "sessionId" to id),
        ).joinToString("\n") { it.toString() } + "\n"
    }

    /** The permission its call asks, as claude's control_request for it (the call is the transcript's, with no result). */
    private fun ask(id: String) {
        val chat = Mesh.open!!
        if (chat.calls.byId[id] == null) settle()
        val call = chat.calls.byId.getValue(id)
        chat.asks.add(Ask("perm-$id", call.name, JSONObject(call.input.toString()), id))
        settle()
    }

    /** The chat read and every group and every call's input and output opened, before it's drawn (as desktop.py opens
     * them: all open, the chat at its end). */
    private fun openAll(first: String) {
        val chat = Mesh.openChat(host.getString("id"), first)
        settle()
        for (call in chat.calls.byId.values) {
            call.inputOpen = true
            call.outputOpen = true
            chat.calls.opened[call.id] = true
        }
        for (line in chat.lines) if (line.kind == "compact") line.uuid?.let { dev.baude.android.CompactFolds.open[it] = true }
    }

    /** typed put in the box a character at a time, as typing (what starts the / and @ lists is the one just typed). */
    private fun type(typed: String) {
        val box = roots().flatMap { all(it.semanticsOwner.rootSemanticsNode) }.firstOrNull { it.config.contains(SemanticsActions.SetText) }
            ?: error("no box to type in")
        for (k in 1..typed.length) {
            box.config[SemanticsActions.SetText].action?.invoke(androidx.compose.ui.text.AnnotatedString(typed.take(k)))
            androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
            settle()
        }
    }

    /** The chat's list (the one that scrolls up and down, with items) scrolled to its end. */
    private fun toEnd() {
        val list = roots().flatMap { all(it.semanticsOwner.rootSemanticsNode) }.firstOrNull { n ->
            val c = n.config
            c.contains(SemanticsProperties.CollectionInfo) && c.contains(SemanticsProperties.VerticalScrollAxisRange) &&
                c.contains(SemanticsActions.ScrollBy)
        } ?: return
        repeat(8) {
            list.config[SemanticsActions.ScrollBy].action?.invoke(0f, 2500f)
            settle()
        }
    }

    private fun transcript(file: String, lines: Int): String {
        val all = File(root, "fixtures/$file").readLines().filter { it.isNotBlank() }
            .map { it.replace("@CWD@", "$home/garden") }
        val (title, body) = all.partition { it.contains("\"custom-title\"") }
        return ((if (lines < 0) body else body.take(lines)) + title).joinToString("\n") + "\n"
    }

    private fun show(screen: @Composable () -> Unit) {
        activity.setContent { BaudeTheme { screen() } }
        settle()
    }

    /** A click on what's labelled label (its text or its description), as a finger's would. */
    private fun tap(label: String) {
        val node = roots().flatMap { all(it.semanticsOwner.rootSemanticsNode) }.firstOrNull { n ->
            n.config.contains(SemanticsActions.OnClick) && (label in said(n) || label == described(n))
        } ?: error("nothing labelled $label")
        node.config[SemanticsActions.OnClick].action?.invoke()
        // (what the tap wrote is told to the composition now: in a run's later scenes the write observer's notice doesn't arrive)
        androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
        settle()
    }

    /** A long-press on what's labelled label, as a finger's would be. */
    private fun hold(label: String) {
        fun found() = roots().flatMap { all(it.semanticsOwner.rootSemanticsNode) }.firstOrNull { n ->
            n.config.contains(SemanticsActions.OnLongClick) && (label in said(n) || label == described(n))
        }
        repeat(20) { if (found() == null) settle() }
        val node = found() ?: error("nothing to hold labelled $label")
        node.config[SemanticsActions.OnLongClick].action?.invoke()
        androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
        settle()
    }

    private fun all(n: SemanticsNode): List<SemanticsNode> = listOf(n) + n.children.flatMap { all(it) }

    private fun said(n: SemanticsNode) = n.config.getOrNull(SemanticsProperties.Text)?.joinToString(" ") { it.text } ?: ""
    private fun described(n: SemanticsNode) = n.config.getOrNull(SemanticsProperties.ContentDescription)?.joinToString(" ")

    /** Every window's root view, shown: the activity's, then a menu's or a dialog's (each a window of its own). */
    private fun windows(): List<View> {
        val global = Class.forName("android.view.WindowManagerGlobal")
        val wm = global.getMethod("getInstance").invoke(null)
        // (the activity's own window first)
        @Suppress("UNCHECKED_CAST")
        val all = global.getDeclaredField("mViews").apply { isAccessible = true }.get(wm) as List<View>
        return all.filter { it.isShown }.sortedBy { if (it === activity.window.decorView) 0 else 1 }
    }

    /** Every window's Compose root. */
    private fun roots(): List<ViewRootForTest> {
        val views = windows()
        fun find(v: View): ViewRootForTest? = v as? ViewRootForTest
            ?: (v as? android.view.ViewGroup)?.let { g -> (0 until g.childCount).firstNotNullOfOrNull { find(g.getChildAt(it)) } }
        return views.mapNotNull { find(it) }
    }

    private fun items(): JSONArray {
        val density = activity.resources.displayMetrics.density
        val out = mutableListOf<JSONObject>()
        roots().forEachIndexed { i, r ->
            val area = if (r.view.rootView === activity.window.decorView) "screen" else "popover"
            val top = r.semanticsOwner.rootSemanticsNode
            val view = r.view
            val at = IntArray(2).also { view.getLocationOnScreen(it) }
            fun walk(n: SemanticsNode) {
                val c = n.config
                val b = n.boundsInRoot
                val text = said(n).ifBlank { null }
                val edit = c.getOrNull(SemanticsProperties.EditableText)?.text
                val desc = described(n)
                val role = c.getOrNull(SemanticsProperties.Role)
                val click = c.contains(SemanticsActions.OnClick)
                val toggle = c.getOrNull(SemanticsProperties.ToggleableState)
                val kind = when {
                    edit != null || c.contains(SemanticsActions.SetText) -> "input"
                    role == Role.Checkbox -> "check"
                    role == Role.Switch || toggle != null -> "switch"
                    role == Role.Image -> "image"
                    role == Role.Tab -> "tab"
                    click || role == Role.Button || role == Role.DropdownList -> "button"
                    text != null -> "text"
                    desc != null -> "image"
                    else -> null
                }
                if (kind != null && b.width > 1 && b.height > 1) {
                    val o = JSONObject().put("kind", kind)
                    (text ?: edit)?.let { o.put("text", it) }
                    c.getOrNull(SemanticsProperties.EditableText)?.let { _ -> text?.let { o.put("sub", it) } }
                    desc?.let { o.put("tip", it) }
                    if (toggle != null) o.put("on", toggle == ToggleableState.On)
                    if (c.contains(SemanticsProperties.Selected) && c[SemanticsProperties.Selected]) o.put("selected", true)
                    if (c.contains(SemanticsProperties.Disabled)) o.put("dim", true)
                    o.put("area", area)
                    o.put("at", JSONArray(listOf((b.left + at[0]) / density, (b.top + at[1]) / density, b.width / density,
                        b.height / density).map { Math.round(it) }))
                    out.add(o)
                }
                n.children.forEach { walk(it) }
            }
            walk(top)
        }
        out.sortWith(compareBy({ if (it.getString("area") == "screen") 0 else 1 },
            { it.getJSONArray("at").getInt(1) / 6 }, { it.getJSONArray("at").getInt(0) }))
        return JSONArray(out)
    }

    private fun write(name: String, o: JSONObject) {
        out.mkdirs()
        File(out, "$name.json").writeText(o.toString(1))
    }
}

/** tests/parity/scenes.json's scenes. */
fun scenes(): List<JSONObject> = JSONObject(File("../../tests/parity/scenes.json").readText()).getJSONArray("scenes")
    .let { a -> (0 until a.length()).map { a.getJSONObject(it) } }

/** Their names, read without org.json (the runner lists them outside Robolectric, where Android's is a stub). */
fun sceneNames(): List<Array<Any>> = Regex(""""name":\s*"([^"]+)"""")
    .findAll(File("../../tests/parity/scenes.json").readText()).map { arrayOf<Any>(it.groupValues[1]) }.toList()

@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(qualifiers = "w411dp-h891dp-notnight-xxhdpi")
class LightParity(scene: String) : Parity("light", scene) {
    companion object {
        @JvmStatic @ParameterizedRobolectricTestRunner.Parameters(name = "{0}") fun names() = sceneNames()
    }
}

@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(qualifiers = "w411dp-h891dp-night-xxhdpi")
class DarkParity(scene: String) : Parity("dark", scene) {
    companion object {
        @JvmStatic @ParameterizedRobolectricTestRunner.Parameters(name = "{0}") fun names() = sceneNames()
    }
}
