package dev.baude.android.screenshots

import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.ui.Modifier
import androidx.compose.foundation.layout.padding
import androidx.compose.ui.unit.dp
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
        dev.baude.android.LogWrap.on = true
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

    /** Typed task variables: the list's warning, the task's Variables, Bind with the host's models, and the run's warning. */
    @Test fun variablesList() {
        show(TaskFixtures.variablesList())
        shoot("variables-list")
    }

    @Test fun variables() {
        show(TaskFixtures.variablesPage())
        shoot("variables")
    }

    @Test fun bind() {
        show(TaskFixtures.bind())
        shoot("bind")
    }

    @Test fun variablesRun() {
        show(TaskFixtures.variablesRun())
        shoot("variables-run")
    }

    @Test fun run() {
        show(TaskFixtures.failedRun())
        shoot("run")
    }

    /** A task whose run waits for input: its row, its run's row and the run's page, each with the hourglass. */
    @Test fun waitingList() {
        show(TaskFixtures.waitingList())
        shoot("waiting-list")
    }

    @Test fun waitingTask() {
        show(TaskFixtures.waitingPage())
        shoot("waiting-task")
    }

    @Test fun waitingRun() {
        show(TaskFixtures.waitingRun())
        shoot("waiting-run")
    }

    /** A run that started one-shot agents: Started, each with its state, and what the done one said. */
    @Test fun runStarted() {
        dev.baude.android.Mesh.sessions["h1"] = TaskFixtures.started
        try {
            show(TaskFixtures.startedRun())
            shoot("run-started")
        } finally {
            dev.baude.android.Mesh.sessions.remove("h1")
        }
    }

    /** A chat's messages from a task's run and from chats it started: the agent's chat's first message (from the task,
     * a link to its run), and the Bot's chat told what two of them said (finished, failed): only their words, the
     * chat and the run linked in the from line. */
    @Test fun senders() {
        fun told(chat: String, title: String, over: String) = dev.baude.android.Msg.StoredSender(kind = "subagent",
            name = title, host = "h1", hostName = "workshop", session = chat, bot = "s1", task = "t1",
            taskName = "Deploy site", run = "r3", over = over)
        activity.setContent {
            BaudeTheme {
                androidx.compose.foundation.layout.Column(Modifier.fillMaxSize().background(LocalBaude.current.inside)
                    .padding(top = 24.dp), verticalArrangement = androidx.compose.foundation.layout.Arrangement.spacedBy(12.dp)) {
                    dev.baude.android.Said(dev.baude.android.Line("you", "The deploy of site failed at its upload. Find out why."),
                        dev.baude.android.Msg.StoredSender(kind = "host", name = "Task “Deploy site”", host = "h1",
                            hostName = "workshop", session = "s1", task = "t1", run = "r3"))
                    dev.baude.android.Said(dev.baude.android.Line("you", "The bucket's key expired yesterday. Renew it, then run the task again."),
                        told("c1", "Why the deploy failed", "finished"))
                    dev.baude.android.Said(dev.baude.android.Line("you", "Failed to authenticate: this login reached its time limit."),
                        told("c2", "Renew the upload key", "failed"))
                }
            }
        }
        settle()
        shoot("senders")
    }

    /** A Bot's chat asked by its task's run (a form to fill), answered with its answer_task tool (the Answered card), then
     * handed the run's failure: each heads with what the task did and a table of the run, then the script's words. The
     * Bot's first turn answers in words only, so it's reminded, then answers with the tool. */
    @Test fun asked() = asked(false)

    /** asked, as it is while the run waits on the reminder (its from line says so), before the tool's answer. */
    @Test fun reminded() = asked(true)

    private fun asked(waiting: Boolean) {
        val answer = dev.baude.android.Call("a1", dev.baude.android.ANSWER_TASK,
            org.json.JSONObject("""{"run": "625623c87b8e486e", "answer": {"bucket": "staging"}}"""))
        answer.result("""{"task": "Deploy site", "run": "625623c87b8e486e", "values": {"bucket": "staging"}}""", false, emptyList())
        val table = "| task | 6def981555c9 |\n|---|---|\n| run | 625623c87b8e486e |\n| start time | 2026-11-26 23:42:25 |\n"
        val tail = "| version | 2009f5f91a9f |\n" +
            "| command | /usr/bin/python3 -u /home/ada/.local/share/baude/bots/s1/tasks/scripts/2009f5f91a9f.py |\n"
        val from = dev.baude.android.Msg.StoredSender(kind = "host", name = "Task “Deploy site”", host = "h1",
            hostName = "workshop", session = "s1", task = "t1", run = "r3")
        activity.setContent {
            BaudeTheme {
                androidx.compose.foundation.layout.Column(Modifier.fillMaxSize().background(LocalBaude.current.inside)
                    .verticalScroll(androidx.compose.foundation.rememberScrollState(Int.MAX_VALUE)) // (its end: the answer)
                    .padding(top = 24.dp), verticalArrangement = androidx.compose.foundation.layout.Arrangement.spacedBy(12.dp)) {
                    dev.baude.android.Said(dev.baude.android.Line("you", "An instance of the 'Deploy site' task asks:\n\n" +
                        table + "| duration | 0.0 s |\n" + tail + "\nWhich bucket does site go to?\n\n" +
                        "Answer with your mcp__mesh__answer_task tool, these fields in its answer:\n- bucket: one of \"staging\", \"production\""), from)
                    androidx.compose.foundation.layout.Box(Modifier.padding(horizontal = 12.dp)) {
                        dev.baude.android.Prose("Staging, I'd say: production deploys wait for the release.")
                    }
                    dev.baude.android.Said(dev.baude.android.Line("you", "You haven't answered run 625623c87b8e486e with " +
                        "your mcp__mesh__answer_task tool, and the task's waiting on it. Answer with the tool now.\n\n" +
                        "Answer with your mcp__mesh__answer_task tool, these fields in its answer:\n- bucket: one of " +
                        "\"staging\", \"production\""), from, waits = waiting)
                    if (!waiting) {
                        androidx.compose.foundation.layout.Box(Modifier.padding(horizontal = 12.dp)) {
                            dev.baude.android.ToolCard(answer, null, {}, null)
                        }
                        androidx.compose.foundation.layout.Box(Modifier.padding(horizontal = 12.dp)) {
                            dev.baude.android.Prose("Answered: staging.")
                        }
                    }
                    if (!waiting) dev.baude.android.Said(dev.baude.android.Line("you", "An instance of the 'Deploy site' task failed\n\n" +
                        table + "| end time | 2026-11-26 23:42:37 |\n| duration | 12.4 s |\n" + tail +
                        "| exit code | 3 |\n\nIt failed: exited with 3. The end of its log:\n\nsay: deploying site\n" +
                        "err: the upload was refused: the bucket's key expired\n\nFind out why. If the script is at fault, " +
                        "fix it with your set_task tool and run it again with run_task."), from)
                }
            }
        }
        settle()
        shoot(if (waiting) "reminded" else "asked")
    }

    /** A Bot's chat told its task's run finished (Notify on graceful exit): the same heading and table, the end of its output. */
    @Test fun finished() {
        val table = "| task | 6def981555c9 |\n|---|---|\n| run | 625623c87b8e486e |\n| start time | 2026-11-27 00:04:23 |\n"
        val tail = "| version | 2009f5f91a9f |\n" +
            "| command | /usr/bin/python3 -u /home/ada/.local/share/baude/bots/s1/tasks/scripts/2009f5f91a9f.py |\n"
        val from = dev.baude.android.Msg.StoredSender(kind = "host", name = "Task “Deploy site”", host = "h1",
            hostName = "workshop", session = "s1", task = "t1", run = "r4")
        activity.setContent {
            BaudeTheme {
                androidx.compose.foundation.layout.Column(Modifier.fillMaxSize().background(LocalBaude.current.inside)
                    .padding(top = 24.dp)) {
                    dev.baude.android.Said(dev.baude.android.Line("you", "An instance of the 'Deploy site' task finished\n\n" +
                        table + "| end time | 2026-11-27 00:05:11 |\n| duration | 48.0 s |\n" + tail +
                        "| exit code | 0 |\n\nThis is for your information; nothing is asked of you.\n\n" +
                        "It last said:\n\nuploaded 14 files to staging"), from)
                }
            }
        }
        settle()
        shoot("finished")
    }

    /** A run opened from a chat's from line (its Bot's chat, at that run), then Back: the run's own task. */
    @Test fun runBack() {
        dev.baude.android.TaskLinks.pending = listOf("h1", "s1", "t1", "r3")
        val st = TaskFixtures.list()
        show(st)
        tap("Deploy site")
        st.runs.addAll(TaskFixtures.runs) // (as its host would answer: no host here)
        androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
        settle()
        shoot("run-back")
    }

    /** The run's log with Wrap turned off: its long lines scrolled sideways. */
    @Test fun runUnwrapped() {
        show(TaskFixtures.failedRun())
        tap(Wording.CodeView.wrap)
        shoot("run-unwrapped")
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
