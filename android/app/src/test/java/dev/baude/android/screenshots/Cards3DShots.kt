package dev.baude.android.screenshots

import android.graphics.Bitmap
import android.graphics.Canvas
import android.os.Looper
import android.os.SystemClock
import android.view.MotionEvent
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import dev.baude.android.Action
import dev.baude.android.BaudeTheme
import dev.baude.android.Cards3DRoom
import dev.baude.android.ChangeBox
import dev.baude.android.MetaCall
import dev.baude.android.MetaFormCard
import dev.baude.android.MetaToolCard
import dev.baude.android.MetaTools
import dev.baude.android.Room3D
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import org.robolectric.shadows.ShadowChoreographer
import java.io.File
import java.time.Duration

/** The 3D cards prototype on the phone (Cards3DRoom), drawn frame by frame at 30 fps: the view orbiting, then a card
 * dragged by a finger (MotionEvents) and let go, flung into the others. ./gradlew --offline testDebugUnitTest --tests
 * '*Cards3DShots*' writes build/cards3d/{orbit,throw}/NNNN.png, and build/cards3d/times.txt (each frame's
 * time on the JVM). CARDS3D_N sets how many cards (default 11). */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35], qualifiers = "w411dp-h891dp-notnight-xxhdpi")
class Cards3DShots {
    private lateinit var activity: ComponentActivity
    private val looper get() = shadowOf(Looper.getMainLooper())
    private val out = File("build/cards3d")
    private val times = ArrayList<String>()

    private fun card(i: Int): @androidx.compose.runtime.Composable () -> Unit {
        when (i % 3) {
            0 -> {
                val call = MetaCall("c$i", "c$i", "mcp__meta__move_chats", JSONObject().put("chats", JSONArray().put("a")).put("to", "Garden"))
                call.state = "done"
                call.result = "Moved 1 chat"
                val a = Action("a$i", "Moved Tidy the release notes $i", "move", JSONObject(), 1)
                call.actions.add(a.id)
                                MetaTools.actions.add(a)
                return { MetaToolCard(call, listOf(a)) }
            }
            1 -> {
                val a = Action("b$i", "Renamed chat $i", "rename", JSONObject(), 1)
                a.kept.value = false
                a.marks.add(JSONArray().put("chat").put("h1/s$i").put("moved").put(""))
                a.marks.add(JSONArray().put("chat").put("h1/t$i").put("added").put("new"))
                MetaTools.actions.add(a)
                return { ChangeBox(listOf(a)) }
            }
            else -> {
                val id = "f$i"
                MetaTools.forms[id] = JSONObject().put("id", id).put("form", "new_group")
                    .put("values", JSONObject().put("name", "Seedlings $i")).put("state", "open").put("pinned", false).put("filled", 0)
                return { MetaFormCard(id) }
            }
        }
    }

    private fun shot(folder: String, i: Int) {
        val t0 = System.nanoTime()
        looper.idleFor(Duration.ofMillis(33))
        val root = activity.window.decorView
        val bitmap = Bitmap.createBitmap(root.width, root.height, Bitmap.Config.ARGB_8888)
        root.draw(Canvas(bitmap))
        val t1 = System.nanoTime()
        times.add("$folder $i ${(t1 - t0) / 1e6}")
        File(out, folder).mkdirs()
        File(out, "$folder/%04d.png".format(i)).outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
    }

    /** Where the words text is drawn, in the window (through the room's 3D layer). */
    private fun where(text: String): android.graphics.RectF {
        fun roots(v: android.view.View): List<androidx.compose.ui.node.RootForTest> = when (v) {
            is androidx.compose.ui.node.RootForTest -> listOf(v)
            is android.view.ViewGroup -> (0 until v.childCount).flatMap { roots(v.getChildAt(it)) }
            else -> emptyList()
        }
        fun all(n: androidx.compose.ui.semantics.SemanticsNode): List<androidx.compose.ui.semantics.SemanticsNode> =
            listOf(n) + n.children.flatMap { all(it) }
        val node = roots(activity.window.decorView).flatMap { all(it.semanticsOwner.unmergedRootSemanticsNode) }.first { n ->
            n.config.getOrElse(androidx.compose.ui.semantics.SemanticsProperties.Text) { emptyList() }.any { text in it.text }
        }
        val b = node.boundsInWindow
        return android.graphics.RectF(b.left, b.top, b.right, b.bottom)
    }

    @Test fun orbitAndThrow() {
        freshCompose()
        ShadowChoreographer.setPaused(true)
        ShadowChoreographer.setFrameDelay(Duration.ofMillis(16))
        activity = Robolectric.buildActivity(ComponentActivity::class.java).setup().get()
        MetaTools.actions.clear()
        MetaTools.forms.clear()
        val n = File("build/cards3d-n").takeIf { it.exists() }?.readText()?.trim()?.toIntOrNull() ?: 11
        val cards = List(n) { card(it) }
        Room3D.time = 0f
        Room3D.orbit = true
        activity.setContent { BaudeTheme { Cards3DRoom(n, cardWidth = 240) { i -> cards[i]() } } }
        settle()
        out.deleteRecursively()
        for (i in 0 until 150) shot("orbit", i)
        // a finger down on a card, dragged up and across, let go mid-move
        val r = where("Renamed chat 4")
        val root = activity.window.decorView
        val t = SystemClock.uptimeMillis()
        var x = r.centerX()
        var y = r.centerY()
        root.dispatchTouchEvent(MotionEvent.obtain(t, t, MotionEvent.ACTION_DOWN, x, y, 0))
        var f = 0
        shot("throw", f++)
        for (k in 1..12) {
            x += 18f; y -= 30f + k * 2f
            root.dispatchTouchEvent(MotionEvent.obtain(t, SystemClock.uptimeMillis(), MotionEvent.ACTION_MOVE, x, y, 0))
            shot("throw", f++)
        }
        root.dispatchTouchEvent(MotionEvent.obtain(t, SystemClock.uptimeMillis(), MotionEvent.ACTION_UP, x, y, 0))
        for (k in 0 until 110) shot("throw", f++)
        File(out, "times.txt").writeText(times.joinToString("\n"))
    }
}
