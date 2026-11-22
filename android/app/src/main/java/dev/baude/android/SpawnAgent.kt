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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
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

/** Meta's spawn_agent form (Baude's spawn_form), a card in its conversation, filled by Meta, submitted by you: the host it
 * starts on (any that's up: one of yours, or a member of their mesh; those that aren't listed greyed, as Baude's dropdown
 * has them), its folder (as last given there, else the host's home), its group (the host's own), what to ask first, and
 * its model, effort and permission mode. Started empty, it's a draft till it's written in; started, it's opened. */
@Composable
fun SpawnAgentForm(f: JSONObject) {
    val c = LocalBaude.current
    val ctx = LocalContext.current
    Mesh.tick.value
    val id = f.optString("id")
    val given = f.optJSONObject("values")
    val up = Mesh.reached("start")
    val known = Mesh.known("start")
    var at by remember { mutableStateOf(given?.str("host")?.let { MetaTools.hostByName(it) }
        ?: picked()?.takeIf { h -> up.any { it.first == h } } ?: up.firstOrNull()?.first) }
    fun home(h: String?) = h?.let { HomePrefs.folder(ctx, it) ?: Mesh.conn(it)?.info?.home?.ifBlank { null } } ?: "~"
    var cwd by remember { mutableStateOf(given?.str("folder") ?: home(at)) }
    var group by remember { mutableStateOf(given?.str("group") ?: at?.let { newGroup(it).ifBlank { null } ?: Mesh.conn(it)?.info?.group }.orEmpty()) }
    var prompt by remember { mutableStateOf(given?.str("prompt").orEmpty()) }
    var model by remember { mutableStateOf<String?>(given?.str("agent")?.let { a -> // (Meta's spawn_agent: its agent)
        at?.let { h -> models(h).firstOrNull { it.name.equals(a, true) || it.value == a }?.value } }) }
    var effort by remember { mutableStateOf<String?>(null) }
    var mode by remember { mutableStateOf("auto") }
    val values = JSONObject().put("host", at?.let { Mesh.label(it) } ?: "").put("folder", cwd).put("group", group)
        .put("prompt", prompt).put("agent", at?.let { h -> chosen(models(h), model ?: defaultModel(h))?.name }.orEmpty())
    LaunchedEffect(values.toString()) { MetaTools.edited(id, values) } // (the user's own edits, kept as Meta's form)
    val wrong = if (at == null) MetaTools.wrong("spawn_agent", values) else null
    fun start(): String? {
        val h = at ?: return wrong
        val msg = Msg.Start(prompt = prompt.trim(), cwd = cwd.trim().ifBlank { "~" }, mode = mode,
            group = group.trim()).json()
        model?.let { msg.put("model", it) }
        effort?.let { msg.put("effort", it) }
        if (prompt.isBlank()) msg.put("draft", true)
        MetaTools.closed(id, "starting it on ${hostName(h)}")
        val made = group.trim()
        Mesh.ask(h, msg) { r ->
            val sid = Msg.Started.from(r).session?.id
            if (r.optString("op") == Op.STARTED && sid != null) {
                HomePrefs.setFolder(ctx, h, cwd.trim().ifBlank { "~" })
                Groups.putIn(made)
                Meta.opener(h, sid)
            } else toast(Wording.NewSession.hostRefused(host = Mesh.label(h),
                why = Msg.Error.from(r).said.orEmpty().ifBlank { Wording.BotBrowser.hostSaidNo }))
        }
        return null
    }
    FormFrame(f, wrong, submit = ::start) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            FormRow(Wording.NewSession.host) {
                if (known.isEmpty()) Text("None of your hosts is connected", style = Type.body, color = c.dim)
                else Picker(at?.let { a -> hostName(a) } ?: Wording.NewSession.host, Wording.NewSession.host,
                    known.map { hostName(it.first) to it.first }, at, dimmed = known.filter { !it.third }.map { it.first }.toSet()) {
                    at = it
                    cwd = home(it)
                    group = Mesh.conn(it)?.info?.group.orEmpty()
                    model = null
                    effort = null
                }
            }
            FormRow(Wording.Common.folder) { NameField(cwd, { cwd = it }, "~", {}, focus = false) }
            FormRow(Wording.NewSession.group) { Field(group, { group = it }, Wording.Sidebar.noGroup) }
            FormRow(Wording.Common.prompt) {
                Box(Modifier.fillMaxWidth().heightIn(min = 96.dp).border(1.dp, c.rule, RoundedCornerShape(Gap.cardRadius))
                    .padding(horizontal = 8.dp, vertical = 6.dp)) {
                    BasicTextField(prompt, { prompt = it }, textStyle = Type.body.copy(color = c.fg),
                        cursorBrush = SolidColor(c.accentBg), modifier = Modifier.fillMaxWidth().heightIn(min = 84.dp))
                }
            }
            FormRow("With") { StartPickers(at, model, effort, mode) { m, e, md -> model = m; effort = e; mode = md } }
        }
    }
}

/** A host as the form lists it: "name (host)". */
private fun hostName(id: String): String {
    val h = Mesh.conn(id)?.info?.host?.ifBlank { null }
    val name = Mesh.label(id)
    return if (h != null && h != name) Wording.NewSession.hostNamed(name = name, host = h) else name
}

/** A form's row, as Baude's form's grid has it: its dim label at the end of the label column, then what's set. */
@Composable
private fun FormRow(label: String, content: @Composable () -> Unit) {
    val c = LocalBaude.current
    Row(verticalAlignment = Alignment.Top) {
        Text(label, style = Type.body, color = c.dim, textAlign = TextAlign.End,
            modifier = Modifier.width(68.dp).padding(top = 8.dp, end = 10.dp))
        Box(Modifier.weight(1f)) { content() }
    }
}
