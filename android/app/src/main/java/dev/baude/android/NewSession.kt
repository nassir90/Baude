package dev.baude.android

import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import org.json.JSONObject

/** Baude's "New session on …" (remote_start_form): the host it starts on (any that's up: one of yours, or a member
 * of their mesh), its folder (as last given there, else the host's home), its group (the host's own), what to ask
 * first, and its model, effort and permission mode. Started empty, it's a draft till it's written in. host: the one
 * it's on (fixed), or null to pick. */
@Composable
fun NewSession(host: String?, folder: String?, done: () -> Unit, opened: (String, String) -> Unit,
               given: JSONObject? = null) {
    val c = LocalBaude.current
    val ctx = LocalContext.current
    Mesh.tick.value
    val up = Mesh.reached()
    var at by remember { mutableStateOf(host ?: given?.str("host")?.let { MetaTools.hostByName(it) }
        ?: HomePrefs.host.value.takeIf { h -> up.any { it.first == h } } ?: up.firstOrNull()?.first) }
    fun home(h: String?) = h?.let { folder ?: HomePrefs.folder(ctx, it) ?: Mesh.conn(it)?.info?.optString("home")?.ifBlank { null } } ?: "~"
    var cwd by remember { mutableStateOf(given?.str("folder") ?: home(at)) }
    var group by remember { mutableStateOf(given?.str("group") ?: at?.let { Mesh.conn(it)?.info?.optString("group") }.orEmpty()) }
    var prompt by remember { mutableStateOf(given?.str("prompt").orEmpty()) }
    var model by remember { mutableStateOf<String?>(null) }
    var effort by remember { mutableStateOf<String?>(null) }
    var mode by remember { mutableStateOf("auto") }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    fun start() {
        val h = at ?: return
        busy = true
        error = null
        val msg = JSONObject().put("op", "start").put("prompt", prompt.trim()).put("cwd", cwd.trim().ifBlank { "~" })
            .put("mode", mode).put("group", group.trim())
        model?.let { msg.put("model", it) }
        effort?.let { msg.put("effort", it) }
        if (prompt.isBlank()) msg.put("draft", true)
        Mesh.ask(h, msg) { r ->
            busy = false
            val id = r.optJSONObject("session")?.optString("id")
            if (r.optString("op") == "started" && id != null) {
                HomePrefs.setFolder(ctx, h, cwd.trim().ifBlank { "~" })
                done()
                opened(h, id)
            } else error = r.optString("error").ifBlank { "the host said no" }
        }
    }
    AlertDialog(
        onDismissRequest = done,
        title = { Text(if (host != null) "New session on ${Mesh.label(host)}" else "New session on the network") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                FormRow("Host") {
                    if (host != null) Text(Mesh.label(host), style = Type.body, color = c.fg)
                    else if (up.isEmpty()) Text("None of your hosts is connected", style = Type.body, color = c.dim)
                    else Picker(at?.let { a -> hostName(a) } ?: "Host", "Host", up.map { hostName(it.first) to it.first }, at) {
                        at = it
                        cwd = home(it)
                        group = Mesh.conn(it)?.info?.optString("group").orEmpty()
                        model = null
                        effort = null
                    }
                }
                FormRow("Folder") { Field(cwd, { cwd = it }, "~") }
                FormRow("Group") { Field(group, { group = it }, "No group") }
                FormRow("Prompt") {
                    Box(Modifier.fillMaxWidth().heightIn(min = 96.dp).border(1.dp, c.rule, RoundedCornerShape(Gap.cardRadius))
                        .padding(horizontal = 8.dp, vertical = 6.dp)) {
                        BasicTextField(prompt, { prompt = it }, textStyle = Type.body.copy(color = c.fg),
                            cursorBrush = SolidColor(c.accentBg), modifier = Modifier.fillMaxWidth().heightIn(min = 84.dp))
                    }
                }
                FormRow("With") { StartPickers(at, model, effort, mode) { m, e, md -> model = m; effort = e; mode = md } }
                error?.let { Text("Couldn't start it: $it", style = Type.caption, color = c.destructive) }
            }
        },
        confirmButton = { Button(if (busy) "Starting…" else "Start", suggested = at != null && !busy) { if (!busy) start() } },
        dismissButton = { Button("Cancel", onClick = done) },
        containerColor = c.dialog,
    )
}

/** A host as the New session dialog lists it: "name (host)". */
private fun hostName(id: String): String {
    val h = Mesh.conn(id)?.info?.optString("host")?.ifBlank { null }
    val name = Mesh.label(id)
    return if (h != null && h != name) "$name ($h)" else name
}

/** A form's row, as the dialog's grid has it: its dim label at the end of the label column, then what's set. */
@Composable
fun FormRow(label: String, content: @Composable () -> Unit) {
    val c = LocalBaude.current
    Row(verticalAlignment = Alignment.Top) {
        Text(label, style = Type.body, color = c.dim, textAlign = TextAlign.End,
            modifier = Modifier.width(68.dp).padding(top = 8.dp, end = 10.dp))
        Box(Modifier.weight(1f)) { content() }
    }
}
