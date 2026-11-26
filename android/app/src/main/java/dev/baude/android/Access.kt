package dev.baude.android

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import org.json.JSONObject

/** Who in the mesh may use what of a host's (accessview.py's, the same rows): a capability's rule, a button naming who may
 * (Everybody, Nobody, the members) that opens a check for each member the host knows of and one over them all, as Home's
 * Groups have them: every one checked is Everybody, none Nobody, some those members (a chat's: Same as Host above them).
 * One split into read and write (PhoneAccess.SPLIT) is its name over two lines, Read and Write, each its rule's button.
 * Sent at once (set: the rule's name and the rule, null for Same as Host); the host's answer is what's shown. own: a
 * chat's own rules (AccessChat's), by name. A host without all of cap's rules shows nothing of it. */
@Composable
fun AccessRule(cap: String, own: JSONObject?, state: JSONObject, chat: Boolean = false, title: String? = null,
               set: (String, JSONObject?) -> Unit) {
    val c = LocalBaude.current
    val names = PhoneAccess.rulesOf(cap)
    val rules = state.optJSONObject("rules")
    if (names.any { rules?.optJSONObject(it) == null }) return
    val host = names.associateWith { rules!!.getJSONObject(it) }
    val checked = if (!chat) host else PhoneAccess.chatRules( // (a chat's: as it's checked, its write within its read)
        names.filter { own?.optJSONObject(it) != null }.associateWith { own!!.getJSONObject(it) }, host)
    fun ruleNamed(name: String): JSONObject? = if (chat && own?.optJSONObject(name) == null) null else checked[name]
    Column(Modifier.alpha(if (state.optBoolean("editable")) 1f else 0.5f)) {
        if (names.size == 1) RuleRow(names[0], title ?: PhoneAccess.named(cap), false, ruleNamed(names[0]), checked[names[0]], state,
            chat) { set(names[0], it) }
        else {
            Text(title ?: PhoneAccess.named(cap), style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.padding(start = 12.dp, end = 12.dp, top = 10.dp))
            for (n in names) RuleRow(n, if (n.endsWith("_read")) Wording.Access.read else Wording.Access.write, true,
                ruleNamed(n), checked[n], state, chat) { set(n, it) }
        }
    }
}

/** One rule's row (name: the rule's), titled title (line: a split capability's Read or Write, dim, in under its name),
 * the button naming who it lets in after it. */
@Composable
private fun RuleRow(name: String, title: String, line: Boolean, rule: JSONObject?, hostRule: JSONObject?, state: JSONObject,
                    chat: Boolean, set: (JSONObject?) -> Unit) {
    val c = LocalBaude.current
    val editable = state.optBoolean("editable")
    val same = chat && rule == null
    val members = state.optJSONArray("members")?.let { m -> (0 until m.length()).map { m.getJSONObject(it) } }.orEmpty()
    val names = members.associate { it.optString("id") to it.optString("name") }
    val shown = shownOf(if (same) hostRule else rule, names.keys)
    val items = members.map { it.optString("id") to it.optString("name") } +
        shown.orEmpty().filter { it !in names }.map { it to it.take(8) }
    var open by remember { mutableStateOf(false) }
    var sending by remember(state) { mutableStateOf(false) }
    val send = { next: JSONObject? -> if (!sending) { sending = true; set(next) } }
    val suffix: @Composable () -> Unit = {
        Box {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(if (same) Wording.Access.sameAsHost else summary(shown, names), style = Type.body, color = c.fg,
                    maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.widthIn(max = 180.dp))
                Icon(Adwaita.PanDown, null, tint = c.fg, modifier = Modifier.size(16.dp.icon))
            }
            Popover(open, { open = false }, ticks = Ticks.START) {
                if (chat) {
                    MenuCheck(Wording.Access.sameAsHost, same) { send(if (same) hostRule ?: ruleOf(null) else null) }
                    MenuDivider()
                }
                MenuChecks(Wording.Access.everybody, items, shown, { send(ruleOf(it)) })
                if (members.isEmpty()) Text(Wording.Access.noMembers, style = Type.body, color = c.dim,
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp))
            }
        }
    }
    val click = if (editable) ({ open = true }) else null
    if (!line) ActionRow(title, null, onClick = click, suffix = suffix)
    else Row(Modifier.fillMaxWidth().heightIn(min = 36.dp).then(if (click != null) Modifier.clickable(onClick = click) else Modifier)
        .padding(start = 24.dp, end = 12.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(title, style = Type.body, color = c.dim, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
        suffix()
    }
}

/** The members a rule lets in, as MenuChecks has them: null for everybody (or a rule naming every one of members), none
 * for nobody. */
private fun shownOf(rule: JSONObject?, members: Set<String>): List<String>? = when (rule?.optString("who") ?: "everybody") {
    "everybody" -> null
    "chosen" -> rule?.optJSONArray("members")?.let { m -> (0 until m.length()).map { m.optString(it) } }.orEmpty()
        .let { if (it.isNotEmpty() && members.isNotEmpty() && it.containsAll(members)) null else it }
    else -> emptyList()
}

/** The rule for the members checked (shownOf's). */
private fun ruleOf(shown: List<String>?): JSONObject = when {
    shown == null -> Msg.AccessRule(who = "everybody").json()
    shown.isEmpty() -> Msg.AccessRule(who = "nobody").json()
    else -> Msg.AccessRule(who = "chosen", members = shown).json()
}

/** Who a rule lets in, in a few words: Everybody; Nobody; studio; studio, pixel-9; 3 members. */
private fun summary(shown: List<String>?, names: Map<String, String>): String = when {
    shown == null -> Wording.Access.everybody
    shown.isEmpty() -> Wording.Access.nobody
    shown.size <= 2 -> (names.keys.filter { it in shown } + shown.filter { it !in names }).joinToString(", ") { names[it] ?: it.take(8) }
    else -> Wording.Access.nMembers(shown.size)
}

/** A reply to access or access_set: taken if it's the host's rules, else said in a toast and the last kept. */
private fun took(reply: JSONObject, last: JSONObject?): JSONObject? {
    if (reply.optString("op") != Op.ERROR) return reply
    toast(Wording.Access.failed(why = Msg.Error.from(reply).said.orEmpty()))
    return last?.let { JSONObject(it.toString()) } ?: reply
}

/** Settings → Access (accessview.Page): the host picked beside New session, then each capability's rule. */
@Composable
fun AccessPage() {
    val c = LocalBaude.current
    val hostId = picked() ?: "phone"
    var reply by remember(hostId) { mutableStateOf<JSONObject?>(null) }
    LaunchedEffect(hostId) { Mesh.ask(hostId, Msg.Access().json()) { reply = it } }
    Column(Modifier.fillMaxSize().background(c.frame).verticalScroll(rememberScrollState())) {
        BoxedList(listOf(Wording.NewSession.host), "") {
            ActionRow(it, null, suffix = { Text(Mesh.label(hostId), style = Type.body, color = c.dim) })
        }
        val r = reply ?: return@Column
        if (r.optString("op") == Op.ERROR) {
            BoxedList(listOf(Msg.Error.from(r).said.orEmpty()), "") { ActionRow(it, null) }
            return@Column
        }
        val state = r.optJSONObject("state") ?: return@Column
        val rules = state.optJSONObject("rules")
        BoxedList(PhoneAccess.CAPABILITIES.filter { cap -> PhoneAccess.rulesOf(cap).all { rules?.optJSONObject(it) != null } }, "") { cap ->
            AccessRule(cap, null, state) { name, rule ->
                Mesh.ask(hostId, Msg.AccessSet(capability = name, rule = null).json().put("rule", rule)) { reply = took(it, reply) }
            }
        }
    }
}

/** A chat's Access… (accessview.chat_dialog): its own rules for who in the mesh may read and write it, on its host. */
@Composable
fun ChatAccess(item: Item, done: () -> Unit) {
    val c = LocalBaude.current
    val h = item.hostId
    val sid = item.s.id.orEmpty()
    var reply by remember(h, sid) { mutableStateOf<JSONObject?>(null) }
    LaunchedEffect(h, sid) { Mesh.ask(h, Msg.Access(session = sid).json()) { reply = it } }
    AlertDialog(
        onDismissRequest = done,
        title = { Text(Wording.Access.chatAccess) },
        text = {
            Column {
                Text(item.s.bot?.name ?: item.s.title.orEmpty(), style = Type.body, color = c.dim)
                val r = reply
                val state = r?.optJSONObject("state")
                if (r != null && r.optString("op") == Op.ERROR) Text(Msg.Error.from(r).said.orEmpty(), style = Type.body, color = c.fg)
                else if (state != null) BoxedList(listOf(Unit), "") {
                    AccessRule("manage", r.optJSONObject("chat"), state, chat = true) { name, rule ->
                        Mesh.ask(h, Msg.AccessSet(session = sid, capability = name, rule = null).json()
                            .put("rule", rule ?: JSONObject.NULL)) { reply = took(it, reply) }
                    }
                }
            }
        },
        confirmButton = { Button(Wording.Common.close, onClick = done) },
        containerColor = c.dialog,
    )
}
