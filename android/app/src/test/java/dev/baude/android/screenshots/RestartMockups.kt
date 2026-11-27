package dev.baude.android.screenshots

import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.ui.Modifier
import com.github.takahirom.roborazzi.captureScreenRoboImage
import dev.baude.android.BaudeTheme
import dev.baude.android.Home
import dev.baude.android.Host
import dev.baude.android.LocalBaude
import dev.baude.android.Mesh
import dev.baude.android.Msg
import dev.baude.android.Settings
import dev.baude.android.Update
import dev.baude.android.Wording
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import org.robolectric.shadows.ShadowChoreographer
import java.io.File
import java.time.Duration

/** A host keeping a restart of its daemon (info's restart: a chat running a command and a task's run hold it up), as
 * the home screen shows it: its snackbar, and Details tapped, what it waits for (the restart paused meanwhile); and
 * each as the host says a chat's command and a task's run have ended (its sessions listing, with its info, heard as a
 * connection hears it). Light and dark. Writes <RESTART_OUT, else build/screenshots/restart>/<theme>/restart-<name>.png
 * (and what's on screen, restart-<name>.txt). A render rig, not a test.
 * In android/:
 *     ./gradlew --offline testDebugUnitTest --tests '*RestartMockups*' */
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35])
abstract class RestartMockups(private val theme: String) {
    private lateinit var activity: ComponentActivity
    private val sent = mutableListOf<String>() // (what h1's asked)
    private val out = File(System.getenv("RESTART_OUT")?.ifBlank { null } ?: "build/screenshots/restart", theme)

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
        Settings.appearance.value = "system"
        Settings.textScale.value = 1f
        Settings.globalScale.value = 1f
        Settings.fullWidth.value = true
        Update.state.value = ""
        dev.baude.android.RestartSnack.toasted.clear() // (shown in a test before: this one's is new)
        dev.baude.android.RestartSnack.held.clear()
        dev.baude.android.RestartSnack.showing = null
    }

    @After fun down() = FixtureConnection.down()

    private fun host(paused: Boolean, other: Boolean = false) {
        val c = FixtureConnection.up(activity, Host(id = "h1", name = "lab-mac", user = "dev", address = "lab-mac")) {
            sent.add(it.toString()); null }
        c.info = Msg.Info(id = "h1", name = "lab-mac", home = "/home/dev", restart = Msg.RestartState(by = "head",
            paused = paused, blockers = listOf(
                Msg.RestartBlocker(kind = "chat", session = "s1", name = "Refactor settings", what = "npm test -- --watch=false"),
                Msg.RestartBlocker(kind = "task", session = "s2", task = "t1", run = "r1", name = "Nightly deploy", what = "a task's run"),
                Msg.RestartBlocker(kind = "task", session = "s2", task = "t2", run = "r2", name = "Release notes",
                    what = "a task's run, waiting for input", waiting = true),
                Msg.RestartBlocker(kind = "agent", session = "s3", host = "h2", name = "apple-foundation",
                    what = "Running apple-foundation for a chat on build-box"),
                Msg.RestartBlocker(kind = "terminal", terminal = "abc123", name = "zsh", what = "/bin/zsh -l"))))
        if (other) {
            val d = FixtureConnection.up(activity, Host(id = "h2", name = "build-box", user = "dev", address = "build-box"))
            d.info = Msg.Info(id = "h2", name = "build-box", home = "/home/dev", restart = Msg.RestartState(by = "update",
                paused = false, blockers = listOf(Msg.RestartBlocker(kind = "chat", session = "s4", name = "Docs pass", what = "make html"))))
        }
        activity.setContent {
            BaudeTheme { Box(Modifier.fillMaxSize().background(LocalBaude.current.inside)) { Home(open = { _, _ -> }, feedback = {}) } }
        }
        settle()
    }

    private fun shoot(name: String) {
        out.mkdirs()
        captureScreenRoboImage(File(out, "restart-$name.png").path)
        File(out, "restart-$name.txt").writeText(words().joinToString("\n"))
    }

    /** Every piece of text on screen (each window's). */
    private fun words(): List<String> {
        val wm = Class.forName("android.view.WindowManagerGlobal").getMethod("getInstance").invoke(null)
        @Suppress("UNCHECKED_CAST")
        val views = wm.javaClass.getDeclaredField("mViews").apply { isAccessible = true }.get(wm) as List<android.view.View>
        fun roots(v: android.view.View): List<androidx.compose.ui.node.RootForTest> = when (v) {
            is androidx.compose.ui.node.RootForTest -> listOf(v)
            is android.view.ViewGroup -> (0 until v.childCount).flatMap { roots(v.getChildAt(it)) }
            else -> emptyList()
        }
        fun all(n: androidx.compose.ui.semantics.SemanticsNode): List<androidx.compose.ui.semantics.SemanticsNode> =
            listOf(n) + n.children.flatMap { all(it) }
        return views.flatMap(::roots).flatMap { all(it.semanticsOwner.rootSemanticsNode) }.flatMap { n ->
            n.config.getOrElse(androidx.compose.ui.semantics.SemanticsProperties.Text) { emptyList() }.map { it.text }
        }
    }

    /** The host saying its restart waits for these only now: its sessions listing, its info in it, heard as h1's
     * connection hears one (Mesh.heard). */
    private fun ended(vararg kinds: String) {
        val c = Mesh.conn("h1")!!
        val was = c.info!!.restart!!
        val info = Msg.Info(id = "h1", name = "lab-mac", home = "/home/dev", restart = Msg.RestartState(by = was.by,
            paused = was.paused, blockers = was.blockers.orEmpty().filter { it.kind !in kinds }))
        val msg = org.json.JSONObject().put("op", dev.baude.android.Op.SESSIONS).put("sessions", org.json.JSONArray())
            .put("server", info.json())
        Mesh::class.java.getDeclaredMethod("heard", dev.baude.android.Connection::class.java, org.json.JSONObject::class.java)
            .apply { isAccessible = true }.invoke(Mesh, c, msg)
        settle() // (what it posted run)
        androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
        settle()
    }

    /** The last thing on screen (the topmost window's first) whose words or description are label, clicked. */
    private fun tap(label: String) {
        val wm = Class.forName("android.view.WindowManagerGlobal").getMethod("getInstance").invoke(null)
        @Suppress("UNCHECKED_CAST")
        val views = wm.javaClass.getDeclaredField("mViews").apply { isAccessible = true }.get(wm) as List<android.view.View>
        fun roots(v: android.view.View): List<androidx.compose.ui.node.RootForTest> = when (v) {
            is androidx.compose.ui.node.RootForTest -> listOf(v)
            is android.view.ViewGroup -> (0 until v.childCount).flatMap { roots(v.getChildAt(it)) }
            else -> emptyList()
        }
        fun all(n: androidx.compose.ui.semantics.SemanticsNode): List<androidx.compose.ui.semantics.SemanticsNode> =
            listOf(n) + n.children.flatMap { all(it) }
        val act = androidx.compose.ui.semantics.SemanticsActions.OnClick
        fun hits() = views.drop(views.indexOf(activity.window.decorView)).flatMap(::roots).map { r ->
            all(r.semanticsOwner.rootSemanticsNode).filter { n ->
                val c = n.config
                c.contains(act) && (c.getOrElse(androidx.compose.ui.semantics.SemanticsProperties.Text) { emptyList() }.any { it.text == label } ||
                    c.getOrElse(androidx.compose.ui.semantics.SemanticsProperties.ContentDescription) { emptyList() }.contains(label))
            }
        }.lastOrNull { it.isNotEmpty() }.orEmpty()
        repeat(20) { if (hits().isEmpty()) settle() }
        hits().first().config[act].action!!.invoke()
        androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
        settle()
    }

    @Test fun pending() {
        host(false)
        shoot("pending")
    }

    @Test fun twoHosts() {
        host(false, other = true)
        shoot("two-hosts")
    }

    @Test fun blockers() {
        host(true)
        tap(Wording.Hosts.restartDetails)
        shoot("blockers")
    }

    @Test fun seeThread() {
        host(false)
        tap(Wording.Hosts.restartDetails)
        tap(Wording.Hosts.seeThread)
        shoot("see-thread")
        tap(Wording.Common.close)
        File(out, "restart-see-thread-sent.txt").writeText(sent.joinToString("\n"))
        shoot("see-thread-dismissed")
    }

    @Test fun snackbarLive() {
        host(false)
        shoot("snackbar-before")
        ended("chat")
        shoot("snackbar-after")
    }

    @Test fun blockersLive() {
        host(true)
        tap(Wording.Hosts.restartDetails)
        shoot("blockers-before")
        ended("chat", "task")
        shoot("blockers-after")
    }
}

@RunWith(RobolectricTestRunner::class)
@Config(qualifiers = "w411dp-h891dp-notnight-xxhdpi")
class LightRestartMockups : RestartMockups("light")

@RunWith(RobolectricTestRunner::class)
@Config(qualifiers = "w411dp-h891dp-night-xxhdpi")
class DarkRestartMockups : RestartMockups("dark")
