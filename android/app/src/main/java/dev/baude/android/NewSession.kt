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
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
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
               given: JSONObject? = null, edited: ((JSONObject) -> Unit)? = null, heading: String? = null,
               first: String? = null, focus: String? = null) {
    val c = LocalBaude.current
    val ctx = LocalContext.current
    Mesh.tick.value
    val up = Mesh.reached("start").sortedBy { it.first != first } // (the host the + belongs to: first)
    val known = Mesh.known("start").sortedBy { it.first != first }
    var at by remember { mutableStateOf(host ?: given?.str("host")?.let { MetaTools.hostByName(it) }
        ?: first?.takeIf { h -> up.any { it.first == h } } ?: picked()?.takeIf { h -> up.any { it.first == h } } ?: up.firstOrNull()?.first) }
    fun home(h: String?) = h?.let { folder ?: HomePrefs.folder(ctx, it) ?: Mesh.conn(it)?.info?.home?.ifBlank { null } } ?: "~"
    var cwd by remember { mutableStateOf(given?.str("folder") ?: home(at)) }
    var group by remember { mutableStateOf(given?.str("group") ?: at?.let { newGroup(it).ifBlank { null } ?: Mesh.conn(it)?.info?.group }.orEmpty()) }
    var prompt by remember { mutableStateOf(given?.str("prompt").orEmpty()) }
    var model by remember { mutableStateOf<String?>(given?.str("agent")?.let { a -> // (Meta's spawn_agent: its agent)
        at?.let { h -> models(h).firstOrNull { it.name.equals(a, true) || it.value == a }?.value } }) }
    var effort by remember { mutableStateOf<String?>(null) }
    var mode by remember { mutableStateOf("auto") }
    androidx.compose.runtime.LaunchedEffect(at, cwd, group, prompt, model) { // (the user's own edits, kept where it's Meta's form)
        edited?.invoke(JSONObject().put("host", at?.let { Mesh.label(it) } ?: "").put("folder", cwd).put("group", group)
            .put("prompt", prompt).put("agent", at?.let { h -> chosen(models(h), model ?: defaultModel(h))?.name }.orEmpty()))
    }
    fun start() {
        val h = at ?: return
        val msg = Msg.Start(prompt = prompt.trim(), cwd = cwd.trim().ifBlank { "~" }, mode = mode,
            group = group.trim()).json()
        model?.let { msg.put("model", it) }
        effort?.let { msg.put("effort", it) }
        if (prompt.isBlank()) msg.put("draft", true)
        done()
        val made = group.trim()
        Mesh.ask(h, msg) { r ->
            val id = Msg.Started.from(r).session?.id
            if (r.optString("op") == Op.STARTED && id != null) {
                HomePrefs.setFolder(ctx, h, cwd.trim().ifBlank { "~" })
                Groups.putIn(made)
                opened(h, id)
            } else toast(Wording.NewSession.hostRefused(host = Mesh.label(h),
                why = Msg.Error.from(r).said.orEmpty().ifBlank { Wording.BotBrowser.hostSaidNo }))
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
                FormRow(Wording.Common.folder) { NameField(cwd, { cwd = it }, "~", {}, focus = focus == "folder") }
                FormRow(Wording.NewSession.group) { Field(group, { group = it }, Wording.Sidebar.noGroup) }
                FormRow(Wording.Common.prompt) {
                    Box(Modifier.fillMaxWidth().heightIn(min = 96.dp).border(1.dp, c.rule, RoundedCornerShape(Gap.cardRadius))
                        .padding(horizontal = 8.dp, vertical = 6.dp)) {
                        val focusing = remember { FocusRequester() }
                        androidx.compose.runtime.LaunchedEffect(Unit) { if (focus != "folder") focusing.requestFocus() }
                        BasicTextField(prompt, { prompt = it }, textStyle = Type.body.copy(color = c.fg),
                            cursorBrush = SolidColor(c.accentBg), modifier = Modifier.fillMaxWidth().heightIn(min = 84.dp).focusRequester(focusing))
                    }
                }
                FormRow("With") { StartPickers(at, model, effort, mode) { m, e, md -> model = m; effort = e; mode = md } }
            }
        },
        confirmButton = { Button(Wording.NewSession.start, suggested = at != null) { start() } },
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
