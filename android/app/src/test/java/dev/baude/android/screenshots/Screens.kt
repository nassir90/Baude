package dev.baude.android.screenshots

import android.os.Looper
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.Composable
import com.github.takahirom.roborazzi.captureRoboImage
import dev.baude.android.Artifact
import dev.baude.android.BaudeTheme
import dev.baude.android.Chat
import dev.baude.android.ChatShell
import dev.baude.android.Line
import dev.baude.android.SideQuestion
import dev.baude.android.Subagent
import dev.baude.android.Home
import dev.baude.android.Host
import dev.baude.android.Key
import dev.baude.android.Mesh
import dev.baude.android.Msg
import dev.baude.android.Settings
import dev.baude.android.SettingsScreen
import dev.baude.android.Update
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
import java.time.Duration
import java.security.KeyPairGenerator
import java.security.interfaces.ECPublicKey

/** The app's screens drawn on the JVM (Robolectric, its native graphics) with fixture data, saved as PNGs by
 * Roborazzi: a render rig, not tests (nothing's checked). ./gradlew --offline screenshots (in android/) writes
 * app/build/screenshots/<theme>/<name>.png, light and dark (LightScreens, DarkScreens). The app's own state is set
 * as a phone would have it (Mesh's hosts and sessions, Update's, Settings'), never fetched: nothing's reached over
 * the network. */
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35])
abstract class Screens(private val theme: String) {
    private lateinit var activity: ComponentActivity

    @Before fun up() {
        freshCompose()
        ShadowChoreographer.setPaused(true)
        ShadowChoreographer.setFrameDelay(Duration.ofMillis(16))
        activity = Robolectric.buildActivity(ComponentActivity::class.java).setup().get()
        Mesh.app = activity.applicationContext
        Settings.load(activity)
        Settings.accent.value = null // (each screen from the settings a phone starts with)
        Settings.appearance.value = "system"
        Settings.textScale.value = 1f
        Settings.globalScale.value = 1f
        Settings.notify.value = true
        Settings.meteredUpdates.value = false
        Update.state.value = ""
        fixtures()
    }

    @After fun down() {
        FixtureConnection.down()
        Mesh.hosts.clear()
        Mesh.sessions.clear()
        Mesh.key.value = null
    }

    @Test fun home() = render("01-home") { HomeScreen() }

    @Test fun homeUpdate() {
        Update.state.value = "ready"
        render("02-home-update") { HomeScreen() }
    }

    @Test fun homeDownloading() {
        Update.state.value = "downloading"
        Update.progress.value = 0.4f
        render("03-home-downloading") { HomeScreen() }
    }

    @Test fun homeWrongKey() {
        Update.state.value = "wrong"
        Update.why.value = "Can't update: the new version is signed with another key"
        render("04-home-wrong-key") { HomeScreen() }
    }

    @Test fun settings() = render("05-settings") { SettingsScreen({}, {}) }

    @Test fun settingsPicked() {
        Settings.accent.value = "green"
        Settings.meteredUpdates.value = true
        Settings.textScale.value = 1.2f
        render("06-settings-green-larger") { SettingsScreen({}, {}) }
    }

    @Test fun homeLarger() {
        Settings.globalScale.value = 1.3f
        render("07-home-global-scale") { HomeScreen() }
    }

    @Test fun settingsDark() {
        Settings.appearance.value = if (theme == "light") "dark" else "light" // (against the phone's)
        Settings.accent.value = "orange"
        render("08-settings-appearance-pinned") { SettingsScreen({}, {}) }
    }

    /** Chats nested under the ones that forked or started them, two deep, with siblings after them (so a parent's
     * line runs past a child's subtree to its last child). */
    @Test fun homeNested() {
        val now = System.currentTimeMillis() / 1000.0
        fun s(id: String, title: String, cwd: String, ago: Double, fork: String? = null, by: String? = null,
              state: String = "idle") = Msg.Session(id = id, title = title, cwd = cwd, updated = now - ago, said = now - ago,
            created = now - ago - 600, state = state, forkFrom = fork, spawnedBy = by)
        Mesh.sessions["h1"] = listOf(
            s("a", "Tidy the release notes", "/home/dev/garden", 60.0, state = "working"),
            s("a1", "Check the changelog links", "/home/dev/garden", 120.0, by = "a"),
            s("a1x", "Read the old tags", "/home/dev/garden", 130.0, by = "a1"),
            s("a1y", "List the broken links", "/home/dev/garden", 140.0, by = "a1"),
            s("a2", "Shorter release notes", "/home/dev/garden", 300.0, fork = "a"),
            s("b", "Why the build is slow", "/home/dev/garden", 3_600.0),
            s("c", "Draft the README", "/home/dev/lantern", 7_200.0),
            s("c1", "README, plainer", "/home/dev/lantern", 7_300.0, fork = "c"),
            s("d", "Fix the flaky upload", "/home/dev/lantern", 86_400.0, state = "waiting"),
        )
        render("09-home-nested") { HomeScreen() }
    }

    // (a chat open on workshop, which is up: each of its pages, as its bottom bar switches to them)
    @Test fun chat() = chatAt("10-chat", "chat")
    @Test fun chatMeta() = chatAt("11-chat-meta", "meta")
    @Test fun chatSubagents() = chatAt("12-chat-subagents", "agents")
    @Test fun chatProcesses() = chatAt("13-chat-processes", "processes")
    @Test fun chatSideQuestions() = chatAt("14-chat-side-questions", "btw")
    @Test fun chatArtifacts() = chatAt("15-chat-artifacts", "artifacts")
    @Test fun chatFiles() = chatAt("16-chat-files", "files")
    @Test fun chatGit() = chatAt("17-chat-git", "git")
    @Test fun chatSignal() = chatAt("18-chat-signal", "signal")

    /** The fixture chat ("Tidy the release notes") open at page, its host up: what was said in it, its subagents,
     * side questions and artifacts. */
    private fun chatAt(name: String, page: String) {
        val chat = Mesh.openChat("h1", "a")
        fill(chat)
        render(name) { ChatShell("h1", "a", back = {}, open = { _, _ -> }, feedback = {}, first = page) }
    }

    private fun fill(chat: Chat) {
        chat.lines.addAll(listOf(
            Line("you", "Tidy the release notes for 0.9: group them by area and drop the internal ones."),
            Line("tool", "Read CHANGELOG.md"),
            Line("tool", "Bash git log --oneline v0.8..HEAD"),
            Line("bot", "Here's a tidier draft, grouped by area:\n\n**Sync**\n- Uploads resume after a dropped " +
                "connection\n- Faster first sync on large folders\n\n**Settings**\n- A larger default size on phones\n\n" +
                "I left out the three internal refactors. Want me to write it to `CHANGELOG.md`?"),
            Line("you", "Yes, and link each item to its PR."),
            Line("tool", "Edit CHANGELOG.md"),
        ))
        chat.agents.addAll(listOf(
            Subagent("t1", "Find each item's PR", "general-purpose", "Match each changelog line to its pull request")
                .apply { activity.value = "Searching merged pull requests" },
            Subagent("t2", "Check the links", "general-purpose", "Open every link in the notes").apply { status.value = "completed" },
            Subagent("t3", "Spell-check the notes", "general-purpose", "Spell-check CHANGELOG.md").apply { status.value = "failed" },
        ))
        chat.btw.addAll(listOf(
            SideQuestion("Is 0.9 a minor or a patch release?").apply {
                status.value = "done"
                thread.add(Line("bot", "Minor: it adds resumable uploads, a new feature."))
            },
            SideQuestion("Which PRs touched sync?").apply { status.value = "running" },
        ))
        chat.artifacts.addAll(listOf(
            Artifact("https://claude.ai/artifact/release-notes", "Release notes 0.9", icon = "doc",
                description = "The notes as they'll be published"),
            Artifact("https://claude.ai/artifact/sync-times", "First sync times", icon = "chart",
                description = "Before and after, by folder size"),
        ))
    }

    @Composable
    private fun HomeScreen() = Home(open = { _, _ -> }, hosts = {}, feedback = {})

    private fun render(name: String, screen: @Composable () -> Unit) {
        show(screen)
        activity.window.decorView.captureRoboImage("build/screenshots/$theme/$name.png")
    }

    private fun show(screen: @Composable () -> Unit) {
        activity.setContent { BaudeTheme { screen() } }
        settle()
    }

    /** Compose's main-thread dispatchers made again, and its snapshot manager started again on them. Each keeps the
     * Choreographer it was made with, and Robolectric makes a new one each test: after the first test, nothing was
     * composed again once drawn (no effect ran, no state change showed, insets never arrived: a chat's bottom bar,
     * hidden while the keyboard's up, never showed). */
    private fun freshCompose() {
        val ui = androidx.compose.ui.platform.AndroidUiDispatcher::class.java
        (ui.getDeclaredField("currentThread").apply { isAccessible = true }.get(null) as ThreadLocal<*>).remove()
        val main = ui.getDeclaredField("Main\$delegate").apply { isAccessible = true }.get(null)
        val make = Class.forName("androidx.compose.ui.platform.AndroidUiDispatcher\$Companion\$Main\$2")
            .getDeclaredField("INSTANCE").apply { isAccessible = true }.get(null) as () -> Any
        main.javaClass.getDeclaredField("_value").apply { isAccessible = true }.set(main, make())
        (Class.forName("androidx.compose.ui.platform.GlobalSnapshotManager").getDeclaredField("started")
            .apply { isAccessible = true }.get(null) as java.util.concurrent.atomic.AtomicBoolean).set(false)
    }

    /** What's posted run, and the clock moved on a frame at a time (a frame each 16ms: an animation that asks for
     * frames, a menu opening, runs out instead of asking for them at one moment forever). */
    private fun settle() {
        repeat(40) { shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(16)) }
    }

    /** Two hosts of fixture names, and their sessions, as a phone that's connected to them lists them. */
    private fun fixtures() {
        val pair = KeyPairGenerator.getInstance("EC").apply { initialize(256) }.generateKeyPair()
        Mesh.key.value = Key(pair.private, pair.public as ECPublicKey)
        val workshop = Host(id = "h1", name = "workshop", user = "dev", address = "workshop.example")
        Mesh.hosts.add(workshop)
        FixtureConnection.up(activity, workshop) // (up, never reached)
        val now = System.currentTimeMillis() / 1000.0
        fun s(id: String, title: String, cwd: String, ago: Double, state: String = "idle") =
            Msg.Session(id = id, title = title, cwd = cwd, updated = now - ago, said = now - ago, created = now - ago - 600,
                state = state, model = "opus", effort = "high", mode = "default")
        Mesh.sessions["h1"] = listOf(
            s("a", "Tidy the release notes", "/home/dev/garden", 60.0, "working"),
            s("b", "Why the build is slow", "/home/dev/garden", 3_600.0),
            s("c", "Draft the README", "/home/dev/lantern", 7_200.0),
            s("d", "Fix the flaky upload", "/home/dev/lantern", 86_400.0, "waiting"),
            s("e", "Sketch the settings page", "/home/dev/lantern", 172_800.0),
        )
    }
}

@RunWith(RobolectricTestRunner::class)
@Config(qualifiers = "w411dp-h891dp-notnight-xxhdpi")
class LightScreens : Screens("light")

@RunWith(RobolectricTestRunner::class)
@Config(qualifiers = "w411dp-h891dp-night-xxhdpi")
class DarkScreens : Screens("dark")

