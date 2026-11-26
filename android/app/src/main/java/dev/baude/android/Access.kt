package dev.baude.android

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import org.json.JSONObject

/** Who in the mesh may use what of a host's (accessview.py's, the same rows): a capability's rule, a choice of
 * Everybody, Nobody or Chosen Members (a chat's: Same as Host first), then for Chosen Members a switch for each member
 * the host knows of. Sent at once (set: the rule, null for Same as Host); the host's answer is what's shown. */
@Composable
fun AccessRule(cap: String, rule: JSONObject?, state: JSONObject, chat: Boolean = false, title: String? = null,
               set: (JSONObject?) -> Unit) {
    val c = LocalBaude.current
    val editable = state.optBoolean("editable")
    val who = rule?.optString("who") ?: if (chat) "same" else "everybody"
    val chosen = rule?.optJSONArray("members")?.let { m -> (0 until m.length()).map { m.optString(it) } }.orEmpty()
    val members = state.optJSONArray("members")?.let { m -> (0 until m.length()).map { m.getJSONObject(it) } }.orEmpty()
    fun said(w: String) = if (w == "same") Wording.Access.sameAsHost else Wording.byKey["access.$w"] ?: w
    fun rule(w: String, ids: List<String>) = if (w == "same") null
        else Msg.AccessRule(who = w, members = if (w == "chosen") ids else null).json()
    Column {
        if (chat) Text(PhoneAccess.named(cap), style = Type.body, color = c.fg,
            modifier = Modifier.padding(start = 12.dp, end = 12.dp, top = 10.dp))
        Choice(if (chat) "" else title ?: PhoneAccess.named(cap), (if (chat) listOf("same") else emptyList()) + PhoneAccess.WHO, who, ::said,
            enabled = editable) { set(rule(it, chosen)) }
        if (who == "chosen") {
            if (members.isEmpty()) Rule { ActionRow(Wording.Access.noMembers, null) }
            for (m in members) Rule {
                val id = m.optString("id")
                SwitchRow(m.optString("name"), null, id in chosen, enabled = editable) { on ->
                    set(rule("chosen", if (on) chosen + id else chosen - id))
                }
            }
        }
    }
}

@Composable
private fun Rule(content: @Composable () -> Unit) {
    val c = LocalBaude.current
    Box(Modifier.fillMaxWidth().height(1.dp).background(c.rule))
    Box(Modifier.padding(start = 16.dp)) { content() }
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
