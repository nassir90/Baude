package dev.baude.android.screenshots

import android.os.Looper
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.Composable
import com.github.takahirom.roborazzi.captureRoboImage
import dev.baude.android.BaudeTheme
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
        val now = System.currentTimeMillis() / 1000.0
        fun s(id: String, title: String, cwd: String, ago: Double, state: String = "idle") =
            Msg.Session(id = id, title = title, cwd = cwd, updated = now - ago, said = now - ago, created = now - ago - 600,
                state = state)
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
