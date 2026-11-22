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
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.verticalScroll
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.layout.boundsInWindow
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntRect
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.window.Popup
import androidx.compose.ui.window.PopupPositionProvider
import androidx.compose.ui.window.PopupProperties
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.offset
import androidx.compose.ui.input.pointer.pointerInput
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicInteger

/** A model a host offers: its value, its name, the efforts it takes (none: it has no effort setting). */
data class ModelChoice(val value: String, val name: String, val efforts: List<String>, val resolved: String? = null,
                       val defaultEffort: String? = null)

/** Thinking effort's levels as Baude says them (claude.EFFORT_LABELS). */
val EFFORT_LABELS = mapOf("off" to Wording.ModeEffort.effortOff, "low" to Wording.ModeEffort.effortLow,
    "medium" to Wording.ModeEffort.effortMedium, "high" to Wording.ModeEffort.effortHigh,
    "xhigh" to Wording.ModeEffort.effortXhigh, "max" to Wording.ModeEffort.effortMax)

/** Permission modes, Claude's (claude.MODES) and Codex's (models.CODEX_MODES). */
val MODES = listOf("default" to Wording.ModeEffort.manual, "acceptEdits" to Wording.ModeEffort.acceptEdits,
    "plan" to Wording.ModeEffort.plan, "auto" to Wording.ModeEffort.auto, "dontAsk" to Wording.ModeEffort.dontAsk,
    "bypassPermissions" to Wording.ModeEffort.bypass)
val CODEX_MODES = listOf("default" to Wording.ModeEffort.manual, "auto" to Wording.ModeEffort.auto,
    "bypassPermissions" to Wording.ModeEffort.codexBypass)

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
    for (m in info.init?.optJSONArray("models").list()) {
        val v = m.optString("value")
        if (v.isBlank() || v == "default") continue
        val efforts = if (m.optBoolean("supportsEffort")) m.optJSONArray("supportedEffortLevels").strings() else emptyList()
        out[v] = ModelChoice(v, m.optString("displayName").ifBlank { v }, efforts, m.optString("resolvedModel").ifBlank { null },
            m.str("defaultEffort"))
    }
    for (m in info.models.orEmpty()) {
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
fun defaultModel(hostId: String): String? = Mesh.conn(hostId)?.info?.init?.optJSONArray("models").list()
    .firstOrNull { it.optString("value") == "default" }?.str("resolvedModel")

/** The one of choices that's model (by its value, or what it resolves to). */
fun chosen(choices: List<ModelChoice>, model: String?): ModelChoice? =
    choices.firstOrNull { it.value == model } ?: choices.firstOrNull { it.resolved != null && it.resolved == model }

/** The family a model (its value, its name) is of (pickers.model_family: schema/icons/models/families.json): the
 * first that matches a word of its name, else its value's last part (not what serves it: ollama/…'s "llama"); null:
 * none. */
fun modelFamily(value: String, name: String): ModelFamilies.Family? {
    for (said in listOf(name.lowercase().split(Regex("\\s+")).filter { it.isNotEmpty() }, listOf(value.lowercase().substringAfterLast('/'))))
        ModelFamilies.all.firstOrNull { f -> said.any { f.match.containsMatchIn(it) } }?.let { return it }
    return null
}

/** A model's family's name, as Baude's model picker heads them ("Other": none). */
fun family(m: ModelChoice): String = modelFamily(m.value, m.name)?.name ?: Wording.Common.other

/** A copy of a model somewhere in the mesh, as Baude's provider picker lists it (provider_choices): its value as a
 * chat on this host runs it, the host it's on, its provider there, what it says of itself, and the provider it's run
 * by here (pid: providers.provider_of's). */
data class Copy(val value: String, val host: String, val provider: String, val m: Msg.ProviderModel, val pid: String?)

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
    if (!hasRuntime(c.pid?.ifBlank { null } ?: providerOf(c.value))) return null
    return c.m.id?.ifBlank { null }?.lowercase()
}

/** Whether two copies are the same model (providers.same): what they're served as (else their names), their version
 * and digest where both say one, their window where both do and neither has a digest. */
fun sameModel(a: Copy, b: Copy): Boolean {
    val ka = kindOf(a)
    val kb = kindOf(b)
    if (ka != null && kb != null) { if (ka != kb) return false }
    else if ((a.m.displayName?.ifBlank { null } ?: a.value).lowercase() !=
        (b.m.displayName?.ifBlank { null } ?: b.value).lowercase()) return false
    fun differ(x: Any?, y: Any?) = x != null && y != null && x.toString() != y.toString()
    return !(differ(a.m.version, b.m.version) || differ(a.m.digest, b.m.digest) ||
        differ(a.m.window, b.m.window) && !(a.m.digest != null || b.m.digest != null))
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
    for (p in info.providers.orEmpty()) for (m in p.models.orEmpty()) {
        val v = m.value?.ifBlank { null } ?: continue
        out.add(Copy(v, here, p.name?.ifBlank { null } ?: p.id.orEmpty(), m, providerOf(v)))
    }
    val me = info.id?.ifBlank { null }
    for (member in info.mesh?.members.orEmpty()) {
        val id = member.id?.ifBlank { null } ?: continue
        if (id == me) continue
        val name = member.name?.ifBlank { null } ?: id.take(8)
        for (p in member.providers.orEmpty()) for (m in p.models.orEmpty()) {
            val inner = m.id?.ifBlank { null } ?: m.value?.ifBlank { null } ?: continue
            val (pid, v) = if (p.http == true) {
                val ep = "openai/mesh-${id.lowercase().replace(Regex("[^a-z0-9]"), "").take(8)}-${slug(p.id.orEmpty())}"
                ep to "$ep/$inner"
            } else "agent/$id/${p.id.orEmpty()}" to "agent/$id/${m.value.orEmpty()}"
            out.add(Copy(v, name, p.name?.ifBlank { null } ?: p.id.orEmpty(), m, pid))
        }
    }
    return out
}

/** Every model a chat on h can run, as Baude's model picker lists them (sync_providers; Meta's Intelligence too:
 * meta.listed_models): h's own (Claude's first), then each other model in the mesh, one of each kind (the same model
 * on two hosts, or served two ways, is one entry: shown's copy where it's of that kind, else the first that has it),
 * a provider's told apart where two of a name aren't the same (providers.told_apart). */
fun meshModels(h: String?, shown: String? = null): List<ModelChoice> {
    h ?: return emptyList()
    val out = ArrayList(models(h))
    val all = copies(h)
    val listed = all.filter { c -> out.any { it.value == c.value } }.toMutableList()
    val own = all.firstOrNull { it.value == shown }
    for (c in all) {
        if (out.any { it.value == c.value } || listed.any { sameModel(it, c) }) continue
        val lead = own?.takeIf { sameModel(it, c) } ?: c
        listed.add(lead)
        out.add(ModelChoice(lead.value, nameOf(lead), if (lead.m.thinking == true) listOf("off", "low", "medium", "high") else emptyList()))
    }
    return out.map { m -> listed.firstOrNull { it.value == m.value }?.let { m.copy(name = toldApart(it, listed)) } ?: m }
}

private fun nameOf(c: Copy) = c.m.displayName?.ifBlank { null } ?: c.value

/** A copy's name, with what tells it from another of listed with its name after it (providers.told_apart): its
 * window or version where they differ, else its digest; just its name where none other has its name. */
fun toldApart(m: Copy, listed: List<Copy>): String {
    val name = nameOf(m)
    val same = listed.filter { nameOf(it).equals(name, true) && it.value != m.value }
    if (same.isEmpty()) return name
    val window = same.any { it.m.window?.toLong() != m.m.window?.toLong() }
    val version = same.any { it.m.version != m.m.version }
    val tags = listOfNotNull(m.m.window?.toLong()?.takeIf { window }?.let { "${it / 1024}k" },
        m.m.version?.ifBlank { null }?.takeIf { version }?.let { "v$it" },
        m.m.digest?.ifBlank { null }?.takeIf { !window && !version }?.removePrefix("sha256:")?.take(7))
    return if (tags.isEmpty()) name else "$name (${tags.joinToString(", ")})"
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
                Op.TURN_ENDED -> waiting.remove("$at/${Msg.TurnEnded.from(msg).session.orEmpty()}")?.let { (v, go) -> go(v) }
                "connected" -> for (k in waiting.keys.filter { it.startsWith("$at/") }) { // (its turn may have ended
                    val s = Mesh.session(at, k.substringAfter("/"))                    // while it was away)
                    if (s == null || s.state.orEmpty() !in setOf("working", "waiting")) waiting.remove(k)?.let { (v, go) -> go(v) }
                }
            }
        }
    }
}

private val nextControl = AtomicInteger(1)

/** A control request to a session's claude, sent as its input (as Baude's RemoteProc sends one): its answer (an
 * error's text, or null) once claude's said. */
fun control(hostId: String, sid: String, request: JSONObject, then: (String?) -> Unit) =
    controlAnswer(hostId, sid, request) { _, why -> then(why) }

/** The same, with claude's whole answer (its response's body) too, when it gave one. */
fun controlAnswer(hostId: String, sid: String, request: JSONObject, then: (JSONObject?, String?) -> Unit) {
    val rid = "android_${nextControl.getAndIncrement()}"
    var heard: ((String, JSONObject) -> Unit)? = null
    var done = false
    heard = { at, msg ->
        val said = if (msg.optString("op") == Op.CLAUDE) Msg.Claude.from(msg) else null
        val r = said?.msg?.optJSONObject("response")
        if (at == hostId && said != null && said.session.orEmpty() == sid &&
            said.msg?.optString("type") == "control_response" && r?.optString("request_id") == rid) {
            Mesh.listeners.remove(heard!!)
            done = true
            then(r.optJSONObject("response"), if (r.optString("subtype") == "error") r.optString("error")
                .ifBlank { Wording.ChatHeader.claudeSaidNo } else null)
        }
    }
    Mesh.listeners.add(heard)
    Mesh.later(30_000) { // (no answer: not waited for any longer)
        if (done || !Mesh.listeners.remove(heard)) return@later
        then(null, Wording.HostStatus.noAnswer)
    }
    val msg = JSONObject().put("type", "control_request").put("request_id", rid).put("request", request)
    Mesh.ask(hostId, Msg.Input(session = sid, msg = msg).json()) { r ->
        if (r.optString("op") == Op.ERROR && Mesh.listeners.remove(heard)) {
            done = true
            then(null, Msg.Error.from(r).said.orEmpty())
        }
    }
}

/** Where a page's pickers go: a row under the bar, the phone held upright (it's too narrow for them beside the
 * title), else in the bar, as Baude's header bar has them. */
@Composable
fun ControlsRow(inBar: Boolean, content: @Composable () -> Unit) {
    val c = LocalBaude.current
    Row(if (inBar) Modifier.horizontalScroll(rememberScrollState()) else Modifier.fillMaxWidth().background(c.frame)
        .horizontalScroll(rememberScrollState()).padding(start = 6.dp, end = 6.dp, bottom = 4.dp), horizontalArrangement = Arrangement.spacedBy(2.dp),
        verticalAlignment = Alignment.CenterVertically) { content() }
}

/** Baude's header bar's controls for a chat (ControlsRow): where its model runs (another's only), its model, its
 * effort, its permission mode. A model another program runs starts a new chat with it, as Baude does (another kind of
 * model can't carry the conversation on). */
@Composable
fun ChatControls(hostId: String, sid: String, said: (String) -> Unit, open: (String, String) -> Unit,
                 modelSet: (String) -> Unit = {}, inBar: Boolean = false) {
    Mesh.tick.value
    val s = Mesh.session(hostId, sid) ?: return
    val asked = remember(hostId, sid) { mutableStateMapOf<String, String>() } // (what's been picked, till it's listed)
    fun listed(k: String) = when (k) { // (what its host lists of it)
        "model" -> s.model
        "effort" -> s.effort
        "mode" -> s.mode
        else -> null
    }?.ifBlank { null }
    val model = asked["model"] ?: listed("model")
    val choices = meshModels(hostId, model)
    val now = chosen(choices, model ?: defaultModel(hostId))
    val prog = program(now?.value ?: model)
    ControlsRow(inBar) {
        val copies = copiesOf(hostId, model)
        if (copies.isNotEmpty()) { // (where the chat's model runs: each copy of it in the mesh, by host and provider)
            val face = copies.firstOrNull { it.value == model }?.let { "${it.host} / ${it.provider}" }?.let(::middleCut)
                ?: Wording.MetaSettings.provider
            Picker(face, if (copies.size > 1) Wording.ChatHeader.providerTip else Wording.ChatHeader.providerTipOnly,
                copies.map { "${it.host} / ${it.provider}" to it.value }, model, enabled = copies.size > 1) { v ->
                Moves.cancel(hostId, sid)
                if (v == listed("model")) { asked.remove("model"); return@Picker } // (the one it's on: no move)
                if (s.state.orEmpty() in setOf("working", "waiting")) { // (at a turn: moved once it's over)
                    asked["model"] = v
                    Moves.later(hostId, sid, v) { value ->
                        Mesh.session(hostId, sid)?.let { pickModel(hostId, sid, it, value, meshModels(hostId, value), HashMap(), said, open, modelSet) }
                    }
                } else pickModel(hostId, sid, s, v, choices, asked, said, open, modelSet)
            }
        }
        ModelPicker((now?.name ?: model ?: Wording.Common.model).let { if (it.length > 30) it.take(29) + "…" else it },
            choices, now?.value ?: model) { v ->
            pickModel(hostId, sid, s, v, choices, asked, said, open, modelSet)
        }
        val efforts = now?.efforts.orEmpty()
        val effort = asked["effort"] ?: listed("effort")
        if (efforts.isEmpty()) { if (prog == "claude") Picker(Wording.MetaSettings.effort,
            Wording.MetaSettings.noEffort(model = now?.name ?: Wording.ChatHeader.thisModel), emptyList(), null,
            enabled = false) {}
        } else Picker(EFFORT_LABELS[effort?.takeIf { it in efforts } ?: now?.defaultEffort?.takeIf { it in efforts }]
            ?: Wording.MetaSettings.effort, Wording.ChatHeader.effortTip, efforts.map { (EFFORT_LABELS[it] ?: it) to it },
            effort) { v ->
            asked["effort"] = v
            said(Wording.ChatHeader.effortNote(label = EFFORT_LABELS[v] ?: v))
            control(hostId, sid, JSONObject().put("subtype", "apply_flag_settings")
                .put("settings", JSONObject().put("effortLevel", v))) { e ->
                if (e != null) { asked.remove("effort"); said("Couldn't change the effort: $e") }
            }
        }
        if (prog == "claude" || prog == "codex" || lists(hostId, "set_permission_mode")) { // (a host that keeps
            // claude's modes whatever the program: a phone's agents)
            val modes = if (prog == "codex") CODEX_MODES else MODES
            val mode = asked["mode"] ?: listed("mode") ?: "default"
            Picker(modes.firstOrNull { it.first == mode }?.second ?: mode, Wording.ChatHeader.modeTip, modes.map { it.second to it.first },
                mode) { v ->
                val was = modes.firstOrNull { it.first == mode }?.second ?: mode
                asked["mode"] = v
                control(hostId, sid, JSONObject().put("subtype", "set_permission_mode").put("mode", v)) { e ->
                    if (e != null) {
                        asked.remove("mode")
                        said(Wording.ChatHeader.modeRefusedStill(mode = modes.firstOrNull { it.first == v }?.second ?: v, why = e, was = was))
                    } else said(Wording.ChatHeader.modeNote(mode = modes.firstOrNull { it.first == v }?.second ?: v))
                }
            }
        }
        if (takes(hostId, "signal_names")) SignalButton(hostId, sid)
    }
    // (what's listed now is what was picked: no longer held here)
    androidx.compose.runtime.SideEffect { for (k in asked.keys.toList()) if (listed(k) == asked[k]) asked.remove(k) }
}

private fun pickModel(hostId: String, sid: String, s: Msg.Session, v: String, choices: List<ModelChoice>,
                      asked: MutableMap<String, String>, said: (String) -> Unit, open: (String, String) -> Unit,
                      modelSet: (String) -> Unit) {
    val was = s.model?.ifBlank { null } ?: defaultModel(hostId)
    if (program(v) != program(chosen(choices, was)?.value ?: was)) { // (another program: a new chat with it)
        val msg = Msg.Start(prompt = "", cwd = s.cwd.orEmpty().ifBlank { "~" }, model = v,
            mode = if (program(v) == "codex") "default" else "auto", draft = true, group = s.group).json()
        Mesh.ask(hostId, msg) { r ->
            val id = Msg.Started.from(r).session?.id
            if (r.optString("op") == Op.STARTED && id != null) open(hostId, id)
            else said(Wording.NewSession.hostRefused(host = Mesh.label(hostId), why = Msg.Error.from(r).said.orEmpty()))
        }
        return
    }
    asked["model"] = v
    said(Wording.ChatHeader.modelNote(name = chosen(choices, v)?.name ?: v))
    control(hostId, sid, JSONObject().put("subtype", "set_model").put("model", v)) { e ->
        if (e != null) { asked.remove("model"); said("Couldn't change the model: $e") } else modelSet(v)
    }
}

/** A label cut in the middle with … past 24 characters (the provider picker's). */
fun middleCut(s: String) = if (s.length > 24) s.take(11) + "…" + s.takeLast(12) else s

/** A dropdown as the header bar's (a flat button, its choice and ▾): its choices (label, value), the one picked
 * checked, those dimmed greyed and not to be picked. */
@Composable
fun Picker(face: String, tip: String, choices: List<Pair<String, String>>, picked: String?, enabled: Boolean = true,
           dimmed: Set<String> = emptySet(), pick: (String) -> Unit) {
    val c = LocalBaude.current
    var open by remember { mutableStateOf(false) }
    Box {
        Row(Modifier.clip(RoundedCornerShape(6.dp)).clickable(enabled = enabled) { open = true }
            .padding(start = 8.dp, end = 4.dp, top = 6.dp, bottom = 6.dp).alpha(if (enabled) 1f else 0.5f),
            verticalAlignment = Alignment.CenterVertically) {
            Text(face, style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.widthIn(max = 180.dp))
            Icon(Adwaita.PanDown, tip, tint = c.fg, modifier = Modifier.size(16.dp.icon))
        }
        Popover(open, { open = false }) {
            for ((label, value) in choices) MenuChoice(label, value == picked, enabled = value !in dimmed) { open = false; pick(value) }
        }
    }
}

/** A page's pane for a picker's list (ChatShell's: MenuPaneLayer), drawn over the page under the picker's button and
 * down to the page's bottom bar at most; the list it shows, the button it's under (in the window, px), how it's closed,
 * and the bottom bar's top (px; none while there's no bar). */
class MenuPane {
    var content by mutableStateOf<(@Composable (Modifier) -> Unit)?>(null)
    var anchor by mutableStateOf(androidx.compose.ui.geometry.Rect.Zero)
    var floor by mutableStateOf<Float?>(null)
    var close: () -> Unit = {}
    var owner: Any? = null
}

val LocalMenuPane = androidx.compose.runtime.compositionLocalOf<MenuPane?> { null }

/** A pane's list over the page (in a Box filling it): from under its button, kept on the screen, down to the bottom
 * bar (or the keyboard's top); a tap elsewhere or Back closes it. */
@Composable
fun MenuPaneLayer(pane: MenuPane) {
    val content = pane.content ?: return
    val density = androidx.compose.ui.platform.LocalDensity.current
    var box by remember { mutableStateOf(androidx.compose.ui.geometry.Rect.Zero) }
    androidx.activity.compose.BackHandler { pane.close() }
    Box(Modifier.fillMaxSize().imePadding().onGloballyPositioned { box = it.boundsInWindow() }
        .pointerInput(Unit) { detectTapGestures { pane.close() } }) {
        val margin = with(density) { 8.dp.toPx() }
        val width = with(density) { MODEL_LIST_WIDTH.toPx() }
        val top = pane.anchor.bottom - box.top
        val bottom = minOf(pane.floor ?: box.bottom, box.bottom) - margin - box.top
        val x = (pane.anchor.left - box.left).coerceAtMost(box.width - width - margin).coerceAtLeast(0f)
        content(Modifier.offset { IntOffset(x.toInt(), top.toInt()) }
            .heightIn(max = with(density) { (bottom - top).coerceAtLeast(0f).toDp() })
            .pointerInput(Unit) { detectTapGestures { } }) // (a tap on the list's own: not one elsewhere)
    }
}

/** A model's logo (its family's, as the desktop draws it beside it), or null. */
fun modelLogo(value: String, name: String): ImageVector? = modelFamily(value, name)?.logo

private val MODEL_ROW = 34.dp // (the search field's height too: its icon over the rows' logos)
private val MODEL_LIST_WIDTH = 280.dp

/** Under its anchor, at its left edge (kept on the screen); above it where it's longer than the room below. */
private object BelowAnchor : PopupPositionProvider {
    override fun calculatePosition(anchorBounds: IntRect, windowSize: IntSize, layoutDirection: LayoutDirection,
                                   popupContentSize: IntSize): IntOffset {
        val x = anchorBounds.left.coerceAtMost(windowSize.width - popupContentSize.width).coerceAtLeast(0)
        val below = anchorBounds.bottom + popupContentSize.height <= windowSize.height
        return IntOffset(x, if (below) anchorBounds.bottom else (anchorBounds.top - popupContentSize.height).coerceAtLeast(0))
    }
}

/** The model picker (pickers.ModelPicker): its face the model's logo and name; a search over the models, each with its
 * family's logo, under their families' heads. On a page with a pane (a chat's, Meta's) its list opens there, under the
 * button and down to the bottom bar at most, scrolling; elsewhere (a dialog, Settings) in a menu under it. */
@Composable
fun ModelPicker(face: String, choices: List<ModelChoice>, picked: String?, pick: (String) -> Unit) {
    val c = LocalBaude.current
    val density = androidx.compose.ui.platform.LocalDensity.current
    val view = androidx.compose.ui.platform.LocalView.current
    val pane = LocalMenuPane.current
    var open by remember { mutableStateOf(false) }
    var query by remember { mutableStateOf("") }
    var anchor by remember { mutableStateOf(androidx.compose.ui.geometry.Rect.Zero) }
    val now = choices.firstOrNull { it.value == picked }
    val logo = picked?.let { modelLogo(it, now?.name ?: face) }
    Box(Modifier.onGloballyPositioned { anchor = it.boundsInWindow() }) {
        Row(Modifier.clip(RoundedCornerShape(6.dp)).clickable(enabled = choices.isNotEmpty()) { open = true; query = "" }
            .padding(start = 8.dp, end = 4.dp, top = 6.dp, bottom = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            if (logo != null) Icon(logo, null, tint = c.fg, modifier = Modifier.padding(end = 6.dp).size(16.dp.icon))
            Text(face, style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.widthIn(max = 180.dp))
            Icon(Adwaita.PanDown, Wording.Common.model, tint = c.fg, modifier = Modifier.size(16.dp.icon))
        }
        val list = @Composable { m: Modifier -> ModelList(query, { query = it }, choices, picked, m) { v -> open = false; pick(v) } }
        if (open && pane != null) {
            val me = remember { Any() }
            androidx.compose.runtime.SideEffect {
                pane.owner = me
                pane.anchor = anchor
                pane.content = list
                pane.close = { open = false }
            }
            androidx.compose.runtime.DisposableEffect(pane) {
                onDispose { if (pane.owner === me) { pane.owner = null; pane.content = null } }
            }
        } else if (open) Popup(popupPositionProvider = BelowAnchor, onDismissRequest = { open = false },
            properties = PopupProperties(focusable = true)) {
            val margin = with(density) { 8.dp.toPx() }
            val room = view.rootView.height - margin - anchor.bottom // (below it, else above it)
            val most = with(density) { (if (room < 200.dp.toPx()) anchor.top - margin else room).toDp() }
            list(Modifier.heightIn(max = most))
        }
    }
}

/** The model picker's list: the search field, then the models it finds (every word typed in a model's name, its
 * value or its family's name) under their families' heads; "No models match" where it finds none. */
@Composable
private fun ModelList(query: String, setQuery: (String) -> Unit, choices: List<ModelChoice>, picked: String?,
                      modifier: Modifier, pick: (String) -> Unit) {
    val c = LocalBaude.current
    Column(modifier.width(MODEL_LIST_WIDTH).shadow(8.dp, RoundedCornerShape(12.dp)).clip(RoundedCornerShape(12.dp))
        .background(c.dialog).padding(vertical = 6.dp)) {
        SearchField(query, setQuery, Wording.ModelPicker.search, Modifier.fillMaxWidth().padding(horizontal = 8.dp).height(MODEL_ROW))
        val words = query.lowercase().split(Regex("\\s+")).filter { it.isNotEmpty() }
        val shown = choices.filter { m -> "${m.value} ${m.name} ${family(m)}".lowercase().let { said -> words.all { it in said } } }
        Column(Modifier.weight(1f, fill = false).padding(top = 4.dp).verticalScroll(rememberScrollState())) {
            if (shown.isEmpty()) Text(Wording.ModelPicker.none, style = Type.body, color = c.dim,
                textAlign = androidx.compose.ui.text.style.TextAlign.Center, modifier = Modifier.fillMaxWidth().padding(vertical = 12.dp))
            for ((fam, ms) in shown.groupBy(::family)) {
                Heading(fam)
                for (m in ms) ModelRow(m, m.value == picked) { pick(m.value) }
            }
        }
    }
}

/** A model's row (pickers.ModelPicker's): its family's logo (the room of one where it has none), its name on one
 * line, the tick on the one picked. */
@Composable
private fun ModelRow(m: ModelChoice, on: Boolean, onClick: () -> Unit) {
    val c = LocalBaude.current
    Row(Modifier.fillMaxWidth().height(MODEL_ROW).clickable(onClick = onClick).padding(horizontal = 16.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        val logo = modelLogo(m.value, m.name)
        if (logo != null) Icon(logo, null, tint = c.fg, modifier = Modifier.size(16.dp.icon))
        else androidx.compose.foundation.layout.Spacer(Modifier.size(16.dp.icon))
        Text(m.name, style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
        Icon(Adwaita.ObjectSelect, null, tint = c.accent, modifier = Modifier.size(16.dp.icon).alpha(if (on) 1f else 0f))
    }
}

/** A short line of what a session runs with, for the New session dialog's pickers' defaults. */
@Composable
fun StartPickers(hostId: String?, model: String?, effort: String?, mode: String, set: (String?, String?, String) -> Unit) {
    val choices = meshModels(hostId, model)
    val now = chosen(choices, model ?: hostId?.let { defaultModel(it) })
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Row(Modifier.heightIn(min = 34.dp), verticalAlignment = Alignment.CenterVertically) {
            ModelPicker(now?.name ?: "Its default model", choices, now?.value) { v ->
                val m = chosen(choices, v)
                set(v, effort?.takeIf { m != null && it in m.efforts }, if (program(v) == "codex") "default" else mode)
            }
            val efforts = now?.efforts.orEmpty()
            if (efforts.isNotEmpty()) Picker(EFFORT_LABELS[effort] ?: Wording.MetaSettings.effort, Wording.ChatHeader.effortTip,
                efforts.map { (EFFORT_LABELS[it] ?: it) to it }, effort) { set(model, it, mode) }
        }
        val modes = if (program(now?.value ?: model) == "codex") CODEX_MODES else MODES
        Picker(modes.firstOrNull { it.first == mode }?.second ?: mode, Wording.ChatHeader.modeTip, modes.map { it.second to it.first },
            mode) { set(model, effort, it) }
    }
}
