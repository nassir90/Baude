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
 * of their mesh; those that aren't listed greyed, as Baude's dropdown has them), its folder (as last given there, else the host's home), its group (the host's own), what to ask
 * first, and its model, effort and permission mode. Started empty, it's a draft till it's written in. host: the one
 * it's on (fixed), or null to pick. */
@Composable
fun NewSession(host: String?, folder: String?, done: () -> Unit, opened: (String, String) -> Unit,
               given: JSONObject? = null, edited: ((JSONObject) -> Unit)? = null, heading: String? = null) {
    val c = LocalBaude.current
    val ctx = LocalContext.current
    Mesh.tick.value
    val up = Mesh.reached("start")
    val known = Mesh.known("start")
    var at by remember { mutableStateOf(host ?: given?.str("host")?.let { MetaTools.hostByName(it) }
        ?: HomePrefs.host.value.takeIf { h -> up.any { it.first == h } } ?: up.firstOrNull()?.first) }
    fun home(h: String?) = h?.let { folder ?: HomePrefs.folder(ctx, it) ?: Mesh.conn(it)?.info?.home?.ifBlank { null } } ?: "~"
    var cwd by remember { mutableStateOf(given?.str("folder") ?: home(at)) }
    var group by remember { mutableStateOf(given?.str("group") ?: at?.let { Mesh.conn(it)?.info?.group }.orEmpty()) }
    var prompt by remember { mutableStateOf(given?.str("prompt").orEmpty()) }
    var model by remember { mutableStateOf<String?>(given?.str("agent")?.let { a -> // (Meta's spawn_agent: its agent)
        at?.let { h -> models(h).firstOrNull { it.name.equals(a, true) || it.value == a }?.value } }) }
    var effort by remember { mutableStateOf<String?>(null) }
    var mode by remember { mutableStateOf("auto") }
    var busy by remember { mutableStateOf(false) }
    androidx.compose.runtime.LaunchedEffect(at, cwd, group, prompt, model) { // (the user's own edits, kept where it's Meta's form)
        edited?.invoke(JSONObject().put("host", at?.let { Mesh.label(it) } ?: "").put("folder", cwd).put("group", group)
            .put("prompt", prompt).put("agent", at?.let { h -> chosen(models(h), model ?: defaultModel(h))?.name }.orEmpty()))
    }
    var error by remember { mutableStateOf<String?>(null) }
    fun start() {
        val h = at ?: return
        busy = true
        error = null
        val msg = Msg.Start(prompt = prompt.trim(), cwd = cwd.trim().ifBlank { "~" }, mode = mode,
            group = group.trim()).json()
        model?.let { msg.put("model", it) }
        effort?.let { msg.put("effort", it) }
        if (prompt.isBlank()) msg.put("draft", true)
        Mesh.ask(h, msg) { r ->
            busy = false
            val id = Msg.Started.from(r).session?.id
            if (r.optString("op") == Op.STARTED && id != null) {
                HomePrefs.setFolder(ctx, h, cwd.trim().ifBlank { "~" })
                opened(h, id)
                done()
            } else error = Wording.NewSession.hostRefused(host = Mesh.label(h),
                why = Msg.Error.from(r).said.orEmpty().ifBlank { Wording.BotBrowser.hostSaidNo })
        }
    }
    AlertDialog(
        onDismissRequest = done,
        title = { Text(heading ?: if (host != null) Wording.NewSession.onHost(host = Mesh.label(host)) else Wording.NewSession.onNetwork) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                FormRow(Wording.NewSession.host) {
                    if (host != null) Text(Mesh.label(host), style = Type.body, color = c.fg)
                    else if (known.isEmpty()) Text("None of your hosts is connected", style = Type.body, color = c.dim)
                    else Picker(at?.let { a -> hostName(a) } ?: Wording.NewSession.host, Wording.NewSession.host,
                        known.map { hostName(it.first) to it.first }, at, dimmed = known.filter { !it.third }.map { it.first }.toSet()) {
                        at = it
                        cwd = home(it)
                        group = Mesh.conn(it)?.info?.group.orEmpty()
                        model = null
                        effort = null
                    }
                }
                FormRow(Wording.Common.folder) { Field(cwd, { cwd = it }, "~") }
                FormRow(Wording.NewSession.group) { Field(group, { group = it }, Wording.Sidebar.noGroup) }
                FormRow(Wording.NewSession.prompt) {
                    Box(Modifier.fillMaxWidth().heightIn(min = 96.dp).border(1.dp, c.rule, RoundedCornerShape(Gap.cardRadius))
                        .padding(horizontal = 8.dp, vertical = 6.dp)) {
                        BasicTextField(prompt, { prompt = it }, textStyle = Type.body.copy(color = c.fg),
                            cursorBrush = SolidColor(c.accentBg), modifier = Modifier.fillMaxWidth().heightIn(min = 84.dp))
                    }
                }
                FormRow("With") { StartPickers(at, model, effort, mode) { m, e, md -> model = m; effort = e; mode = md } }
                error?.let { Text(it, style = Type.caption, color = c.destructive) }
            }
        },
        confirmButton = { Button(if (busy) "Starting…" else Wording.NewSession.start, suggested = at != null && !busy) { if (!busy) start() } },
        dismissButton = { Button(Wording.Common.cancel, onClick = done) },
        containerColor = c.dialog,
    )
}

/** A host as the New session dialog lists it: "name (host)". */
private fun hostName(id: String): String {
    val h = Mesh.conn(id)?.info?.host?.ifBlank { null }
    val name = Mesh.label(id)
    return if (h != null && h != name) Wording.NewSession.hostNamed(name = name, host = h) else name
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
