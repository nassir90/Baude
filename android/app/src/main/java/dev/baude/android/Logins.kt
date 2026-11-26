package dev.baude.android

import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withLink
import androidx.compose.ui.unit.dp
import org.json.JSONObject

/** Signing in to Claude from a chat, on the machine the chat runs on (Baude's LoginCard): claude auth login run there
 * by its host process (hostlogin), on a connection of its own to it, for the chat (its session). What the card says
 * is the chat's sign-in as its host has it (host: ChatLogin, told as login_state), the same on every head: a turn
 * that failed for want of it says so (that its sign-in expired, or that there's none, with claude's reason) with Sign
 * in; /login starts it at once. What a sign-in from here says comes as it's said: the sign-in page's address, a link
 * to open here; a code that page shows is pasted in the card and passed to it; Cancel lets go of the connection,
 * which stops it there, and the card says again what it said before. Once it's signed in: who as, the chat's claude
 * there started again on it, and Send again for what this phone sent for the failed turn (retry). stage: ask,
 * elsewhere (another head signing in), starting, page, checking, signed, failed, cancelled. Compose state, changed
 * on the main thread. */
class Login(val hostId: String, val sid: String, first: Msg.ChatLogin?, val retry: Pair<String, List<Picked>>?, private val args: List<String>) {
    val key = java.util.UUID.randomUUID().toString()
    /** The chat's Claude account, for a chat run under one, signed in to in place of the CLI's own sign-in, on its
     * host: a folder account's by claude auth login run there with its folder (hostaccounts' sign_in), a token
     * account's by a token from claude setup-token pasted in place of it, replacing that account's (saveToken). */
    val account = Mesh.session(hostId, sid)?.account?.takeIf { it.provider == "claude" && it.kind in listOf("token", "folder") }
    val token = account?.takeIf { it.kind == "token" }
    val host = mutableStateOf(first) // (the chat's sign-in, as its host has it)
    val why: String? get() = host.value?.reason
    val stage = mutableStateOf(if (first != null || token != null) "ask" else "starting")
    val url = mutableStateOf<String?>(null)
    val said = mutableStateOf<String?>(null) // (failed: why; signed: who as)
    private var before: Msg.ChatLogin? = null // (what it said as a sign-in from here started: put back if cancelled)
    val resent = mutableStateOf(false)
    private val lines = ArrayList<String>()
    private var conn: Connection? = null

    init {
        first?.let { hostSaid(it) }
    }

    /** The chat's sign-in as its host has it now: what the card says, unless a sign-in from here is going on (it
     * says how that goes itself). */
    fun hostSaid(l: Msg.ChatLogin?) {
        if (conn != null || token != null) {
            if (l != null) host.value = l
            return
        }
        if (l == null) { // (nothing to sign in for: a /login stopped, from another head)
            if (stage.value == "elsewhere") stage.value = "cancelled"
            return
        }
        host.value = l
        when (l.state) {
            "needed" -> stage.value = "ask"
            "signing_in" -> stage.value = "elsewhere"
            "signed_in" -> { stage.value = "signed"; said.value = l.who }
            "failed" -> { stage.value = "failed"; said.value = l.reason }
        }
    }

    fun start() {
        conn?.stop()
        before = host.value
        lines.clear()
        url.value = null
        said.value = null
        stage.value = "starting"
        conn = Mesh.watch(hostId, { c, m -> heard(c, m) }) { c ->
            if (c !== conn) return@watch
            if (c.state == "up") c.send(if (account != null) Msg.Accounts(req = REQ, what = "sign_in", id = account.id).json()
                else Msg.Login(req = REQ, args = args, session = sid).json())
            else if (c.state == "down") end(null, Wording.HostStatus.connectionDropped)
        }
        if (conn == null) end(null, Wording.HostStatus.hostAbsent)
    }

    /** (on its connection's thread) What the host says of it. */
    private fun heard(c: Connection, m: JSONObject) {
        if (m.optInt("req", -1) != REQ) return
        Mesh.post {
            if (c !== conn) return@post
            when (m.optString("op")) {
                Op.LOGIN_SAID -> line(Msg.LoginSaid.from(m).line.orEmpty())
                Op.LOGIN_END -> Msg.LoginEnd.from(m).let { e ->
                    if (e.code?.toInt() == 0) signed(e.who) else end(e.code?.toInt(), e.why)
                }
                Op.ACCOUNTS -> Msg.AccountsReply.from(m).let { r ->
                    when {
                        r.line != null && r.accounts == null -> line(r.line)
                        r.signedIn == true -> signed(r.who)
                        else -> end(null, r.error)
                    }
                }
                Op.ERROR -> end(null, Msg.Error.from(m).said.orEmpty())
            }
        }
    }

    /** A line claude auth login wrote: the sign-in page's address, its link; anything else kept, for if it fails. */
    private fun line(text: String) {
        // (its prompt has no line break: what it says next, "Login failed: …", comes on the prompt's line)
        val l = AUTH_ESCAPES.replace(text, "").replace("Paste code here if prompted > ", "").trim()
        val found = Regex("https://\\S+").find(l)
        if (found != null) {
            url.value = found.value.trimEnd('.', ',', ')')
            if (stage.value == "starting") stage.value = "page"
        } else if (l.isNotEmpty() && !l.startsWith("Opening browser")) lines.add(l)
    }

    fun paste(code: String) {
        val c = conn ?: return
        if (code.isBlank()) return
        val msg = if (account != null) Msg.Accounts(req = REQ, what = "code", code = code.trim()).json()
            else Msg.LoginCode(code = code.trim()).json()
        if (!c.send(msg)) return
        stage.value = "checking"
    }

    fun cancel() {
        stop()
        back()
    }

    /** Cancelled: the card as it was before the sign-in from here started (a failed turn's: that it needs signing
     * in, and why), as its host says it too; a /login's, cancelled. */
    private fun back() {
        val was = before
        said.value = null
        if (was != null) hostSaid(was) else stage.value = "cancelled"
    }

    private fun stop() {
        val c = conn
        conn = null
        c?.stop()
    }

    private fun end(code: Int?, why: String?) {
        stop()
        if ((code == -15 || code == 130) && why.isNullOrEmpty()) return back()
        stage.value = "failed"
        said.value = why?.ifBlank { null }
            ?: lines.lastOrNull { it.lowercase().startsWith("login failed") || it.lowercase().startsWith("error") }
            ?: lines.lastOrNull()
    }

    /** Signed in: who as; the chat's claude there stopped, so its next message starts it on the new sign-in (once the
     * turn's over, if one's running: stopped, it would be killed). */
    private fun signed(who: String?) {
        stop()
        stage.value = "signed"
        said.value = who
        val chat = Mesh.open?.takeIf { it.hostId == hostId && it.sid == sid }
        if (chat != null && isWorking(chat)) chat.chrome.restartAfterTurn = true
        else Mesh.ask(hostId, Msg.Stop(session = sid, restart = true).json()) {}
    }

    /** A chat under a Claude account: its token replaced on its host, then its claude started again on it. */
    fun saveToken(token: String) {
        val a = this.token ?: return
        if (token.isBlank()) return
        stage.value = "checking"
        Mesh.ask(hostId, Msg.Accounts(what = "token", id = a.id.orEmpty(), token = token.trim()).json()) { r ->
            val e = if (r.optString("op") == Op.ERROR) Msg.Error.from(r).said else Msg.AccountsReply.from(r).error
            if (!e.isNullOrBlank()) {
                stage.value = "failed"
                said.value = e
            } else signed(a.name)
        }
    }

    /** Send again: what this phone sent for the failed turn, its text and its files, sent again. */
    fun sendAgain(chat: Chat) {
        val (text, files) = retry ?: return
        if (isWorking(chat)) return
        resent.value = true
        Mesh.send(chat, text, files, keep = false, failed = { resent.value = false })
    }

    companion object {
        const val REQ = 1 // (the sign-in's own: no request of the connection's is numbered so)
    }
}

/** The chats' sign-in cards, by their key (a "login" line's text), and each chat's own (the last made in it). */
object Logins {
    val all = mutableStateMapOf<String, Login>()
    private val ofChat = HashMap<String, String>()

    /** A chat's sign-in as its host has it (login_state, or attach's answer): said in its card, the same on every
     * head; a card made if it has none, in place of the failed turn's error (the transcript's line for it, if it's in
     * already; else the line it gives later isn't drawn: loginStandsIn). */
    fun hostSaid(chat: Chat, l: Msg.ChatLogin?) {
        val mine = ofChat["${chat.hostId}/${chat.sid}"]?.let { k -> all[k]?.takeIf { chat.lines.any { it.kind == "login" && it.text == k } } }
        if (mine != null) return mine.hostSaid(l)
        l ?: return
        val login = Login(chat.hostId, chat.sid, l, chat.chrome.turnSent, emptyList())
        add(chat, login)
        val line = Line("login", login.key)
        val at = chat.lines.indexOfLast { it.kind == "error" && needsLogin(it.text) }
        if (at >= 0 && at > chat.lines.indexOfLast { it.kind == "you" }) chat.lines[at] = line
        else {
            chat.lines.add(line)
            chat.chrome.loginStandsIn = true
        }
    }

    /** /login: its card, signing in at once. */
    fun start(chat: Chat, args: List<String>) {
        val login = Login(chat.hostId, chat.sid, null, null, args)
        add(chat, login)
        chat.lines.add(Line("login", login.key))
        if (login.token == null) login.start()
    }

    private fun add(chat: Chat, login: Login) {
        all[login.key] = login
        ofChat["${chat.hostId}/${chat.sid}"] = login.key
    }
}

/** A sign-in card (LoginCard): the warning-bordered box a permission's is, its title bold, then a line, then what
 * can be done now. */
@Composable
fun LoginCard(login: Login) {
    val c = LocalBaude.current
    val stage = login.stage.value
    val dim = c.fg.copy(alpha = c.fg.alpha * 0.6f)
    val chat = Mesh.open?.takeIf { it.sid == login.sid }
    Column(Modifier.fillMaxWidth().border(1.dp, c.warning, RoundedCornerShape(Gap.cardRadius))
        .padding(horizontal = 12.dp, vertical = 8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        val account = login.token
        val title = when {
            account != null && stage == "signed" -> Wording.Subscriptions.tokenSaved
            account != null && stage != "failed" -> Wording.Subscriptions.replaceHeading(name = account.name.orEmpty())
            else -> null
        } ?: when (stage) {
            "ask" -> login.account?.let { Wording.Subscriptions.signInHeading(name = it.name.orEmpty()) }
                ?: if (login.host.value?.expired == true) Wording.SignIn.expiredOn(host = Mesh.label(login.hostId))
                else Wording.SignIn.notSignedInOn(host = Mesh.label(login.hostId))
            "signed" -> Wording.SignIn.signedIn
            "failed" -> Wording.SignIn.failed
            "cancelled" -> Wording.SignIn.cancelled
            else -> Wording.SignIn.signingIn
        }
        SelectionContainer { Text(title, style = Type.bold, color = c.fg) }
        val url = login.url.value
        when {
            stage == "ask" -> login.why?.let { SelectionContainer { Text(loginReason(it), style = Type.body, color = dim) } }
            stage == "starting" -> Text(Wording.SignIn.starting, style = Type.body, color = dim)
            stage == "page" && url != null -> Text(buildAnnotatedString {
                val link = Wording.SignIn.openPageThere
                val (before, after) = Wording.SignIn.pageThere(link = "\u0000").split("\u0000", limit = 2).let {
                    it[0] to it.getOrElse(1) { "" }
                }
                append(before)
                withLink(LinkAnnotation.Url(url, TextLinkStyles(SpanStyle(color = c.accent,
                    textDecoration = TextDecoration.Underline)))) { append(link) }
                append(after)
            }, style = Type.body, color = c.fg)
            stage == "checking" -> Text(if (account != null) Wording.Subscriptions.checking else Wording.SignIn.checkingCode,
                style = Type.body, color = dim)
            account != null && stage == "signed" -> Text(account.name.orEmpty(), style = Type.body, color = c.fg)
            stage == "signed" -> login.said.value.let { who ->
                SelectionContainer {
                    Text(if (who != null) Wording.SignIn.asWho(who = who) else Wording.SignIn.whoUnknown,
                        style = Type.body, color = if (who != null) c.fg else dim)
                }
            }
            stage == "failed" -> login.said.value?.let { SelectionContainer { Text(it, style = Type.body, color = dim) } }
        }
        if (account != null && (stage == "ask" || stage == "failed")) {
            var token by remember(login) { mutableStateOf("") }
            SecretField(token, { token = it }, Wording.Subscriptions.tokenHint) { login.saveToken(token) }
            Button(Wording.Common.save, suggested = true) { login.saveToken(token) }
        } else if (stage == "starting" || stage == "page") {
            var code by remember(login) { mutableStateOf("") }
            // (the field a row of its own, the buttons under it: beside them, a phone's width leaves it too narrow)
            NameField(code, { code = it }, Wording.SignIn.codeHint, { login.paste(code); code = "" }, focus = false)
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Button(Wording.Common.send) { login.paste(code); code = "" }
                Button(Wording.Common.cancel) { login.cancel() }
            }
        }
        when {
            account != null && stage != "signed" -> Unit
            stage == "ask" -> Button(Wording.SignIn.signIn, suggested = true) { login.start() }
            stage == "failed" || stage == "cancelled" -> Button(Wording.Common.tryAgain, suggested = true) { login.start() }
            stage == "signed" && login.retry != null && !login.resent.value && chat != null ->
                Button(Wording.SignIn.sendAgain, suggested = true) { login.sendAgain(chat) }
        }
    }
}
