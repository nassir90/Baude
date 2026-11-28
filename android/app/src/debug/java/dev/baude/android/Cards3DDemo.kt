package dev.baude.android

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import kotlinx.coroutines.delay
import org.json.JSONArray
import org.json.JSONObject

/** The 3D cards prototype with fixture cards (Meta's tool cards, change boxes, form cards), for measuring frames on a
 * device: extras n (how many cards, 11) and throw (a card flung every 0.5 s, from 3 s in). */
class Cards3DDemo : ComponentActivity() {
    override fun onCreate(saved: Bundle?) {
        super.onCreate(saved)
        val n = intent.getIntExtra("n", 11)
        val throws = intent.getBooleanExtra("throw", false)
        Room3D.cached = intent.getBooleanExtra("cached", false)
        MetaTools.actions.clear()
        MetaTools.forms.clear()
        val cards: List<@Composable () -> Unit> = List(n) { i ->
            when (i % 3) {
                0 -> {
                    val call = MetaCall("c$i", "c$i", "mcp__meta__move_chats", JSONObject().put("chats", JSONArray().put("a")).put("to", "Garden"))
                    call.state = "done"; call.result = "Moved 1 chat"
                    val a = Action("a$i", "Moved Tidy the release notes $i", "move", JSONObject(), 1)
                    call.actions.add(a.id); MetaTools.actions.add(a)
                    val f: @Composable () -> Unit = { MetaToolCard(call, listOf(a)) }; f
                }
                1 -> {
                    val a = Action("b$i", "Renamed chat $i", "rename", JSONObject(), 1)
                    a.kept.value = false
                    a.marks.add(JSONArray().put("chat").put("h1/s$i").put("moved").put(""))
                    a.marks.add(JSONArray().put("chat").put("h1/t$i").put("added").put("new"))
                    MetaTools.actions.add(a)
                    val f: @Composable () -> Unit = { ChangeBox(listOf(a)) }; f
                }
                else -> {
                    val id = "f$i"
                    MetaTools.forms[id] = JSONObject().put("id", id).put("form", "new_group")
                        .put("values", JSONObject().put("name", "Seedlings $i")).put("state", "open").put("pinned", false).put("filled", 0)
                    val f: @Composable () -> Unit = { MetaFormCard(id) }; f
                }
            }
        }
        setContent {
            BaudeTheme {
                Cards3DRoom(n, cardWidth = 240) { i -> cards[i]() }
                if (throws) LaunchedEffect(Unit) {
                    delay(3000)
                    while (true) { Room3D.throwOne(); delay(500) }
                }
            }
        }
    }
}
