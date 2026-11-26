package dev.baude.android

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withLink
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import org.json.JSONObject

/** The accounts a host keeps, as its accounts answer last said them (hostaccounts):
 * Compose state. */
class HostAccounts(val hostId: String) {
    val accounts = mutableStateOf<List<Msg.Account>>(emptyList())
    val defaults = mutableStateOf<Map<String, String>>(emptyMap())
    val error = mutableStateOf<String?>(null)
    val loaded = mutableStateOf(false)
    val usage = mutableStateMapOf<String, Usage>() // (each subscription's, by usage key: an account's id, "" Claude's CLI's own sign-in, CODEX_CLI Codex's)
    val last = mutableStateOf<Map<String, List<Limit>>>(emptyMap()) // (what the host last read of each, shown greyed till usage has it)
    val codexUsage = mutableStateOf(false) // (whether the host reads Codex usage: an older host's reads of Codex's CLI default are Claude's)
    val claudeCli = mutableStateOf(true) // (whether the host has claude: without it, a Claude account is a token)
    val missing = mutableStateOf<Map<String, Msg.Dep>>(emptyMap()) // (a provider's program the host lacks, by provider)
    private val reading = HashSet<String>()

    /** A subscription's usage as last read: its limits (name, percent), or what to say instead. */
    class Usage(val limits: List<Limit>?, val said: String?)

    /** One limit: its key (five_hour…), name, percent used, and when it resets (seconds since 1970), if said. */
    class Limit(val key: String, val name: String, val percent: Double, val resets: Double?)

    /** The subscriptions whose usage is read, by usage key: Claude's CLI's own sign-in (where the host has claude),
     * each Claude account, Codex's CLI's own sign-in (where the host has codex), each Codex account. */
    fun usageIds(): List<String> =
        (if (claudeCli.value) listOf("") else emptyList()) + accounts.value.filter { it.provider == "claude" }.map { it.id.orEmpty() } +
            (if (cliCard("codex")) listOf(CODEX_CLI) else emptyList()) +
            accounts.value.filter { it.provider == "codex" && codexUsage.value }.map { it.id.orEmpty() }

    /** Whether a provider's CLI's own sign-in has a card: the host has its program (Codex's: and reads its usage). */
    fun cliCard(provider: String) = if (provider == "claude") claudeCli.value else provider !in missing.value && codexUsage.value

    /** (on the main thread) Each subscription's (id's) usage asked of the host, one ask at a time each. */
    fun readUsage(ids: List<String>) {
        for (id in ids) {
            if (!reading.add(id)) continue
            Mesh.ask(hostId, (if (id == CODEX_CLI) Msg.Accounts(what = "usage", provider = "codex", id = "")
                else Msg.Accounts(what = "usage", id = id)).json()) { r ->
                reading.remove(id)
                val error = took(r)
                val limits = Msg.AccountsReply.from(r).usage?.takeIf { it.isNotEmpty() }
                usage[id] = when {
                    error != null -> Usage(null, error)
                    limits != null -> Usage(limits(limits), null)
                    else -> Usage(null, Wording.Subscriptions.usageUnavailable)
                }
            }
        }
    }

    /** An accounts request (what, and its fields): the page filled again from what it answers; then(error or null). */
    fun ask(msg: Msg.Accounts, then: (String?) -> Unit = { e -> if (e != null) toast(e) }) {
        Mesh.ask(hostId, msg.json()) { r -> then(took(r)) }
    }

    /** (on the main thread) An accounts answer, or an error: its accounts kept; its error, if it has one. */
    fun took(r: JSONObject): String? {
        if (r.optString("op") == Op.ERROR) return Msg.Error.from(r).said.orEmpty().also { if (!loaded.value) error.value = it }
        val g = Msg.AccountsReply.from(r)
        g.accounts?.let { accounts.value = it }
        g.defaults?.let { d -> defaults.value = d.keys().asSequence().associateWith { d.optString(it) } }
        g.claudeCli?.let { claudeCli.value = it }
        codexUsage.value = g.codexUsage == true
        missing.value = g.missing.orEmpty()
        g.lastUsage?.let { l -> last.value = l.mapValues { limits(it.value.usage.orEmpty()) } }
        loaded.value = true
        error.value = null
        return g.error?.ifBlank { null }
    }

    private fun limits(u: List<Msg.UsageLimit>) =
        u.map { Limit(it.key.orEmpty(), it.name.orEmpty(), it.percent?.toDouble() ?: 0.0, it.resets?.toDouble()) }

    companion object {
        const val USAGE_S = 30 // (how often a shown page reads its subscriptions' usage again)
        const val CODEX_CLI = "codex" // (Codex's CLI's own sign-in's usage key, as its host says it: accounts.CODEX_CLI)
        val PROVIDERS = listOf("claude" to Wording.Report.claude, "codex" to Wording.Programs.codex)
    }
}

/** Settings → Subscriptions (subscriptionpage.Page), Settings' second page: the Claude and Codex accounts kept on the
 * host picked beside New session, changed there: its name; for each provider, its heading with the pill picking the
 * subscription a new chat takes, then a card for each subscription (CLI default first, where the host has the
 * provider's program), each account's with ⋮ (Rename…, Sign In Again… or Replace Token…, Remove…), and each card's usage as a
 * bar for each limit with its percentage, read as the page is shown and every USAGE_S while it is and the app is in front; then Add account
 * (where the host lacks the provider's program, its Dependencies row instead: DepRow). */
@Composable
fun SubscriptionsPage() {
    val c = LocalBaude.current
    val hostId = picked()?.takeIf { it != "phone" } ?: Mesh.hosts.firstOrNull()?.id // (computer hosts only: the phone keeps no accounts)
    val host = remember(hostId) { hostId?.let { HostAccounts(it) } }
    var dialog by remember { mutableStateOf<Pair<String, Any?>?>(null) } // (which dialog, and its provider or account)
    LaunchedEffect(hostId) {
        val h = host ?: return@LaunchedEffect
        Mesh.ask(h.hostId, Msg.Accounts(what = "list").json()) { r -> h.took(r)?.let { e -> h.error.value = e } }
    }
    val ids = host?.usageIds().orEmpty()
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    LaunchedEffect(hostId, ids, lifecycle) {
        val h = host ?: return@LaunchedEffect
        lifecycle.repeatOnLifecycle(Lifecycle.State.RESUMED) { // (the app in front: read at once, then every USAGE_S)
            while (true) {
                h.readUsage(ids)
                delay(HostAccounts.USAGE_S * 1000L)
            }
        }
    }
    Column(Modifier.fillMaxSize().background(c.frame)) {
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
            BoxedList(listOf(Unit), "") {
                ActionRow(Wording.NewSession.host, null, suffix = {
                    Text(hostId?.let { Mesh.label(it) } ?: Wording.MeshVersions.none, style = Type.body, color = c.dim)
                })
            }
            if (host != null) for ((provider, title) in HostAccounts.PROVIDERS) {
                val error = host.error.value
                val mine = host.accounts.value.filter { it.provider == provider }
                Row(Modifier.padding(start = 14.dp, end = 10.dp, top = 6.dp), verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    modelLogo(if (provider == "claude") "claude" else "gpt", title)?.let {
                        Icon(it, null, tint = c.fg, modifier = Modifier.size(16.dp.icon))
                    }
                    Text(title, style = Type.bold, color = c.fg, modifier = Modifier.weight(1f))
                }
                if (error != null) {
                    BoxedList(listOf(Unit), "") { ActionRow(error, null) }
                    continue
                }
                val names = mapOf("" to Wording.Subscriptions.cliDefault) + mine.associate { it.id.orEmpty() to it.name.orEmpty() }
                BoxedList(listOf(Unit), "") {
                    Choice(Wording.Subscriptions.current, names.keys.toList(), host.defaults.value[provider]?.takeIf { it in names } ?: "",
                        { names.getValue(it) }, enabled = host.loaded.value) { id ->
                        host.ask(Msg.Accounts(what = "select", provider = provider, id = id))
                    }
                }
                val cli = if (provider == "claude") "" else HostAccounts.CODEX_CLI
                if (host.cliCard(provider))
                    SubscriptionCard(Wording.Subscriptions.cliDefault, null, host.usage[cli], host.last.value[cli], true) {}
                for (a in mine) SubscriptionCard(a.name.orEmpty(), a, host.usage[a.id.orEmpty()], host.last.value[a.id.orEmpty()],
                    provider == "claude" || host.codexUsage.value) {
                    kind -> dialog = kind to a }
                val lacks = host.missing.value[provider] // (its program isn't on the host: what Dependencies says, no account to add)
                BoxedList(listOf(Unit), "") {
                    if (lacks != null) DepRow(lacks, depTitle(lacks, title))
                    else ButtonRow(Wording.Subscriptions.addAccount, Adwaita.ListAdd) { dialog = "add" to provider }
                }
            }
        }
    }
    val (kind, of) = dialog ?: return
    val close = { dialog = null }
    if (host == null) return
    val a = of as? Msg.Account
    when {
        kind == "add" -> AddAccount(host, of as String, close)
        a == null -> close()
        kind == "rename" -> RenameAccount(host, a, close)
        kind == "again" && a.kind == "token" -> ReplaceToken(host, a, close)
        kind == "again" && a.provider == "claude" -> ClaudeSignIn(host, a.id.orEmpty(), close)
        kind == "again" && a.provider == "codex" && host.missing.value["codex"] != null ->
            host.missing.value.getValue("codex").let { SignInLacks(it, depTitle(it, Wording.Programs.codex), a, close) }
        kind == "again" -> CodexSignIn(host, a.id.orEmpty(), null, close)
        kind == "remove" -> RemoveAccount(host, a, close)
    }
}

/** A subscription's card: its name and, an account's, ⋮ with what can be done to it (pick: "rename", "again" or
 * "remove"); its usage: a bar for each limit with its name and percentage over it, else what was
 * said instead. Till it's first read here, what the host last read (last) greyed (Checking… where it has none). */
@Composable
private fun SubscriptionCard(name: String, a: Msg.Account?, usage: HostAccounts.Usage?, last: List<HostAccounts.Limit>?,
                             shown: Boolean, pick: (String) -> Unit) {
    val c = LocalBaude.current
    val shape = RoundedCornerShape(12.dp)
    Column(Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 4.dp).clip(shape).background(c.card)
        .border(1.dp, c.rule, shape).padding(start = 12.dp, end = 6.dp, top = 4.dp, bottom = 12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(Modifier.fillMaxWidth().heightIn(min = 36.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(name, style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            if (a != null) AccountMenu(a, pick)
        }
        if (shown) UsageBars(usage, last, Modifier.padding(end = 6.dp))
    }
}

/** A subscription's usage (a Subscriptions card's, and ⋮'s usage row's): a line for each limit, its name and percentage,
 * its bar under it; else what was said instead. Till it's read, what the host last read (last) greyed (Checking… where
 * it has none). */
@Composable
private fun UsageBars(usage: HostAccounts.Usage?, last: List<HostAccounts.Limit>?, modifier: Modifier = Modifier) {
    val c = LocalBaude.current
    val stale = usage == null && last != null
    Column(modifier.alpha(if (stale) Tokens.Subscriptions.staleOpacity.toFloat() else 1f),
        verticalArrangement = Arrangement.spacedBy(6.dp)) {
        val limits = if (stale) last else usage?.limits
        if (limits == null) Text(usage?.said ?: Wording.Subscriptions.checking, style = Type.body, color = c.dim)
        else for (l in limits) {
            val percent = l.percent
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(limitLabel(l), style = Type.body, color = c.fg, modifier = Modifier.weight(1f))
                Text(Wording.Subscriptions.usagePercent(percent = "%.4g".format(java.util.Locale.ROOT, percent)
                    .trimEnd('0').trimEnd('.')), style = Type.body, color = c.dim)
            }
            Box(Modifier.fillMaxWidth().height(6.dp).clip(RoundedCornerShape(50)).background(c.fg.copy(alpha = 0.15f))) {
                Box(Modifier.fillMaxWidth((percent / 100).toFloat().coerceIn(0f, 1f)).fillMaxHeight().clip(RoundedCornerShape(50))
                    .background(c.accentBg))
            }
        }
    }
}

/** ⋮'s usage row (subscriptionpage.MenuUsage): the current Claude subscription on the host picked beside New session (the
 * one a new chat there takes), its name, then its limits as its Subscriptions card has them, read as ⋮ opens (again once
 * USAGE_S has passed); the host's last reading greyed till then. Nothing where the host has no Claude subscription. Its
 * menu item opens Settings → Subscriptions as it's tapped (MainMenu). */
@Composable
fun MenuUsage() {
    val c = LocalBaude.current
    val hostId = picked()?.takeIf { it != "phone" } ?: Mesh.hosts.firstOrNull()?.id ?: return
    val host = MenuUsageHosts.of(hostId)
    LaunchedEffect(hostId) {
        Mesh.ask(hostId, Msg.Accounts(what = "list").json()) { r ->
            host.took(r)?.let { e -> host.error.value = e }
            val id = MenuUsageHosts.profile(host) ?: return@ask
            if (System.currentTimeMillis() - (MenuUsageHosts.readAt[hostId] ?: 0L) >= HostAccounts.USAGE_S * 1000L) {
                MenuUsageHosts.readAt[hostId] = System.currentTimeMillis()
                host.usage.remove(id)
                host.readUsage(listOf(id))
            }
        }
    }
    if (!host.loaded.value && host.error.value == null) return
    val id = MenuUsageHosts.profile(host)
    if (host.error.value == null && id == null) return
    val name = host.accounts.value.firstOrNull { it.provider == "claude" && it.id.orEmpty() == id }?.name ?: Wording.Subscriptions.cliDefault
    Column(Modifier.fillMaxWidth().padding(start = menuTextStart(), end = Popups.inset, top = 8.dp, bottom = 8.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp)) {
        val error = host.error.value
        if (error != null) Text(error, style = Type.body, color = c.dim)
        else {
            Text(name, style = Type.bold, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis)
            UsageBars(host.usage[id], host.last.value[id])
        }
    }
}

/** ⋮'s usage row's hosts' accounts, kept between its openings (as the desktop's row keeps its reading), and when each
 * was last read. */
object MenuUsageHosts {
    private val hosts = HashMap<String, HostAccounts>()
    val readAt = HashMap<String, Long>()
    fun of(id: String) = hosts.getOrPut(id) { HostAccounts(id) }

    /** The current Claude subscription's id ("" the CLI's own sign-in), or null where the host has none. */
    fun profile(h: HostAccounts): String? {
        val id = h.defaults.value["claude"].orEmpty()
        val account = h.accounts.value.any { it.provider == "claude" && it.id.orEmpty() == id && id != "" }
        return if (account) id else if (h.claudeCli.value) "" else null
    }
}

/** A limit's name over its bar, with when it resets in the viewer's local time and clock: Five Hour Limit (Resets 3:00 PM),
 * Weekly Limit (Resets Friday 3:00 PM), or just the time where it resets today. */
@Composable
private fun limitLabel(l: HostAccounts.Limit): String {
    val resets = l.resets ?: return l.name
    val ctx = LocalContext.current
    val at = java.time.Instant.ofEpochSecond(resets.toLong()).atZone(java.time.ZoneId.systemDefault())
    val clock = if (android.text.format.DateFormat.is24HourFormat(ctx)) "HH:mm" else "h:mm a"
    val time = at.format(java.time.format.DateTimeFormatter.ofPattern(clock))
    val day = at.format(java.time.format.DateTimeFormatter.ofPattern("EEEE"))
    val today = at.toLocalDate() == java.time.LocalDate.now()
    return when {
        l.key == "five_hour" -> Wording.Subscriptions.fiveHourLimit(time = time)
        l.key == "seven_day" && today -> Wording.Subscriptions.weeklyLimitToday(time = time)
        l.key == "seven_day" -> Wording.Subscriptions.weeklyLimit(day = day, time = time)
        today -> Wording.Subscriptions.otherLimitToday(name = l.name, time = time)
        else -> Wording.Subscriptions.otherLimit(name = l.name, day = day, time = time)
    }
}

/** An account's ⋮ with what can be done to it (pick: "rename", "again" or "remove"). */
@Composable
private fun AccountMenu(a: Msg.Account, pick: (String) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Box {
        IconButton(Adwaita.ViewMore, Wording.Subscriptions.moreTip) { open = true }
        Popover(open, { open = false }) {
            MenuChoice(Wording.Common.renameEllipsis, false) { open = false; pick("rename") }
            MenuChoice(if (a.kind == "token") Wording.Subscriptions.replaceToken else Wording.Subscriptions.signInAgain, false) {
                open = false
                pick("again")
            }
            MenuChoice(Wording.Common.removeEllipsis, false) { open = false; pick("remove") }
        }
    }
}

/** A dialog's form that stays till what it sends is done: its fields, why it wasn't, and its button (submit; none
 * while a card in it says what can be done). */
@Composable
private fun Form(heading: String, submit: String?, done: () -> Unit, said: String?, busy: Boolean, go: () -> Unit,
                 fields: @Composable () -> Unit) {
    val c = LocalBaude.current
    AlertDialog(
        onDismissRequest = done,
        title = { Text(heading) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                fields()
                said?.let { SelectionContainer { Text(it, style = Type.body, color = c.fg) } }
            }
        },
        confirmButton = { if (submit != null) Button(submit, suggested = true, enabled = !busy, onClick = go) },
        dismissButton = { Button(Wording.Common.close, onClick = done) },
        containerColor = c.dialog,
    )
}

/** An account's Sign In Again… where its provider's program isn't on the host: the heading and, in place of the sign-in
 * button, the Dependencies row (dep, titled title). */
@Composable
private fun SignInLacks(dep: Msg.Dep, title: String, a: Msg.Account, done: () -> Unit) {
    val c = LocalBaude.current
    AlertDialog(
        onDismissRequest = done,
        title = { Text(Wording.Subscriptions.signInHeading(name = a.name.orEmpty())) },
        text = { BoxedList(listOf(Unit), "") { DepRow(dep, title) } },
        confirmButton = { Button(Wording.Common.close, onClick = done) },
        containerColor = c.dialog,
    )
}

/** msg sent from a form: done once it's done, else why not (said). */
private fun send(host: HostAccounts, msg: Msg.Accounts, busy: (Boolean) -> Unit, said: (String?) -> Unit, done: () -> Unit) {
    busy(true)
    host.ask(msg) { e ->
        busy(false)
        if (e != null) said(e) else done()
    }
}

@Composable
private fun AddAccount(host: HostAccounts, provider: String, done: () -> Unit) {
    if (provider == "codex") return CodexSignIn(host, null, "", done)
    if (host.claudeCli.value) return ClaudeSignIn(host, null, done)
    var name by remember { mutableStateOf("") }
    var token by remember { mutableStateOf("") }
    var said by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    val go = {
        if (name.isNotBlank()) send(host, Msg.Accounts(what = "add", provider = "claude", name = name.trim(), token = token.trim()),
            { busy = it }, { said = it }, done)
    }
    Form(Wording.Subscriptions.addClaude, Wording.Subscriptions.saveAccount, done, said, busy, go) {
        NameField(name, { name = it }, Wording.Subscriptions.nameHint, go)
        SecretField(token, { token = it }, Wording.Subscriptions.tokenHint, go)
    }
}

@Composable
private fun RenameAccount(host: HostAccounts, a: Msg.Account, done: () -> Unit) {
    var name by remember { mutableStateOf(a.name.orEmpty()) }
    var said by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    val go = {
        if (name.isNotBlank()) send(host, Msg.Accounts(what = "rename", id = a.id.orEmpty(), name = name.trim()),
            { busy = it }, { said = it }, done)
    }
    Form(Wording.Subscriptions.renameHeading(name = a.name.orEmpty()), Wording.Common.rename, done, said, busy, go) {
        NameField(name, { name = it }, Wording.Subscriptions.nameHint, go)
    }
}

@Composable
private fun ReplaceToken(host: HostAccounts, a: Msg.Account, done: () -> Unit) {
    var token by remember { mutableStateOf("") }
    var said by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    val go = { send(host, Msg.Accounts(what = "token", id = a.id.orEmpty(), token = token.trim()), { busy = it }, { said = it }, done) }
    Form(Wording.Subscriptions.replaceHeading(name = a.name.orEmpty()), Wording.Common.save, done, said, busy, go) {
        SecretField(token, { token = it }, Wording.Subscriptions.tokenHint, go)
    }
}

@Composable
private fun RemoveAccount(host: HostAccounts, a: Msg.Account, done: () -> Unit) {
    val c = LocalBaude.current
    AlertDialog(
        onDismissRequest = done,
        title = { Text(Wording.Subscriptions.removeHeading(name = a.name.orEmpty())) },
        confirmButton = {
            Button(Wording.Common.remove, destructive = true) {
                done()
                host.ask(Msg.Accounts(what = "remove", id = a.id.orEmpty()))
            }
        },
        dismissButton = { Button(Wording.Common.cancel, onClick = done) },
        containerColor = c.dialog,
    )
}

/** Codex's sign-in run on a host (codex login, by hostaccounts' sign_in), on a connection of its own: a new account's
 * (id null: named name, added once it's signed in) or an account's (id); the browser's (method browser, the default)
 * or the device code's (device). Its state as the host says it (login, a ChatLogin: signing in, with the page to open,
 * and the device code's code to enter there, once codex has said them; signed in, as who; failed, with why); null till
 * one's started, and once it's cancelled. The browser's page sends the browser back to the host's own 127.0.0.1, which
 * the phone's isn't: the address it ends on is pasted (address) and passed to the host (paste: callback), and what
 * became of it said (note). stop() lets go of the connection, which stops it there. Compose state, changed on the main
 * thread. */
class CodexLogin(val host: HostAccounts, val id: String?) {
    val name = mutableStateOf("")
    val home = mutableStateOf("")
    val login = mutableStateOf<Msg.ChatLogin?>(null)
    val said = mutableStateOf<String?>(null) // (a signed-in CODEX_HOME's add: why it wasn't)
    val busy = mutableStateOf(false)
    val copied = mutableStateOf(false)
    val address = mutableStateOf("")
    val note = mutableStateOf<String?>(null)
    private var conn: Connection? = null

    fun start(method: String = "browser") {
        conn?.stop()
        said.value = null
        copied.value = false
        address.value = ""
        note.value = null
        login.value = Msg.ChatLogin(state = "signing_in", method = method)
        val fresh = id == null
        conn = Mesh.watch(host.hostId, { w, m ->
            if (m.optInt("req", -1) != REQ) return@watch
            Mesh.post {
                if (w !== conn) return@post
                val g = Msg.AccountsReply.from(m)
                if (m.optString("op") == Op.ACCOUNTS && g.what == "callback") { // (a pasted address: codex's end follows, else why not)
                    val page = g.page
                    note.value = g.error ?: page?.body?.takeIf { (page.status?.toInt() ?: 0) >= 400 && page.type.orEmpty().startsWith("text/plain") }
                    if (note.value == null) address.value = ""
                    return@post
                }
                if (m.optString("op") == Op.ACCOUNTS && g.login != null && g.accounts == null) {
                    login.value = g.login
                    return@post
                }
                if (m.optString("op") == Op.ACCOUNTS && g.line != null && g.accounts == null) return@post // (an older host's lines: not an end)
                conn = null
                w.stop()
                val e = host.took(m)
                login.value = g.login ?: if (e == null) Msg.ChatLogin(state = "signed_in", who = g.who)
                    else Msg.ChatLogin(state = "failed", reason = e, method = method)
            }
        }) { w ->
            if (w !== conn) return@watch
            if (w.state == "up") w.send(Msg.Accounts(req = REQ, what = "sign_in", id = id, name = name.value.trim().takeIf { fresh },
                method = method).json())
            else if (w.state == "down") end(Wording.HostStatus.connectionDropped)
        }
        if (conn == null) end(Wording.HostStatus.hostAbsent)
    }

    private fun end(why: String) {
        conn = null
        login.value = Msg.ChatLogin(state = "failed", reason = why)
    }

    fun paste() {
        val w = conn ?: return
        if (address.value.isNotBlank() && w.send(Msg.Accounts(req = REQ, what = "callback", url = address.value.trim()).json()))
            note.value = Wording.Subscriptions.checkingAddress
    }

    fun cancel() {
        stop()
        login.value = null
    }

    fun stop() {
        conn?.stop()
        conn = null
    }
}

/** Add account (Codex) and Sign In Again… (a Codex account, id): an existing signed-in CODEX_HOME on the host (a new
 * account's only), or Codex's device-code sign-in run there (CodexLogin), as a card (CodexCard); Close lets go of it,
 * which stops it there. name: a new account's ("" to start), null for one signed in to again. */
@Composable
internal fun CodexSignIn(host: HostAccounts, id: String?, name: String?, done: () -> Unit,
                         login: CodexLogin = remember { CodexLogin(host, id).apply { this.name.value = name.orEmpty() } }) {
    DisposableEffect(login) { onDispose { login.stop() } }
    val fresh = id == null
    var label by login.name
    var home by login.home
    val account = host.accounts.value.firstOrNull { it.id == id }?.name.orEmpty()
    val state = login.login.value
    val go = {
        when {
            fresh && label.isBlank() -> Unit
            fresh && home.isNotBlank() -> { login.cancel(); send(host, Msg.Accounts(what = "add", provider = "codex",
                name = label.trim(), home = home.trim()), { login.busy.value = it }, { login.said.value = it }, done) }
            else -> login.start()
        }
    }
    val heading = if (fresh) Wording.Subscriptions.addCodex else Wording.Subscriptions.signInHeading(name = account)
    Form(heading, if (state != null) null else if (fresh && home.isNotBlank()) Wording.Subscriptions.useProfile
        else Wording.SignIn.signIn, { login.stop(); done() }, login.said.value, login.busy.value, go) {
        if (fresh) {
            val open = state == null || state.state == "failed"
            NameField(label, { label = it }, Wording.Subscriptions.nameHint, go, enabled = open)
            NameField(home, { home = it }, Wording.Subscriptions.codexHomeHint, go, focus = false, enabled = open)
        }
        if (state != null) CodexCard(login, if (fresh) label.trim() else account, go)
    }
}

/** A Codex sign-in as a card, the way a chat's Sign in card is (LoginCard's look: the warning-bordered box, its title
 * bold, then a line, then what can be done now): signing in, then its page's link (opened here: the host's browser
 * isn't the phone's); the browser's with a box for the address the browser ends on (Send), and "Use a device code
 * instead"; the device code's with the code to enter there, large, with Copy; and Cancel. Signed in, as who (else the
 * account's name); failed, why, and Try Again (again; and the device code's way, where the browser's failed). */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun CodexCard(login: CodexLogin, name: String, again: () -> Unit) {
    val c = LocalBaude.current
    val l = login.login.value ?: return
    val dim = c.fg.copy(alpha = c.fg.alpha * 0.6f)
    val clip = LocalClipboardManager.current
    val browser = l.method != "device"
    Column(Modifier.fillMaxWidth().border(1.dp, c.warning, RoundedCornerShape(Gap.cardRadius))
        .padding(horizontal = 12.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        val title = when (l.state) {
            "signed_in" -> Wording.Subscriptions.codexSignedIn
            "failed" -> Wording.SignIn.failed
            else -> Wording.Subscriptions.codexSigningIn
        }
        SelectionContainer { Text(title, style = Type.bold, color = c.fg) }
        val url = l.url
        val code = l.code
        when {
            l.state == "signing_in" && url == null ->
                Text(if (browser) Wording.SignIn.starting else Wording.Subscriptions.requestingCode, style = Type.body, color = dim)
            l.state == "signing_in" && url != null -> {
                Text(buildAnnotatedString {
                    val (before, after) = Wording.SignIn.pageThere(link = "\u0000").split("\u0000", limit = 2).let {
                        it[0] to it.getOrElse(1) { "" }
                    }
                    append(before)
                    withLink(LinkAnnotation.Url(url, TextLinkStyles(SpanStyle(color = c.accent,
                        textDecoration = TextDecoration.Underline)))) { append(Wording.SignIn.openPageThere) }
                    append(after)
                }, style = Type.body, color = c.fg)
                if (browser) {
                    var address by login.address
                    NameField(address, { address = it }, Wording.Subscriptions.addressHint, login::paste, focus = false)
                    Button(Wording.Common.send, onClick = login::paste)
                    login.note.value?.let { SelectionContainer { Text(it, style = Type.body, color = dim) } }
                }
                // (the code a line of its own, its buttons under it: beside them, a phone's width breaks it)
                else if (code != null) SelectionContainer { Text(code, style = Type.deviceCode, color = c.fg, softWrap = false) }
            }
            l.state == "signed_in" -> SelectionContainer {
                Text(l.who?.let { Wording.SignIn.asWho(who = it) } ?: name, style = Type.body, color = c.fg)
            }
            else -> SelectionContainer { Text(l.reason ?: Wording.Subscriptions.codexFailed, style = Type.body, color = dim) }
        }
        // (a phone's width wraps "Use a device code instead" onto a line of its own)
        when (l.state) {
            "signing_in" -> FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                l.code?.takeIf { l.url != null && !browser }?.let { code ->
                    Button(if (login.copied.value) Wording.Subscriptions.codeCopied else Wording.Common.copy) {
                        clip.setText(AnnotatedString(code))
                        login.copied.value = true
                    }
                }
                if (browser) Button(Wording.Subscriptions.deviceInstead) { login.start("device") }
                Button(Wording.Common.cancel) { login.cancel() }
            }
            "failed" -> FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Button(Wording.Common.tryAgain, suggested = true, onClick = again)
                if (browser) Button(Wording.Subscriptions.deviceInstead) { login.start("device") }
            }
        }
    }
}

/** claude auth login run on a host with a Claude account's own folder (hostaccounts' sign_in), on a connection of its
 * own: a new account's (id null: named name, added once it's signed in) or an account's (id). The page to open (url)
 * as the host passes it on, a code that page shows sent to it on the same connection (paste), what to say (said);
 * stop() lets go of the connection, which stops it there. Compose state, changed on the main thread. */
class ClaudeLogin(val host: HostAccounts, val id: String?) {
    val name = mutableStateOf("")
    val folder = mutableStateOf("")
    val code = mutableStateOf("")
    val url = mutableStateOf<String?>(null)
    val said = mutableStateOf<String?>(null)
    val busy = mutableStateOf(false)
    private var conn: Connection? = null
    private var checking = false

    fun start(done: () -> Unit) {
        val lines = ArrayList<String>()
        checking = false
        busy.value = true
        url.value = null
        said.value = Wording.SignIn.starting
        conn?.stop()
        val fresh = id == null
        conn = Mesh.watch(host.hostId, { w, m ->
            if (m.optInt("req", -1) != REQ) return@watch
            Mesh.post {
                if (w !== conn) return@post
                val g = Msg.AccountsReply.from(m)
                if (m.optString("op") == Op.ACCOUNTS && g.line != null && g.accounts == null) {
                    val found = Regex("https://\\S+").find(g.line)
                    if (found != null) {
                        url.value = found.value.trimEnd('.', ',', ')')
                        said.value = null
                    } else if (!g.line.startsWith("Opening browser")) {
                        lines.add(g.line)
                        if (checking) { // (what claude says of a pasted code: "Invalid code…", and it asks again)
                            checking = false
                            said.value = g.line
                        }
                    }
                    return@post
                }
                conn = null
                w.stop()
                busy.value = false
                url.value = null
                val e = host.took(m)
                if (e != null) said.value = e else done()
            }
        }) { w ->
            if (w !== conn) return@watch
            if (w.state == "up") w.send(Msg.Accounts(req = REQ, what = "sign_in", provider = "claude".takeIf { fresh }, id = id,
                name = name.value.trim().takeIf { fresh }).json())
            else if (w.state == "down") end(Wording.HostStatus.connectionDropped)
        }
        if (conn == null) end(Wording.HostStatus.hostAbsent)
    }

    private fun end(why: String) {
        busy.value = false
        url.value = null
        said.value = why
    }

    fun paste() {
        val w = conn ?: return
        if (code.value.isNotBlank() && w.send(Msg.Accounts(req = REQ, what = "code", code = code.value.trim()).json())) {
            code.value = ""
            checking = true
            said.value = Wording.SignIn.checkingCode
        }
    }

    fun stop() {
        conn?.stop()
        conn = null
    }
}

/** Add account (Claude, on a host with claude) and Sign In Again… (a folder account, id): an existing signed-in Claude
 * folder on the host (a new account's only), or claude auth login run there with the account's own folder
 * (ClaudeLogin): the page to open shown as a link, a box for the code that page may show; Close stops it. */
@Composable
internal fun ClaudeSignIn(host: HostAccounts, id: String?, done: () -> Unit, login: ClaudeLogin = remember { ClaudeLogin(host, id) }) {
    val c = LocalBaude.current
    DisposableEffect(login) { onDispose { login.stop() } }
    val fresh = id == null
    var name by login.name
    var folder by login.folder
    var code by login.code
    val go = {
        when {
            fresh && name.isBlank() -> Unit
            fresh && folder.isNotBlank() -> send(host, Msg.Accounts(what = "add", provider = "claude", name = name.trim(),
                folder = folder.trim()), { login.busy.value = it }, { login.said.value = it }, done)
            else -> login.start(done)
        }
    }
    val heading = if (fresh) Wording.Subscriptions.addClaude
        else Wording.Subscriptions.signInHeading(name = host.accounts.value.firstOrNull { it.id == id }?.name.orEmpty())
    Form(heading, if (fresh && folder.isNotBlank()) Wording.Subscriptions.useFolder else Wording.SignIn.signIn,
        { login.stop(); done() }, login.said.value, login.busy.value, go) {
        if (fresh) {
            NameField(name, { name = it }, Wording.Subscriptions.nameHint, go)
            NameField(folder, { folder = it }, Wording.Subscriptions.claudeFolderHint, go, focus = false)
        }
        login.url.value?.let { link ->
            Text(buildAnnotatedString {
                val (before, after) = Wording.SignIn.pageThere(link = "\u0000").split("\u0000", limit = 2).let {
                    it[0] to it.getOrElse(1) { "" }
                }
                append(before)
                withLink(LinkAnnotation.Url(link, TextLinkStyles(SpanStyle(color = c.accent,
                    textDecoration = TextDecoration.Underline)))) { append(Wording.SignIn.openPageThere) }
                append(after)
            }, style = Type.body, color = c.fg)
            NameField(code, { code = it }, Wording.SignIn.codeHint, login::paste, focus = false)
            Button(Wording.Common.send, onClick = login::paste)
        }
    }
}

private const val REQ = 1 // (a sign-in's own connection: its one request)

/** NameField for a secret: what's typed shown as dots. */
@Composable
fun SecretField(value: String, set: (String) -> Unit, hint: String, onDone: () -> Unit) {
    NameField(value, set, hint, onDone, focus = false, secret = true)
}
