package dev.baude.android.screenshots

import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.ui.Modifier
import com.github.takahirom.roborazzi.captureScreenRoboImage
import dev.baude.android.BaudeTheme
import dev.baude.android.LocalBaude
import dev.baude.android.Mesh
import dev.baude.android.Settings
import dev.baude.android.TasksPage
import dev.baude.android.TasksState
import dev.baude.android.Update
import dev.baude.android.Wording
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

/** The Tasks page drawn from TaskFixtures (no host: its state filled by hand), light and dark: the list, Deploy site's
 * page, a failed run's log, Add Trigger with Git push picked, Change Task and Change Inputs. Writes <TASKS_OUT, else build/screenshots/tasks>/
 * <theme>/tasks-<name>.png. A render rig, not a test. In android/:
 *     ./gradlew --offline testDebugUnitTest --tests '*TasksMockups*' */
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35])
abstract class TasksMockups(private val theme: String) {
    private lateinit var activity: ComponentActivity
    private val out = File(System.getenv("TASKS_OUT")?.ifBlank { null } ?: "build/screenshots/tasks", theme)

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

    private fun show(state: TasksState) {
        activity.setContent {
            BaudeTheme {
                Box(Modifier.fillMaxSize().background(LocalBaude.current.inside)) { TasksPage("h1", "s1", true, state) }
            }
        }
        settle()
    }

    private fun shoot(name: String) {
        out.mkdirs()
        captureScreenRoboImage(File(out, "tasks-$name.png").path)
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

    @Test fun list() {
        show(TaskFixtures.list())
        shoot("list")
    }

    @Test fun task() {
        show(TaskFixtures.page())
        shoot("task")
    }

    /** The whole of the page, its runs too (a screen tall enough for it). */
    @Test @Config(qualifiers = "+h1450dp") fun taskWhole() {
        show(TaskFixtures.page())
        shoot("task-whole")
    }

    @Test fun run() {
        show(TaskFixtures.failedRun())
        shoot("run")
    }

    @Test fun trigger() {
        show(TaskFixtures.page())
        tap(Wording.Tasks.addTrigger)
        tap(Wording.Tasks.schedule)
        tap(Wording.Tasks.gitPush)
        shoot("trigger")
    }

    @Test fun runForm() {
        show(TaskFixtures.page())
        tap(Wording.Tasks.run)
        shoot("run-form")
    }

    @Test fun change() {
        show(TaskFixtures.page().apply { editing = task })
        shoot("change")
    }

    @Test fun inputs() {
        show(TaskFixtures.page().apply { forming = task })
        shoot("inputs")
    }

    @Test fun delete() {
        show(TaskFixtures.page())
        tap(Wording.Common.delete)
        shoot("delete")
    }
}

@RunWith(RobolectricTestRunner::class)
@Config(qualifiers = "w411dp-h891dp-notnight-xxhdpi")
class LightTasksMockups : TasksMockups("light")

@RunWith(RobolectricTestRunner::class)
@Config(qualifiers = "w411dp-h891dp-night-xxhdpi")
class DarkTasksMockups : TasksMockups("dark")
