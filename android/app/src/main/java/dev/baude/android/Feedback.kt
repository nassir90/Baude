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
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
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
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

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
    var sending by remember { mutableStateOf(false) }
    var failed by remember { mutableStateOf<String?>(null) }
    var showContext by rememberSaveable { mutableStateOf(false) }
    Mesh.tick.value
    val up = Mesh.reached().filter { (h, _) -> Mesh.hosts.any { it.id == h } } // (a host of yours: its gh files it)
    var picked by rememberSaveable { mutableStateOf<String?>(null) }
    val host = picked?.takeIf { p -> up.any { it.first == p } } ?: up.firstOrNull()?.first
    var target by remember { mutableStateOf<String?>(null) }
    var targetWhy by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(host) {
        target = null
        targetWhy = null
        val h = host ?: return@LaunchedEffect
        Mesh.ask(h, JSONObject().put("op", "feedback").put("check", true)) { r ->
            if (r.optString("op") == "feedback_target") target = r.optString("repo")
            else targetWhy = said(h, r.optString("error"))
        }
    }
    val full = context + listOf("phone: Baude for Android ${version(ctx)} on Android ${Build.VERSION.RELEASE} (${Build.MODEL})")
    fun send() {
        val h = host ?: return
        sending = true
        failed = null
        Mesh.ask(h, JSONObject().put("op", "feedback").put("note", id).put("text", text).put("context", JSONArray(full))) { r ->
            sending = false
            if (r.optString("op") == "feedback_filed") {
                Toast.makeText(ctx, "Filed as an issue on ${r.optString("repo")}", Toast.LENGTH_LONG).show()
                back()
            } else failed = said(h, r.optString("error"))
        }
    }
    Column(Modifier.fillMaxSize().background(c.frame).imePadding()) {
        Bar("Feedback", start = { IconButton(Icons.AutoMirrored.Filled.ArrowBack, "Discard", onClick = back) })
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)) {
            context.firstOrNull()?.let { Text(it, style = Type.bold, color = c.fg, modifier = Modifier.padding(top = 10.dp)) }
            // what should change: a card as the composer is (Baude's Feedback window's one text box)
            Box(Modifier.fillMaxWidth().heightIn(min = 140.dp, max = 280.dp).clip(RoundedCornerShape(Gap.composerRadius))
                .background(c.inside).border(1.dp, c.rule, RoundedCornerShape(Gap.composerRadius)).padding(12.dp)) {
                if (text.isEmpty()) Text("What should change?", style = Type.body, color = c.dim)
                BasicTextField(text, { text = it; failed = null }, textStyle = Type.body.copy(color = c.fg),
                    cursorBrush = SolidColor(c.accentBg), modifier = Modifier.fillMaxWidth().heightIn(min = 116.dp))
            }
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(2.dp)) {
                Text("Filed by", style = Type.caption, color = c.dim)
                Picker(host?.let { Mesh.label(it) } ?: "No host connected", "The host whose gh files it",
                    up.map { it.second to it.first }, host, enabled = up.size > 1) { picked = it }
                Text(when {
                    host == null -> ""
                    target != null -> "on $target"
                    targetWhy != null -> ""
                    else -> "…"
                }, style = Type.caption, color = c.dim)
            }
            targetWhy?.let { Text(it, style = Type.caption, color = c.warning) }
            Text(if (showContext) "Hide context" else "Show context", style = Type.body,
                color = c.accent, modifier = Modifier.clip(RoundedCornerShape(6.dp)).clickable { showContext = !showContext }
                    .padding(vertical = 4.dp))
            if (showContext) Text(full.joinToString("\n") { "- $it" }, style = Type.mono, color = c.fg, modifier = Modifier
                .fillMaxWidth().heightIn(max = 160.dp).clip(RoundedCornerShape(Gap.codeRadius)).background(c.raised)
                .verticalScroll(rememberScrollState()).horizontalScroll(rememberScrollState()).padding(8.dp))
            failed?.let { Text("Saved on this phone, but it couldn't be filed: $it", style = Type.caption, color = c.destructive) }
        }
        val ready = text.isNotBlank() && host != null && !sending
        Box(Modifier.fillMaxWidth().navigationBarsPadding().padding(16.dp).height(52.dp).clip(RoundedCornerShape(12.dp))
            .background(if (ready) c.accentBg else c.fg.copy(alpha = 0.10f)).clickable(enabled = ready) { send() },
            contentAlignment = Alignment.Center) {
            Text(when {
                sending -> "Sending…"
                failed != null -> "Try sending again"
                else -> "Send feedback"
            }, style = Type.bold, color = if (ready) c.accentFg else c.dim)
        }
    }
}

/** Why a host couldn't file, as you'd say it (an older host's Baude doesn't know the op). */
private fun said(host: String, error: String): String =
    if (error.startsWith("unknown op")) "${Mesh.label(host)}'s Baude can't file feedback for a phone: update it"
    else error.ifBlank { "it said no" }

private fun version(ctx: Context): String = try {
    ctx.packageManager.getPackageInfo(ctx.packageName, 0).versionName ?: "?"
} catch (_: Exception) {
    "?"
}
