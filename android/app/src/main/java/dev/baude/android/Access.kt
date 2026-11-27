package dev.baude.android

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.layout.Placeable
import androidx.compose.ui.layout.SubcomposeLayout
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.DpOffset
import androidx.compose.ui.unit.dp
import org.json.JSONObject

/** Who in the mesh may use what of a host's (accessview.py's, the same rows): a capability's name, then its rule's field
 * as an email's To (ToField): who may as pills, each with ×, Everybody one pill, none Nobody; typing lists who may be
 * added. One split into read and write (PhoneAccess.SPLIT) has a line for each, Read and Write, each with its rule's
 * field. Sent at once (set: the rule's name and the rule, null for Same as Host); the host's answer is what's shown.
 * own: a chat's own rules (AccessChat's), by name. A host without all of cap's rules shows nothing of it. */
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
    val editable = state.optBoolean("editable")
    val members = state.optJSONArray("members")?.let { m -> (0 until m.length()).map { m.getJSONObject(it) } }.orEmpty()
    val known = members.associate { it.optString("id") to it.optString("name") }
    Column(Modifier.fillMaxWidth().alpha(if (editable) 1f else 0.5f).padding(start = 12.dp, end = 12.dp, top = 10.dp, bottom = 8.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(title ?: PhoneAccess.named(cap), style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis)
        for (n in names) Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (names.size > 1) Text(if (n.endsWith("_read")) Wording.Access.read else Wording.Access.write, style = Type.body,
                color = c.dim, maxLines = 1, modifier = Modifier.widthIn(min = 44.dp))
            ToField(PhoneAccess.named(n), keysOf(ruleNamed(n), chat), state, known, chat, editable, Modifier.weight(1f)) { set(n, ruleOf(it)) }
        }
    }
}

private const val EVERYBODY = "*everybody" // (a field's pills' keys beside the members' ids: Everybody, Same as Host)
private const val SAME = "*same"

/** A rule as its field's pills: Everybody's, none for nobody, the members' ids for chosen ones; a chat's Same as Host
 * (null, chat): its. */
private fun keysOf(rule: JSONObject?, chat: Boolean): List<String> = when {
    rule == null && chat -> listOf(SAME)
    else -> when (rule?.optString("who") ?: "everybody") {
        "everybody" -> listOf(EVERYBODY)
        "chosen" -> rule?.optJSONArray("members")?.let { m -> (0 until m.length()).map { m.optString(it) } }.orEmpty()
        else -> emptyList()
    }
}

/** The rule for a field's pills (keysOf's): null for Same as Host. */
private fun ruleOf(keys: List<String>): JSONObject? = when {
    SAME in keys -> null
    EVERYBODY in keys -> Msg.AccessRule(who = "everybody").json()
    keys.isEmpty() -> Msg.AccessRule(who = "nobody").json()
    else -> Msg.AccessRule(who = "chosen", members = keys).json()
}

/** The names (key to name) typed picks out, in order: those starting with it, then those with it in them. */
private fun matches(typed: String, names: Map<String, String>): List<String> {
    val t = typed.trim().lowercase()
    val first = names.filter { it.value.lowercase().startsWith(t) }.keys.toList()
    return first + names.filter { it.key !in first && t in it.value.lowercase() }.keys
}

/** One rule's field (label: the rule's name, as it's told apart), as an email's To (accessview.RuleField): who it lets
 * in as pills, wrapping, two lines of them and "<n> more" for the rest, which opens it out to every one; none is Nobody,
 * said in it. Tapped, it takes typing, and lists who may be added under it (Same as Host for a chat's, Everybody, then
 * the members, but those it has), matched as it's typed: a tap, or the keyboard's Done once something is typed, adds one (Everybody or Same as
 * Host in place of the rest, a member in place of either); × takes one out. change(the pills) as they're changed;
 * what it shows stays as changed till the host answers (host: its reply's state; a refusal brings back what it has). */
@Composable
private fun ToField(label: String, keys: List<String>, host: Any, known: Map<String, String>, chat: Boolean, editable: Boolean,
                    modifier: Modifier = Modifier, change: (List<String>) -> Unit) {
    val c = LocalBaude.current
    var shown by remember(keys, host) { mutableStateOf(keys) }
    var typing by remember { mutableStateOf(false) }
    var typed by remember { mutableStateOf("") }
    var open by remember { mutableStateOf(false) }
    val focus = remember { FocusRequester() }
    val keyboard = LocalSoftwareKeyboardController.current
    fun named(k: String) = when (k) { EVERYBODY -> Wording.Access.everybody; SAME -> Wording.Access.sameAsHost; else -> known[k] ?: k.take(8) }
    fun changed(next: List<String>) { shown = next; change(next) }
    fun add(k: String) { typed = ""; changed(if (k == EVERYBODY || k == SAME) listOf(k) else shown.filter { it != EVERYBODY && it != SAME } + k) }
    val offered = (if (chat) listOf(SAME) else emptyList()) + EVERYBODY + known.keys
    val found = matches(typed, offered.filter { it !in shown }.associateWith { named(it) })
    LaunchedEffect(typing) { if (typing) runCatching { focus.requestFocus() } }
    Box(modifier) {
        val shape = RoundedCornerShape(8.dp)
        Box(Modifier.fillMaxWidth().clip(shape).background(c.fg.copy(alpha = 0.08f))
            .then(if (typing) Modifier.border(2.dp, c.accent.copy(alpha = 0.5f), shape) else Modifier)
            .then(if (editable) Modifier.clickable(onClickLabel = label) { typing = true }.semantics { contentDescription = label } else Modifier)
            .padding(horizontal = 4.dp, vertical = 4.dp).heightIn(min = 28.dp), contentAlignment = Alignment.CenterStart) {
            PillFlow(if (open || typing) Int.MAX_VALUE else 2, shown.map { k -> { Pill(k, named(k), if (editable) ({ changed(shown - k) }) else null) } },
                more = { n -> MorePill(n) { open = true } },
                typing = if (typing) ({
                    BasicTextField(typed, { typed = it }, singleLine = true, textStyle = Type.body.copy(color = c.fg),
                        cursorBrush = SolidColor(c.accent),
                        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                        keyboardActions = KeyboardActions(onDone = { if (typed.isNotBlank()) found.firstOrNull()?.let { add(it) } else keyboard?.hide() }),
                        modifier = Modifier.focusRequester(focus).onPreviewKeyEvent { e ->
                                if (e.type == KeyEventType.KeyDown && e.key == Key.Backspace && typed.isEmpty() && shown.isNotEmpty()) {
                                    changed(shown.dropLast(1)); true
                                } else false
                            }.padding(horizontal = 4.dp, vertical = 4.dp),
                        decorationBox = { inner ->
                            Box { if (typed.isEmpty() && shown.isEmpty()) Text(Wording.Access.nobody, style = Type.body, color = c.dim); inner() }
                        })
                }) else null,
                empty = { Text(Wording.Access.nobody, style = Type.body, color = c.dim, modifier = Modifier.padding(horizontal = 4.dp, vertical = 4.dp)) })
        }
        if (typing) PopupAt(DpOffset.Zero, { typing = false; typed = ""; open = false }, focusable = false) { most ->
            val pop = RoundedCornerShape(Popups.radius)
            Column(Modifier.widthIn(min = 220.dp, max = most.width).heightIn(max = minOf(most.height, 300.dp)).shadow(8.dp, pop).clip(pop)
                .background(c.dialog).verticalScroll(rememberScrollState()).padding(vertical = Popups.pad).width(IntrinsicSize.Max)) {
                if (found.isEmpty() && known.isEmpty()) Text(Wording.Access.noMembers, style = Type.body, color = c.dim,
                    modifier = Modifier.padding(horizontal = Popups.inset, vertical = 6.dp))
                for ((i, k) in found.withIndex()) MenuLine(label = named(k), onClick = { add(k) }) {
                    Box(Modifier.size(24.dp), contentAlignment = Alignment.Center) {
                        when (k) {
                            EVERYBODY -> Icon(Adwaita.SystemUsers, null, tint = c.fg, modifier = Modifier.size(16.dp.icon))
                            SAME -> Icon(Adwaita.NetworkServer, null, tint = c.fg, modifier = Modifier.size(16.dp.icon))
                            else -> Avatar(named(k), null, 24.dp)
                        }
                    }
                    Text(named(k), style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis,
                        fontWeight = if (i == 0 && typed.isNotEmpty()) FontWeight.SemiBold else null)
                }
            }
        }
    }
}

/** One of a field's pills: a member's picture (its initials), or Everybody's or Same as Host's icon, the name, and ×
 * taking it out (remove; null: none). */
@Composable
private fun Pill(key: String, name: String, remove: (() -> Unit)?) {
    val c = LocalBaude.current
    val all = key == EVERYBODY || key == SAME
    Row(Modifier.clip(RoundedCornerShape(99.dp)).background(if (all) c.accentBg.copy(alpha = 0.22f) else c.fg.copy(alpha = 0.10f))
        .padding(start = if (all) 8.dp else 3.dp, end = if (remove != null) 2.dp else 10.dp).heightIn(min = 26.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
        when (key) {
            EVERYBODY -> Icon(Adwaita.SystemUsers, null, tint = c.accent, modifier = Modifier.size(14.dp.icon))
            SAME -> Icon(Adwaita.NetworkServer, null, tint = c.accent, modifier = Modifier.size(14.dp.icon))
            else -> Avatar(name, null, 20.dp)
        }
        Text(name, style = Type.body, color = if (all) c.accent else c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis,
            modifier = Modifier.widthIn(max = 180.dp))
        if (remove != null) Box(Modifier.size(24.dp).clip(CircleShape).clickable(onClickLabel = Wording.Common.remove, onClick = remove)
            .semantics { contentDescription = "${Wording.Common.remove} $name" }, contentAlignment = Alignment.Center) {
            Icon(Adwaita.WindowClose, null, tint = if (all) c.accent else c.dim, modifier = Modifier.size(10.dp.icon))
        }
    }
}

/** "<n> more": the pills a field has past what it shows, opening it out to every one. */
@Composable
private fun MorePill(n: Int, onClick: () -> Unit) {
    val c = LocalBaude.current
    val label = Wording.Access.nMore(n = n)
    Box(Modifier.clip(RoundedCornerShape(99.dp)).border(1.dp, c.fg.copy(alpha = 0.15f), RoundedCornerShape(99.dp))
        .background(c.fg.copy(alpha = 0.06f)).clickable(onClickLabel = label, onClick = onClick)
        .padding(horizontal = 10.dp).heightIn(min = 26.dp), contentAlignment = Alignment.Center) {
        Text(label, style = Type.bold, color = c.fg, maxLines = 1)
    }
}

/** Pills wrapped onto lines, at most lines of them, then where typing goes (typing, the rest of its line, wide enough
 * for a few letters), or empty with no pills and no typing; past lines, as many as fit with more(the count of the rest)
 * after them. */
@Composable
private fun PillFlow(lines: Int, pills: List<@Composable () -> Unit>, more: @Composable (Int) -> Unit,
                     typing: (@Composable () -> Unit)?, empty: @Composable () -> Unit) {
    SubcomposeLayout { cs ->
        val width = cs.maxWidth
        val gap = 4.dp.roundToPx()
        val least = if (typing != null) 64.dp.roundToPx() else 0
        val loose = Constraints(maxWidth = width)
        val ps = pills.mapIndexed { i, p -> subcompose("p$i", p).first().measure(loose) }
        /** [(x, line)] for those widths, and where on its line the next thing (typing) goes. */
        fun lay(ws: List<Int>): Triple<List<Pair<Int, Int>>, Int, Int> {
            var x = 0; var line = 0
            val at = ws.map { w -> if (x > 0 && x + w > width) { x = 0; line++ }; (x to line).also { x += w + gap } }
            if (least > 0 && x > 0 && x + least > width) { x = 0; line++ }
            return Triple(at, x, line)
        }
        var shownPills = ps
        var morePl: Placeable? = null
        var placed = lay(ps.map { it.width })
        if (placed.third + 1 > lines) {
            for (k in ps.size - 1 downTo 0) {
                val m = subcompose("m${ps.size - k}") { more(ps.size - k) }.first().measure(loose)
                val trial = lay(ps.take(k).map { it.width } + m.width)
                if (trial.third + 1 <= lines || k == 0) { shownPills = ps.take(k); morePl = m; placed = trial; break }
            }
        }
        val items = shownPills + listOfNotNull(morePl)
        val (at, tx, tline) = placed
        val typer = typing?.let { subcompose("t", it).first().measure(Constraints.fixedWidth(maxOf(least, width - tx)).copy(minHeight = 0)) }
        val blank = if (typing == null && items.isEmpty()) subcompose("e", empty).first().measure(loose) else null
        val count = maxOf(if (typer != null) tline + 1 else 0, (at.maxOfOrNull { it.second } ?: -1) + 1, if (blank != null) 1 else 0)
        val lineH = (items.map { it.height } + listOfNotNull(typer?.height, blank?.height)).maxOrNull() ?: 0
        val h = count * lineH + maxOf(0, count - 1) * gap
        layout(width, h) {
            items.forEachIndexed { i, p -> p.place(at[i].first, at[i].second * (lineH + gap) + (lineH - p.height) / 2) }
            typer?.place(tx, tline * (lineH + gap) + (lineH - typer.height) / 2)
            blank?.place(0, (lineH - blank.height) / 2)
        }
    }
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
