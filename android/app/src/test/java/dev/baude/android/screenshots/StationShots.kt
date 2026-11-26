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
import dev.baude.android.Action
import dev.baude.android.BaudeTheme
import dev.baude.android.Call
import dev.baude.android.ChangeBox
import dev.baude.android.LocalBaude
import dev.baude.android.MetaCall
import dev.baude.android.MetaFormCard
import dev.baude.android.MetaToolCard
import dev.baude.android.MetaTools
import dev.baude.android.SceneModel
import dev.baude.android.SceneRoom
import dev.baude.android.StatusDot
import dev.baude.android.ToolCard
import dev.baude.android.Type
import org.json.JSONArray
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

/** The Space Station scene theme (tests/semantic/space-station/scene.json, the desktop's too) drawn on the phone from
 * its data, at two moments of its orbit: the chats on its front deck (a Bash card in one: the phone's ToolCard), the
 * sessions' rows on the left, Meta's cards on the right (MetaToolCard, ChangeBox, MetaFormCard), the host on the lab
 * module. The desktop's windows aren't the phone's, so the dock has none. ./gradlew --offline testDebugUnitTest --tests
 * '*StationShots*' writes build/scenetheme/station-*.png. */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35], qualifiers = "w411dp-h891dp-notnight-xxhdpi")
class StationShots {
    private val out = File("build/scenetheme")
    private val sessions = listOf( // (as the host's sessions answer has them, and station.py's)
        Triple("a", "Fix the flaky upload", "working"), Triple("b", "Tidy the release notes", "waiting"),
        Triple("c", "Garden planner", "idle"), Triple("d", "Port the CI to arm64", "idle"),
        Triple("e", "Benchmark the parser", "stopped"), Triple("f", "Old notes", "stopped"),
        Triple("g", "Draft the March newsletter", "stopped"))
    private val world = sessions.mapIndexed { i, (id, name, state) ->
        JSONObject().put("kind", "chat").put("id", "h1/$id").put("host", "h1").put("name", name).put("state", state).put("used", 100 - i)
    } + listOf(JSONObject().put("kind", "host").put("id", "h1").put("name", "workshop").put("state", "up")) +
        (0..2).map { JSONObject().put("kind", "meta_card").put("id", "m$it").put("name", "card $it").put("state", "done").put("used", 50 - it) }
    private val said = mapOf("h1/a" to ("The upload test fails one run in five. Can you find why?" to "It races the temp folder's cleanup."),
        "h1/b" to ("Tidy the release notes for 2.4" to "Shall I fold the three bug-fix lines into one?"),
        "h1/c" to ("What should I plant in March?" to "Broad beans, onion sets and early potatoes."))
    private val bash = Call("t1", "Bash", JSONObject().put("command", "pytest tests/test_upload.py -q")).also {
        it.result("3 passed in 0.41s", false, emptyList())
    }

    /** A chat, of the phone's own parts: the dot, the name, a message, its reply, and (the working one's) its Bash card. */
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
            if (obj.getString("id") == "h1/a") ToolCard(bash, null, {}, null)
        }
    }

    /** A sessions row: the dot and the name, as the phone's list has them. */
    @Composable
    private fun row(obj: JSONObject) {
        val c = LocalBaude.current
        Row(Modifier.fillMaxSize().background(c.inside).padding(horizontal = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            BasicText(obj.getString("name"), Modifier.weight(1f), style = Type.body.copy(color = c.fg))
            StatusDot(obj.optString("state"), obj.optString("state") == "waiting")
        }
    }

    /** A host: its name and its chats' rows. */
    @Composable
    private fun host(obj: JSONObject) {
        val c = LocalBaude.current
        Column(Modifier.fillMaxSize().background(c.inside).padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            BasicText(obj.getString("name"), style = Type.bold.copy(color = c.fg))
            for (o in world.filter { it.optString("kind") == "chat" && it.optString("host") == obj.getString("id") }) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    StatusDot(o.optString("state"), o.optString("state") == "waiting")
                    Spacer(Modifier.width(6.dp))
                    BasicText(o.getString("name"), style = Type.body.copy(color = c.fg))
                }
            }
        }
    }

    private val cards: Map<String, @Composable () -> Unit> by lazy {
        val call = MetaCall("c0", "c0", "mcp__meta__move_pages", JSONObject().put("pages", JSONArray().put("Notes/Plan 0")).put("to", "Archive"))
        call.state = "done"
        call.result = "Moved 1 page"
        val moved = Action("a0", "Moved page Plan 0", "move", JSONObject(), 1)
        call.actions.add(moved.id)
        MetaTools.actions.add(moved)
        val added = Action("b1", "Added page Ideas 1", "add", JSONObject(), 1)
        added.kept.value = false
        added.marks.add(JSONArray().put("page").put("Notes/Ideas 1").put("added").put(""))
        added.marks.add(JSONArray().put("page").put("Notes/Todo 1").put("archived").put(""))
        MetaTools.actions.add(added)
        MetaTools.forms["f2"] = JSONObject().put("id", "f2").put("form", "new_group")
            .put("values", JSONObject().put("name", "Seedlings")).put("state", "open").put("pinned", false).put("filled", 0)
        val m0: @Composable () -> Unit = { MetaToolCard(call, listOf(moved)) }
        val m1: @Composable () -> Unit = { ChangeBox(listOf(added)) }
        val m2: @Composable () -> Unit = { MetaFormCard("f2") }
        mapOf("m0" to m0, "m1" to m1, "m2" to m2)
    }

    @Composable
    private fun panel(show: String, obj: JSONObject) {
        val c = LocalBaude.current
        when (show) {
            "chat" -> chat(obj)
            "chat_row" -> row(obj)
            "host" -> host(obj)
            "meta_card" -> Box(Modifier.fillMaxSize().background(c.inside).padding(8.dp)) { cards[obj.getString("id")]?.invoke() }
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

    @Test fun station() {
        org.robolectric.shadows.ShadowChoreographer.setPaused(true) // (frames only as the clock's moved: the dots pulse forever)
        org.robolectric.shadows.ShadowChoreographer.setFrameDelay(Duration.ofMillis(16))
        freshCompose()
        val activity = Robolectric.buildActivity(ComponentActivity::class.java).setup().get()
        val model = SceneModel(JSONObject(File("../../tests/semantic/space-station/scene.json").readText()), world)
        for (t in listOf(0f, 12f)) {
            val started = System.nanoTime()
            activity.setContent { BaudeTheme { SceneRoom(model, time = t) { show, obj -> panel(show, obj) } } }
            settle()
            shot(activity, "station-${t.toInt()}")
            File(out, "station-${t.toInt()}.txt").writeText("${(System.nanoTime() - started) / 1_000_000} ms to compose, draw and settle\n")
        }
        check(model.nodes.values.count { it.panel != null } == 13) // (3 chats, 6 rows, 3 cards, the host: no window)
    }
}
