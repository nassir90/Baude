package dev.baude.android

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
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
 * Sent at once (set: the rule, null for Same as Host); the host's answer is what's shown. */
@Composable
fun AccessRule(cap: String, rule: JSONObject?, state: JSONObject, chat: Boolean = false, title: String? = null,
               set: (JSONObject?) -> Unit) {
    val c = LocalBaude.current
    val editable = state.optBoolean("editable")
    val hostRule = state.optJSONObject("rules")?.optJSONObject(cap)
    val same = chat && rule == null
    val shown = shownOf(if (same) hostRule else rule)
    val members = state.optJSONArray("members")?.let { m -> (0 until m.length()).map { m.getJSONObject(it) } }.orEmpty()
    val names = members.associate { it.optString("id") to it.optString("name") }
    val items = members.map { it.optString("id") to it.optString("name") } +
        shown.orEmpty().filter { it !in names }.map { it to it.take(8) }
    var open by remember { mutableStateOf(false) }
    Column(Modifier.alpha(if (editable) 1f else 0.5f)) {
        if (chat) Text(PhoneAccess.named(cap), style = Type.body, color = c.fg,
            modifier = Modifier.padding(start = 12.dp, end = 12.dp, top = 10.dp))
        ActionRow(if (chat) "" else title ?: PhoneAccess.named(cap), null, onClick = if (editable) ({ open = true }) else null, suffix = {
            Box {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(if (same) Wording.Access.sameAsHost else summary(shown, names), style = Type.body, color = c.fg,
                        maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.widthIn(max = 180.dp))
                    Icon(Adwaita.PanDown, null, tint = c.fg, modifier = Modifier.size(16.dp.icon))
                }
                Popover(open, { open = false }, ticks = Ticks.START) {
                    if (chat) {
                        MenuCheck(Wording.Access.sameAsHost, same) { set(if (same) hostRule ?: ruleOf(null) else null) }
                        MenuDivider()
                    }
                    MenuChecks(Wording.Access.everybody, items, shown, { set(ruleOf(it)) })
                    if (members.isEmpty()) Text(Wording.Access.noMembers, style = Type.body, color = c.dim,
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp))
                }
            }
        })
    }
}

/** The members a rule lets in, as MenuChecks has them: null for everybody, none for nobody. */
private fun shownOf(rule: JSONObject?): List<String>? = when (rule?.optString("who") ?: "everybody") {
    "everybody" -> null
    "chosen" -> rule?.optJSONArray("members")?.let { m -> (0 until m.length()).map { m.optString(it) } }.orEmpty()
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
    shown.size <= 2 -> shown.joinToString(", ") { names[it] ?: it.take(8) }
    else -> Wording.Access.nMembers(shown.size)
}

/** A reply to access or access_set: taken if it's the host's rules, else said in a toast and the last kept. */
private fun took(reply: JSONObject, last: JSONObject?): JSONObject? {
    if (reply.optString("op") != Op.ERROR) return reply
    toast(Wording.Access.failed(why = Msg.Error.from(reply).said.orEmpty()))
    return last ?: reply
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
        BoxedList(PhoneAccess.CAPABILITIES, "") { cap ->
            AccessRule(cap, state.optJSONObject("rules")?.optJSONObject(cap), state) { rule ->
                Mesh.ask(hostId, Msg.AccessSet(capability = cap, rule = null).json().put("rule", rule)) { reply = took(it, reply) }
            }
        }
    }
}

/** A chat's Access… (accessview.chat_dialog): its own rule for who in the mesh may manage it, on its host. */
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
                    AccessRule("manage", r.optJSONObject("chat"), state, chat = true) { rule ->
                        Mesh.ask(h, Msg.AccessSet(session = sid, rule = null).json().put("rule", rule ?: JSONObject.NULL)) {
                            reply = took(it, reply)
                        }
                    }
                }
            }
        },
        confirmButton = { Button(Wording.Common.close, onClick = done) },
        containerColor = c.dialog,
    )
}
