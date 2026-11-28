package dev.baude.android.screenshots

import android.graphics.Bitmap
import android.graphics.Canvas
import android.os.Looper
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import dev.baude.android.BaudeTheme
import dev.baude.android.LocalBaude
import dev.baude.android.SceneModel
import dev.baude.android.SceneRoom
import dev.baude.android.StatusDot
import dev.baude.android.Type
import org.json.JSONObject
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.io.File
import java.time.Duration

/** The semantic themes framework's sample scene (tests/semantic/three-chats/scene.json, the desktop's too) drawn on
 * the phone from its data: three chats as panels on a lit floor, light and dark, at two moments of its orbit; then
 * the same chats as boxes a tap opens (pick reveal). ./gradlew --offline testDebugUnitTest --tests '*SceneThemeShots*'
 * writes its pictures in build/scenetheme. */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35], qualifiers = "w411dp-h891dp-notnight-xxhdpi")
class SceneThemeShots {
    private val out = File("build/scenetheme")
    private val world = listOf(
        JSONObject("""{"kind": "chat", "id": "h1/a", "host": "h1", "name": "Fix the flaky upload", "state": "working", "used": 3}"""),
        JSONObject("""{"kind": "chat", "id": "h1/b", "host": "h1", "name": "Tidy the release notes", "state": "waiting", "used": 2}"""),
        JSONObject("""{"kind": "chat", "id": "h1/c", "host": "h1", "name": "Garden planner", "state": "idle", "bot": true, "used": 1}"""),
    )
    private val said = mapOf("h1/a" to ("The upload test fails one run in five. Can you find why?" to "It races the temp folder's cleanup."),
        "h1/b" to ("Tidy the release notes for 2.4" to "Shall I fold the three bug-fix lines into one?"),
        "h1/c" to ("What should I plant in March?" to "Broad beans, onion sets and early potatoes."))

    /** The sample's chat panel, of the phone's own parts: the dot, the name, a message and its reply. */
    @Composable
    private fun chat(obj: JSONObject) {
        val c = LocalBaude.current
        val (q, a) = said[obj.getString("id")] ?: ("" to "")
        Column(Modifier.fillMaxSize().background(c.inside).padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                StatusDot(obj.optString("state"), obj.optString("state") == "waiting")
                Spacer(Modifier.width(6.dp))
                BasicText(obj.getString("name"), style = Type.bold.copy(color = c.fg))
            }
            Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
                BasicText(q, Modifier.background(c.bubble, RoundedCornerShape(12.dp)).padding(10.dp, 6.dp), style = Type.body.copy(color = c.fg))
            }
            BasicText(a, style = Type.body.copy(color = c.fg))
        }
    }

    private fun shot(activity: ComponentActivity, name: String) {
        repeat(10) { shadowOf(Looper.getMainLooper()).idleFor(Duration.ofMillis(16)) }
        val root = activity.window.decorView
        val bitmap = Bitmap.createBitmap(root.width, root.height, Bitmap.Config.ARGB_8888)
        root.draw(Canvas(bitmap))
        out.mkdirs()
        File(out, "$name.png").outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
    }

    private fun scene() = JSONObject(File("../../tests/semantic/three-chats/scene.json").readText())

    private fun threeChats(name: String) {
        freshCompose()
        val activity = Robolectric.buildActivity(ComponentActivity::class.java).setup().get()
        val model = SceneModel(scene(), world)
        for (t in listOf(0f, 10f)) {
            activity.setContent { BaudeTheme { SceneRoom(model, time = t) { _, obj -> chat(obj) } } }
            settle()
            shot(activity, "scene-$name-${t.toInt()}")
        }
    }

    /** (One test: a second in the same run never settles.) */
    @Test fun scenes() {
        org.robolectric.shadows.ShadowChoreographer.setPaused(true) // (frames only as the clock's moved: the dots pulse forever)
        org.robolectric.shadows.ShadowChoreographer.setFrameDelay(Duration.ofMillis(16))
        threeChats("light")
        boxesOpen()
    }

    private fun boxesOpen() {
        freshCompose()
        val activity = Robolectric.buildActivity(ComponentActivity::class.java).setup().get()
        val s = scene()
        s.getJSONObject("camera").remove("orbit")
        val gifts = JSONObject("""{"id": "gifts", "position": [0, 1.07, 0], "material": "panel",
            "slots": {"of": "chat", "limit": 2, "layout": "row", "spacing": 0.3, "hidden": true,
              "panel": {"show": "chat", "px": [400, 520], "size": [1.54, 2.0]},
              "with": [{"id": "box", "mesh": "box", "material": "lamp", "position": [0, -0.6, 0], "scale": [0.8, 0.8, 0.8], "pick": "reveal"}]}}""")
        val nodes = s.getJSONArray("nodes")
        val keep = org.json.JSONArray()
        for (i in 0 until nodes.length()) if (nodes.getJSONObject(i).getString("id") in setOf("floor", "lamp")) keep.put(nodes.getJSONObject(i))
        s.put("nodes", keep.put(gifts))
        val model = SceneModel(s, world)
        activity.setContent { BaudeTheme { SceneRoom(model, time = 0f) { _, obj -> chat(obj) } } }
        settle()
        shot(activity, "boxes-before")
        model.picked(model.nodes.getValue("gifts/0/box")) {}
        settle()
        shot(activity, "boxes-after")
        check(!model.nodes.getValue("gifts/0/panel").hidden && model.nodes.getValue("gifts/0/box").hidden)
    }
}
