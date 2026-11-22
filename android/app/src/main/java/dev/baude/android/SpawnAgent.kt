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
    val up = Mesh.reached("start")
    val known = Mesh.known("start")
    val named = formValue(f, "host") // (the host it names; one that isn't up stays named, and the form waits for it)
    val at = if (named.isNotBlank()) MetaTools.hostByName(named)
        else picked()?.takeIf { h -> up.any { it.first == h } } ?: up.firstOrNull()?.first
    fun home(h: String?) = h?.let { HomePrefs.folder(ctx, it) ?: Mesh.conn(it)?.info?.home?.ifBlank { null } } ?: "~"
    LaunchedEffect(id, at) { // (what's left empty: as the host it starts on has it, kept in the form)
        val h = at ?: return@LaunchedEffect
        val v = MetaTools.forms[id]?.optJSONObject("values") ?: return@LaunchedEffect
        val set = buildList {
            if (v.optString("host").isBlank()) add("host" to Mesh.label(h))
            if (!v.has("mode")) { // (first shown with a host)
                if (v.optString("folder").isBlank()) add("folder" to home(h))
                if (v.optString("group").isBlank()) add("group" to (newGroup(h).ifBlank { null } ?: Mesh.conn(h)?.info?.group).orEmpty())
                if (v.optString("agent").isBlank()) chosen(models(h), defaultModel(h))?.name?.let { add("agent" to it) }
                add("mode" to "auto")
            }
        }
        if (set.isNotEmpty()) setFormValues(id, *set.toTypedArray())
    }
    val (cwd, setCwd) = formText(f, "folder")
    val (group, setGroup) = formText(f, "group")
    val (prompt, setPrompt) = formText(f, "prompt")
    val agent = formValue(f, "agent")
    val model = at?.let { h -> models(h).firstOrNull { it.name.equals(agent, true) || it.value == agent }?.value }
    val effort = formValue(f, "effort").ifBlank { null }
    val mode = formValue(f, "mode").ifBlank { "auto" }
    val wrong = if (at == null) MetaTools.wrong("spawn_agent", f.optJSONObject("values") ?: JSONObject()) else null
    fun start(): String? {
        val h = at ?: return wrong
        val msg = Msg.Start(prompt = prompt.text.trim(), cwd = cwd.text.trim().ifBlank { "~" }, mode = mode,
            group = group.text.trim()).json()
        model?.let { msg.put("model", it) }
        effort?.let { msg.put("effort", it) }
        if (prompt.text.isBlank()) msg.put("draft", true)
        MetaTools.closed(id, "starting it on ${hostName(h)}")
        val made = group.text.trim()
        val folder = cwd.text.trim().ifBlank { "~" }
        Mesh.ask(h, msg) { r ->
            val sid = Msg.Started.from(r).session?.id
            if (r.optString("op") == Op.STARTED && sid != null) {
                HomePrefs.setFolder(ctx, h, folder)
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
                if (known.isEmpty() && named.isBlank()) Text("None of your hosts is connected", style = Type.body, color = c.dim)
                else Picker(at?.let { a -> hostName(a) } ?: named.ifBlank { Wording.NewSession.host }, Wording.NewSession.host,
                    known.map { hostName(it.first) to it.first }, at, dimmed = known.filter { !it.third }.map { it.first }.toSet()) {
                    setFormValues(id, "host" to Mesh.label(it), "folder" to home(it), "group" to Mesh.conn(it)?.info?.group.orEmpty(),
                        "agent" to (chosen(models(it), defaultModel(it))?.name).orEmpty(), "effort" to "")
                }
            }
            FormRow(Wording.Common.folder) { NameField(cwd, setCwd, "~", {}, focus = false) }
            FormRow(Wording.NewSession.group) { Field(group, setGroup, Wording.Sidebar.noGroup) }
            FormRow(Wording.Common.prompt) {
                Box(Modifier.fillMaxWidth().heightIn(min = 96.dp).border(1.dp, c.rule, RoundedCornerShape(Gap.cardRadius))
                    .padding(horizontal = 8.dp, vertical = 6.dp)) {
                    BasicTextField(prompt, setPrompt, textStyle = Type.body.copy(color = c.fg),
                        cursorBrush = SolidColor(c.accentBg), modifier = Modifier.fillMaxWidth().heightIn(min = 84.dp))
                }
            }
            FormRow("With") {
                StartPickers(at, model, effort, mode) { m, e, md ->
                    setFormValues(id, "agent" to (at?.let { h -> chosen(models(h), m ?: defaultModel(h))?.name }).orEmpty(),
                        "effort" to e.orEmpty(), "mode" to md)
                }
            }
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
