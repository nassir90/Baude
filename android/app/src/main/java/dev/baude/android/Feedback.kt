package dev.baude.android

import android.content.Context
import android.os.Build
import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

/** Feedback written here and not filed yet, kept on this phone (filesDir/feedback/<id>.json) and tried again till
 * it's filed, as Baude's keep_filing tries its notes: every minute, and as a host comes back. A note's tried through
 * the host it was last sent to, and another only once that's been away a while (a host files a note once, claiming
 * it while gh runs; two hosts can't see each other's claims). */
object Outbox {
    private val going = HashSet<String>() // (notes asked of a host now: not asked again till it answers)
    private var started = false
    private const val STICK_MS = 5 * 60_000L // (how long a note waits for the host it was sent through)

    private fun dir() = java.io.File(Mesh.app.filesDir, "feedback").apply { mkdirs() }

    fun keep(id: String, text: String, context: List<String>, host: String) {
        val o = JSONObject().put("id", id).put("text", text).put("context", JSONArray(context)).put("host", host)
            .put("tried", System.currentTimeMillis())
        val f = java.io.File(dir(), "$id.json")
        o.put("kept", try { JSONObject(f.readText()).optLong("kept", System.currentTimeMillis()) } catch (_: Exception) { System.currentTimeMillis() })
        f.writeText(o.toString())
    }

    /** Each note kept and not filed yet: its id, its text, and when it was kept (ms). */
    fun listed(): List<JSONObject> = notes().filter { !it.has("filed") }.map { o ->
        JSONObject().put("id", o.optString("id")).put("text", o.optString("text")).put("kept", o.optLong("kept", o.optLong("tried")))
    }

    private fun notes(): List<JSONObject> = (dir().listFiles() ?: emptyArray()).filter { it.name.endsWith(".json") }
        .mapNotNull { f -> try { JSONObject(f.readText()) } catch (_: Exception) { null } }

    /** A note sent through host: what it said, to then (null: filed). */
    fun send(id: String, host: String, then: (JSONObject) -> Unit) {
        val f = java.io.File(dir(), "$id.json")
        val o = try { JSONObject(f.readText()) } catch (_: Exception) {
            return then(Msg.Error(error = "it isn't kept here any more").json()) }
        if (!going.add(id)) return then(Msg.Error(error = "it's being sent already").json())
        o.put("host", host).put("tried", System.currentTimeMillis())
        f.writeText(o.toString())
        Mesh.ask(host, Msg.Feedback(note = id, text = o.optString("text"), context = o.optJSONArray("context").strings())
            .json()) { r ->
            going.remove(id)
            if (r.optString("op") == Op.FEEDBACK_FILED) f.delete() // (filed: nothing more kept of it here)
            then(r)
        }
    }

    /** Every note not filed yet tried again (through its host, or, that one away long enough, another). */
    fun again() {
        val up = Mesh.reached("feedback").map { it.first }.filter { h -> Mesh.hosts.any { it.id == h } }
        for (o in notes()) {
            if (o.has("filed")) { java.io.File(dir(), "${o.optString("id")}.json").delete(); continue } // (from before)
            val was = o.optString("host")
            val h = if (was in up) was
                else if (was.isEmpty() || System.currentTimeMillis() - o.optLong("tried") > STICK_MS) up.firstOrNull() else null
            h?.let { send(o.optString("id"), it) {} }
        }
    }

    fun start() {
        if (started) return
        started = true
        Mesh.listeners.add { _, msg -> if (msg.optString("op") == "connected") Mesh.later(2_000) { again() } }
        fun tick() { again(); Mesh.later(60_000) { tick() } }
        Mesh.later(10_000) { tick() }
    }
}

/** Send Feedback: what should change, and the screen it's about (the context, shown if you ask), filed as an issue
 * by a host's gh (this phone has none), where Baude's Send Feedback files it (fixes.submit_issue_target: the private
 * repository the host's checkout has, if its gh sees it). Its id stays the same while it's tried again, so the host
 * files it once. */
@Composable
fun FeedbackScreen(context: List<String>, back: () -> Unit) {
    val c = LocalBaude.current
    val ctx = LocalContext.current
    var text by rememberSaveable { mutableStateOf("") }
    val id = rememberSaveable { "android-" + UUID.randomUUID().toString().replace("-", "").take(16) }
    Mesh.tick.value
    val up = Mesh.reached("feedback").filter { (h, _) -> Mesh.hosts.any { it.id == h } } // (a host of yours that files it)
    val host = up.firstOrNull()?.first
    var target by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(host) {
        target = null
        val h = host ?: return@LaunchedEffect
        Mesh.ask(h, Msg.Feedback(check = true).json()) { r ->
            if (r.optString("op") == Op.FEEDBACK_TARGET) target = Msg.FeedbackTarget.from(r).repo.orEmpty()
        }
    }
    val full = context + listOf("phone: Baude for Android ${version(ctx)} on Android ${Build.VERSION.RELEASE} (${Build.MODEL})")
    val ready = text.isNotBlank()
    fun save() {
        val app = Mesh.app
        val h = host
        val repo = target
        Outbox.keep(id, text, full, h ?: "")
        back()
        Toast.makeText(app, if (h != null) Wording.Feedback.savedFiling else Wording.Feedback.saved, Toast.LENGTH_LONG).show()
        if (h != null) Outbox.send(id, h) { r ->
            if (r.optString("op") == Op.FEEDBACK_FILED) {
                Toast.makeText(app, Wording.Feedback.filed(repo = Msg.FeedbackFiled.from(r).repo.orEmpty()), Toast.LENGTH_LONG).show()
            } else if (!repo.isNullOrBlank()) {
                Toast.makeText(app, Wording.Feedback.notFiledWhy(repo = repo, why = said(Msg.Error.from(r))), Toast.LENGTH_LONG).show()
            }
        }
    }
    val focus = remember { FocusRequester() }
    LaunchedEffect(Unit) { focus.requestFocus() }
    Column(Modifier.fillMaxSize().background(c.frame).imePadding()) {
        Bar(Wording.Feedback.title, start = { Button(Wording.Common.cancel) { back() } },
            end = { Button(Wording.Common.save, suggested = true, enabled = ready) { save() } })
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 10.dp)) {
            // what should change: a card as the composer is (Baude's Feedback window's one text box)
            Box(Modifier.fillMaxWidth().heightIn(min = 140.dp, max = 280.dp).clip(RoundedCornerShape(Gap.composerRadius))
                .background(c.inside).border(1.dp, c.rule, RoundedCornerShape(Gap.composerRadius)).padding(12.dp)) {
                BasicTextField(text, { text = it }, textStyle = Type.body.copy(color = c.fg),
                    cursorBrush = SolidColor(c.accentBg), modifier = Modifier.fillMaxWidth().heightIn(min = 116.dp)
                        .focusRequester(focus))
            }
        }
    }
}

/** Why a host couldn't file, as it said it. */
private fun said(e: Msg.Error): String = e.said?.ifBlank { null } ?: Wording.BotBrowser.hostSaidNo

private fun version(ctx: Context): String = try {
    ctx.packageManager.getPackageInfo(ctx.packageName, 0).versionName ?: "?"
} catch (_: Exception) {
    "?"
}
