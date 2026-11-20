package dev.baude.android

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicInteger

/** A model a host offers: its value, its name, the efforts it takes (none: it has no effort setting). */
data class ModelChoice(val value: String, val name: String, val efforts: List<String>, val resolved: String? = null)

/** Thinking effort's levels as Baude says them (claude.EFFORT_LABELS). */
val EFFORT_LABELS = mapOf("off" to "No thinking", "low" to "Low effort", "medium" to "Medium effort",
    "high" to "High effort", "xhigh" to "Extra-high effort", "max" to "Max effort")

/** Permission modes, Claude's (claude.MODES) and Codex's (models.CODEX_MODES). */
val MODES = listOf("default" to "Manual", "acceptEdits" to "Accept edits", "plan" to "Plan", "auto" to "Auto",
    "dontAsk" to "Don't ask", "bypassPermissions" to "Bypass permissions")
val CODEX_MODES = listOf("default" to "Manual", "auto" to "Auto", "bypassPermissions" to "Bypass Permissions")

/** The program a model runs on, as models.model_program says: claude, codex, apple or pi. */
fun program(model: String?): String = when {
    model == null -> "claude"
    model.startsWith("agent/") -> if (model.endsWith("/apple")) "apple" else "pi"
    model.startsWith("codex/") -> "codex"
    model == "apple" -> "apple"
    model.startsWith("ollama/") || model.startsWith("openai/") || model.startsWith("mlx-") -> "pi"
    else -> "claude"
}

/** The models a host offers: what its claude said it has (initialize's, with their efforts), and the others it can
 * run (its info's); "default" left out, as Baude leaves it. */
fun models(hostId: String): List<ModelChoice> {
    val info = Mesh.conn(hostId)?.info ?: return emptyList()
    val out = LinkedHashMap<String, ModelChoice>()
    for (m in info.optJSONObject("init")?.optJSONArray("models").list()) {
        val v = m.optString("value")
        if (v.isBlank() || v == "default") continue
        val efforts = if (m.optBoolean("supportsEffort")) m.optJSONArray("supportedEffortLevels").strings() else emptyList()
        out[v] = ModelChoice(v, m.optString("displayName").ifBlank { v }, efforts, m.optString("resolvedModel").ifBlank { null })
    }
    for (m in info.optJSONArray("models").list()) {
        val v = m.optString("value")
        if (v.isBlank() || v == "default" || v in out) continue
        val efforts = m.optJSONArray("supportedEffortLevels").strings()
        out[v] = ModelChoice(v, m.optString("displayName").ifBlank { v }, efforts)
    }
    return out.values.toList()
}

/** A field's string, or null where it's null or missing (org.json says "null" for one). */
fun JSONObject.str(k: String): String? = if (isNull(k)) null else optString(k).ifBlank { null }

/** The model a host's claude runs when none's said: its "default"'s (what Baude selects for it). */
fun defaultModel(hostId: String): String? = Mesh.conn(hostId)?.info?.optJSONObject("init")?.optJSONArray("models").list()
    .firstOrNull { it.optString("value") == "default" }?.str("resolvedModel")

/** The one of choices that's model (by its value, or what it resolves to). */
fun chosen(choices: List<ModelChoice>, model: String?): ModelChoice? =
    choices.firstOrNull { it.value == model } ?: choices.firstOrNull { it.resolved != null && it.resolved == model }

/** A model's family, as Baude's model picker heads them (widgets.MODEL_FAMILIES, the common ones). */
fun family(m: ModelChoice): String {
    val said = (m.name + " " + m.value.substringAfterLast('/')).lowercase()
    return listOf("Claude" to "claude|opus|sonnet|haiku|fable", "GPT" to "gpt|openai|codex", "Qwen" to "qwen|qwq",
        "Gemma" to "gemma", "Gemini" to "gemini", "Llama" to "llama", "Mistral" to "mistral|mixtral|stral",
        "DeepSeek" to "deepseek", "Apple" to "apple").firstOrNull { Regex(it.second).containsMatchIn(said) }?.first ?: "Other"
}

/** A copy of a model somewhere in the mesh, as Baude's provider picker lists it (provider_choices): its value as a
 * chat on this host runs it, the host it's on, its provider there, and what it says of itself. */
data class Copy(val value: String, val host: String, val provider: String, val m: JSONObject)

/** The provider a value names (providers.provider_of): apple, mlx, ollama, openai/<endpoint>, agent/<member>/…;
 * null for Claude's. */
fun providerOf(v: String): String? = when {
    v == "apple" -> "apple"
    v.startsWith("mlx-") -> "mlx"
    v.startsWith("ollama/") -> "ollama"
    v.startsWith("openai/") -> "openai/" + v.removePrefix("openai/").substringBefore("/")
    v.startsWith("agent/") -> v.split("/").let { p -> "agent/${p.getOrNull(1)}/" + if (p.lastOrNull() == "apple") "apple" else "mlx" }
    else -> null
}

private fun hasRuntime(pid: String?) = pid != null && pid != "apple" && !(pid.startsWith("agent/") && pid.endsWith("/apple"))

/** What it's served as, any case (providers.kind), or null. */
private fun kindOf(c: Copy): String? {
    if (!hasRuntime(c.m.optString("provider").ifBlank { null } ?: providerOf(c.value))) return null
    return (c.m.str("resolvedModel") ?: c.m.str("id"))?.lowercase()
}

/** Whether two copies are the same model (providers.same): what they're served as (else their names), their version
 * and digest where both say one, their window where both do and neither has a digest. */
fun sameModel(a: Copy, b: Copy): Boolean {
    val ka = kindOf(a)
    val kb = kindOf(b)
    if (ka != null && kb != null) { if (ka != kb) return false }
    else if ((a.m.str("displayName") ?: a.value).lowercase() != (b.m.str("displayName") ?: b.value).lowercase()) return false
    fun differ(k: String) = a.m.has(k) && b.m.has(k) && !a.m.isNull(k) && !b.m.isNull(k) && a.m.opt(k).toString() != b.m.opt(k).toString()
    return !(differ("version") || differ("digest") || differ("window") && !(a.m.has("digest") || b.m.has("digest")))
}

private fun slug(p: String): String {
    var s = p.lowercase().replace(Regex("[^a-z0-9_-]"), "-")
    if (s.length > 17) s = s.take(10) + "-" + java.security.MessageDigest.getInstance("SHA-1").digest(p.toByteArray())
        .joinToString("") { "%02x".format(it) }.take(6)
    return s
}

/** Every copy of a model a chat on hostId can run (baude.provider_choices): its host's own providers', then each
 * other member's of its mesh, as that host runs them (providers.elsewhere: an endpoint's through the mesh's proxy,
 * openai/mesh-<id>-<provider>/…, else run there as an agent, agent/<member>/…). */
fun copies(hostId: String): List<Copy> {
    val info = Mesh.conn(hostId)?.info ?: return emptyList()
    val out = ArrayList<Copy>()
    val here = Mesh.label(hostId)
    for (p in info.optJSONArray("providers").list()) for (m in p.optJSONArray("models").list()) {
        val v = m.str("value") ?: continue
        out.add(Copy(v, here, p.str("name") ?: p.optString("id"), JSONObject(m.toString()).put("provider", providerOf(v))))
    }
    val me = info.str("id")
    for (member in info.optJSONObject("mesh")?.optJSONArray("members").list()) {
        val id = member.str("id") ?: continue
        if (id == me) continue
        val name = member.str("name") ?: id.take(8)
        for (p in member.optJSONArray("providers").list()) for (m in p.optJSONArray("models").list()) {
            val inner = m.str("id") ?: m.str("value") ?: continue
            val (pid, v) = if (p.optBoolean("http")) {
                val ep = "openai/mesh-${id.lowercase().replace(Regex("[^a-z0-9]"), "").take(8)}-${slug(p.optString("id"))}"
                ep to "$ep/$inner"
            } else "agent/$id/${p.optString("id")}" to "agent/$id/${m.optString("value")}"
            val d = JSONObject(m.toString()).put("provider", pid)
            if (!d.has("id") && m.has("resolvedModel")) d.put("id", m.optString("resolvedModel"))
            out.add(Copy(v, name, p.str("name") ?: p.optString("id"), d))
        }
    }
    return out
}

/** The copies of the model a chat runs (none: it's Claude's, or nothing else has it). */
fun copiesOf(hostId: String, model: String?): List<Copy> {
    model ?: return emptyList()
    if (providerOf(model) == null) return emptyList()
    val all = copies(hostId)
    val now = all.firstOrNull { it.value == model } ?: return emptyList()
    return all.filter { sameModel(it, now) }
}

/** Model changes waiting for a turn's end (Baude's move_after_turn): (host, session) -> the value to move to, and
 * how that chat moves (its own). Made once its turn ends, or, its host back after a drop, at once if it's not at one. */
object Moves {
    private val waiting = HashMap<String, Pair<String, (String) -> Unit>>()
    private var listening = false

    fun cancel(hostId: String, sid: String) = waiting.remove("$hostId/$sid")

    fun later(hostId: String, sid: String, value: String, apply: (String) -> Unit) {
        waiting["$hostId/$sid"] = value to apply
        if (listening) return
        listening = true
        Mesh.listeners.add { at, msg ->
            when (msg.optString("op")) {
                Op.TURN_ENDED -> waiting.remove("$at/${msg.optString("session")}")?.let { (v, go) -> go(v) }
                "connected" -> for (k in waiting.keys.filter { it.startsWith("$at/") }) { // (its turn may have ended
                    val s = Mesh.session(at, k.substringAfter("/"))                    // while it was away)
                    if (s == null || s.optString("state") !in setOf("working", "waiting")) waiting.remove(k)?.let { (v, go) -> go(v) }
                }
            }
        }
    }
}

private val nextControl = AtomicInteger(1)

/** A control request to a session's claude, sent as its input (as Baude's RemoteProc sends one): its answer (an
 * error's text, or null) once claude's said. */
fun control(hostId: String, sid: String, request: JSONObject, then: (String?) -> Unit) {
    val rid = "android_${nextControl.getAndIncrement()}"
    var heard: ((String, JSONObject) -> Unit)? = null
    var done = false
    heard = { at, msg ->
        val r = msg.optJSONObject("msg")?.optJSONObject("response")
        if (at == hostId && msg.optString("op") == Op.CLAUDE && msg.optString("session") == sid &&
            msg.optJSONObject("msg")?.optString("type") == "control_response" && r?.optString("request_id") == rid) {
            Mesh.listeners.remove(heard!!)
            done = true
            then(if (r.optString("subtype") == "error") r.optString("error").ifBlank { "it said no" } else null)
        }
    }
    Mesh.listeners.add(heard)
    Mesh.later(30_000) { // (no answer: not waited for any longer)
        if (done || !Mesh.listeners.remove(heard)) return@later
        then("it didn't answer")
    }
    val msg = JSONObject().put("type", "control_request").put("request_id", rid).put("request", request)
    Mesh.ask(hostId, Msg.Input(session = sid, msg = msg).json()) { r ->
        if (r.optString("op") == Op.ERROR && Mesh.listeners.remove(heard)) {
            done = true
            then(r.optString("error"))
        }
    }
}

/** Baude's header bar's controls for a chat, in a row under its title (the phone's too narrow for them beside it):
 * where its model runs (another's only), its model, its effort, its permission mode. A model another program runs
 * starts a new chat with it, as Baude does (another kind of model can't carry the conversation on). */
@Composable
fun ChatControls(hostId: String, sid: String, said: (String) -> Unit, open: (String, String) -> Unit) {
    val c = LocalBaude.current
    Mesh.tick.value
    val s = Mesh.session(hostId, sid) ?: return
    val asked = remember(hostId, sid) { mutableStateMapOf<String, String>() } // (what's been picked, till it's listed)
    val model = asked["model"] ?: s.str("model")
    val choices = models(hostId)
    val now = chosen(choices, model ?: defaultModel(hostId))
    val prog = program(now?.value ?: model)
    Row(Modifier.fillMaxWidth().background(c.frame).horizontalScroll(rememberScrollState())
        .padding(start = 6.dp, end = 6.dp, bottom = 4.dp), horizontalArrangement = Arrangement.spacedBy(2.dp),
        verticalAlignment = Alignment.CenterVertically) {
        val copies = copiesOf(hostId, model)
        if (copies.isNotEmpty()) { // (where the chat's model runs: each copy of it in the mesh, by host and provider)
            val face = copies.firstOrNull { it.value == model }?.let { "${it.host} / ${it.provider}" } ?: "Provider"
            Picker(face, if (copies.size > 1) "Where the chat's model runs" else "Where the chat's model runs (the only one that has it)",
                copies.map { "${it.host} / ${it.provider}" to it.value }, model, enabled = copies.size > 1) { v ->
                Moves.cancel(hostId, sid)
                if (v == s.str("model")) { asked.remove("model"); return@Picker } // (the one it's on: no move)
                if (s.optString("state") in setOf("working", "waiting")) { // (at a turn: moved once it's over)
                    asked["model"] = v
                    Moves.later(hostId, sid, v) { value ->
                        Mesh.session(hostId, sid)?.let { pickModel(hostId, sid, it, value, models(hostId), HashMap(), said, open) }
                    }
                } else pickModel(hostId, sid, s, v, choices, asked, said, open)
            }
        }
        ModelPicker(now?.name ?: model ?: "Model", choices, now?.value) { v ->
            pickModel(hostId, sid, s, v, choices, asked, said, open)
        }
        val efforts = now?.efforts.orEmpty()
        val effort = asked["effort"] ?: s.str("effort")
        if (efforts.isEmpty()) Picker("Effort", "${now?.name ?: "This model"} has no effort setting", emptyList(), null,
            enabled = false) {}
        else Picker(EFFORT_LABELS[effort] ?: "Effort", "Thinking effort", efforts.map { (EFFORT_LABELS[it] ?: it) to it },
            effort) { v ->
            asked["effort"] = v
            control(hostId, sid, JSONObject().put("subtype", "apply_flag_settings")
                .put("settings", JSONObject().put("effortLevel", v))) { e ->
                if (e != null) { asked.remove("effort"); said("Couldn't change the effort: $e") }
            }
        }
        if (prog == "claude" || prog == "codex" || offers(hostId, Since.SET_PERMISSION_MODE)) { // (a host that keeps
            // claude's modes whatever the program: a phone's agents)
            val modes = if (prog == "codex") CODEX_MODES else MODES
            val mode = asked["mode"] ?: s.str("mode") ?: "default"
            Picker(modes.firstOrNull { it.first == mode }?.second ?: mode, "Permission mode", modes.map { it.second to it.first },
                mode) { v ->
                val was = modes.firstOrNull { it.first == mode }?.second ?: mode
                asked["mode"] = v
                control(hostId, sid, JSONObject().put("subtype", "set_permission_mode").put("mode", v)) { e ->
                    if (e != null) {
                        asked.remove("mode")
                        said("Couldn't switch to ${modes.firstOrNull { it.first == v }?.second ?: v}: $e (still $was)")
                    }
                }
            }
        }
    }
    // (what's listed now is what was picked: no longer held here)
    androidx.compose.runtime.SideEffect { for (k in asked.keys.toList()) if (s.str(k) == asked[k]) asked.remove(k) }
}

private fun pickModel(hostId: String, sid: String, s: JSONObject, v: String, choices: List<ModelChoice>,
                      asked: MutableMap<String, String>, said: (String) -> Unit, open: (String, String) -> Unit) {
    val was = s.str("model") ?: defaultModel(hostId)
    if (program(v) != program(chosen(choices, was)?.value ?: was)) { // (another program: a new chat with it)
        val msg = Msg.Start(prompt = "", cwd = s.optString("cwd").ifBlank { "~" }, model = v, mode = if (program(v) == "codex") "default" else "auto", draft = true).json()
        if (!s.isNull("group")) msg.put("group", s.optString("group"))
        Mesh.ask(hostId, msg) { r ->
            val id = r.optJSONObject("session")?.optString("id")
            if (r.optString("op") == Op.STARTED && id != null) open(hostId, id)
            else said("Couldn't start a chat with it: " + r.optString("error"))
        }
        return
    }
    asked["model"] = v
    control(hostId, sid, JSONObject().put("subtype", "set_model").put("model", v)) { e ->
        if (e != null) { asked.remove("model"); said("Couldn't change the model: $e") }
    }
}

/** A dropdown as the header bar's (a flat button, its choice and ▾): its choices (label, value), the one picked
 * checked. */
@Composable
fun Picker(face: String, tip: String, choices: List<Pair<String, String>>, picked: String?, enabled: Boolean = true,
           pick: (String) -> Unit) {
    val c = LocalBaude.current
    var open by remember { mutableStateOf(false) }
    Box {
        Row(Modifier.clip(RoundedCornerShape(6.dp)).clickable(enabled = enabled) { open = true }
            .padding(start = 8.dp, end = 4.dp, top = 6.dp, bottom = 6.dp).alpha(if (enabled) 1f else 0.5f),
            verticalAlignment = Alignment.CenterVertically) {
            Text(face, style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.widthIn(max = 180.dp))
            Icon(Icons.Filled.ArrowDropDown, tip, tint = c.fg, modifier = Modifier.size(18.dp))
        }
        Popover(open, { open = false }) {
            for ((label, value) in choices) MenuChoice(label, value == picked) { open = false; pick(value) }
        }
    }
}

/** The model picker (widgets.ModelPicker): its face the model's name; a search over them, under their families'
 * heads. */
@Composable
fun ModelPicker(face: String, choices: List<ModelChoice>, picked: String?, pick: (String) -> Unit) {
    val c = LocalBaude.current
    var open by remember { mutableStateOf(false) }
    var query by remember { mutableStateOf("") }
    Box {
        Row(Modifier.clip(RoundedCornerShape(6.dp)).clickable(enabled = choices.isNotEmpty()) { open = true; query = "" }
            .padding(start = 8.dp, end = 4.dp, top = 6.dp, bottom = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(face, style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.widthIn(max = 180.dp))
            Icon(Icons.Filled.ArrowDropDown, "Model", tint = c.fg, modifier = Modifier.size(18.dp))
        }
        Popover(open, { open = false }) {
            SearchField(query, { query = it }, "Search models", Modifier.width(260.dp).padding(horizontal = 8.dp, vertical = 4.dp))
            val q = query.trim().lowercase()
            val shown = choices.filter { q.isEmpty() || it.name.lowercase().contains(q) || it.value.lowercase().contains(q) }
            if (shown.isEmpty()) Text("No models match", style = Type.body, color = c.dim,
                modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp))
            for ((fam, ms) in shown.groupBy(::family)) {
                Heading(fam)
                for (m in ms) MenuChoice(m.name, m.value == picked) { open = false; pick(m.value) }
            }
        }
    }
}

/** A short line of what a session runs with, for the New session dialog's pickers' defaults. */
@Composable
fun StartPickers(hostId: String?, model: String?, effort: String?, mode: String, set: (String?, String?, String) -> Unit) {
    val choices = if (hostId == null) emptyList() else models(hostId)
    val now = chosen(choices, model ?: hostId?.let { defaultModel(it) })
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Row(Modifier.heightIn(min = 34.dp), verticalAlignment = Alignment.CenterVertically) {
            ModelPicker(now?.name ?: "Its default model", choices, now?.value) { v ->
                val m = chosen(choices, v)
                set(v, effort?.takeIf { m != null && it in m.efforts }, if (program(v) == "codex") "default" else mode)
            }
            val efforts = now?.efforts.orEmpty()
            if (efforts.isNotEmpty()) Picker(EFFORT_LABELS[effort] ?: "Effort", "Thinking effort",
                efforts.map { (EFFORT_LABELS[it] ?: it) to it }, effort) { set(model, it, mode) }
        }
        val modes = if (program(now?.value ?: model) == "codex") CODEX_MODES else MODES
        Picker(modes.firstOrNull { it.first == mode }?.second ?: mode, "Permission mode", modes.map { it.second to it.first },
            mode) { set(model, effort, it) }
    }
}
