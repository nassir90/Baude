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
 * the home screen shows it: the line at its foot, and tapped, what it waits for (the restart paused meanwhile). Light
 * and dark. Writes <RESTART_OUT, else build/screenshots/restart>/<theme>/restart-<name>.png. A render rig, not a test.
 * In android/:
 *     ./gradlew --offline testDebugUnitTest --tests '*RestartMockups*' */
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35])
abstract class RestartMockups(private val theme: String) {
    private lateinit var activity: ComponentActivity
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
    }

    @After fun down() = FixtureConnection.down()

    private fun host(paused: Boolean) {
        val c = FixtureConnection.up(activity, Host(id = "h1", name = "lab-mac", user = "dev", address = "lab-mac"))
        c.info = Msg.Info(id = "h1", name = "lab-mac", home = "/home/dev", restart = Msg.RestartState(by = "head",
            paused = paused, blockers = listOf(
                Msg.RestartBlocker(kind = "chat", session = "s1", name = "Refactor settings", what = "npm test -- --watch=false"),
                Msg.RestartBlocker(kind = "task", session = "s2", name = "Nightly deploy", what = "a task's run"))))
        activity.setContent {
            BaudeTheme { Box(Modifier.fillMaxSize().background(LocalBaude.current.inside)) { Home(open = { _, _ -> }, feedback = {}) } }
        }
        settle()
    }

    private fun shoot(name: String) {
        out.mkdirs()
        captureScreenRoboImage(File(out, "restart-$name.png").path)
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

    @Test fun blockers() {
        host(true)
        tap(Wording.Hosts.restartPaused)
        shoot("blockers")
    }
}

@RunWith(RobolectricTestRunner::class)
@Config(qualifiers = "w411dp-h891dp-notnight-xxhdpi")
class LightRestartMockups : RestartMockups("light")

@RunWith(RobolectricTestRunner::class)
@Config(qualifiers = "w411dp-h891dp-night-xxhdpi")
class DarkRestartMockups : RestartMockups("dark")
