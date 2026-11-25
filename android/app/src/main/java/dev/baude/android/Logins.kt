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
 * by its host process (hostlogin), on a connection of its own to it. A turn that failed for want of it (why: its
 * error) first says so, with Sign in; /login starts it at once. What it says comes as it's said: the sign-in page's
 * address, a link to open here; a code that page shows is pasted in the card and passed to it; Cancel lets go of the
 * connection, which stops it there. Once it's signed in: who as, the chat's claude there started again on it, and
 * Send again for what this phone sent for the failed turn (retry). stage: ask, starting, page, checking, signed,
 * failed, cancelled. Compose state, changed on the main thread. */
class Login(val hostId: String, val sid: String, val why: String?, val retry: Pair<String, List<Picked>>?, private val args: List<String>) {
    val key = java.util.UUID.randomUUID().toString()
    /** The chat's Claude account, for a chat run under one: a token from claude setup-token pasted in place of claude
     * auth login, replacing that account's on its host (saveToken), not the CLI's own sign-in. */
    val account = Mesh.session(hostId, sid)?.account?.takeIf { it.kind == "token" }
    val stage = mutableStateOf(if (why != null || account != null) "ask" else "starting")
    val url = mutableStateOf<String?>(null)
    val said = mutableStateOf<String?>(null) // (failed: why; signed: who as)
    val resent = mutableStateOf(false)
    private val lines = ArrayList<String>()
    private var conn: Connection? = null

    fun start() {
        conn?.stop()
        lines.clear()
        url.value = null
        said.value = null
        stage.value = "starting"
        conn = Mesh.watch(hostId, { c, m -> heard(c, m) }) { c ->
            if (c !== conn) return@watch
            if (c.state == "up") c.send(Msg.Login(req = REQ, args = args).json())
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
        if (code.isBlank() || !c.send(Msg.LoginCode(code = code.trim()).json())) return
        stage.value = "checking"
    }

    fun cancel() {
        stop()
        stage.value = "cancelled"
        said.value = null
    }

    private fun stop() {
        val c = conn
        conn = null
        c?.stop()
    }

    private fun end(code: Int?, why: String?) {
        stop()
        val cancelled = (code == -15 || code == 130) && why.isNullOrEmpty()
        stage.value = if (cancelled) "cancelled" else "failed"
        said.value = if (cancelled) null else why?.ifBlank { null }
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
        val a = account ?: return
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

/** The chats' sign-in cards, by their key (a "login" line's text). */
object Logins {
    val all = mutableStateMapOf<String, Login>()

    /** A turn that failed for want of signing in (why: its error): its card, in place of the error (the transcript's
     * line for it, if it's in already; else the line it gives later isn't drawn: loginStandsIn). */
    fun ask(chat: Chat, why: String) {
        val login = Login(chat.hostId, chat.sid, why, chat.chrome.turnSent, emptyList())
        all[login.key] = login
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
        all[login.key] = login
        chat.lines.add(Line("login", login.key))
        if (login.account == null) login.start()
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
        val account = login.account
        val title = when {
            account != null && stage == "signed" -> Wording.Subscriptions.tokenSaved
            account != null && stage != "failed" -> Wording.Subscriptions.replaceHeading(name = account.name.orEmpty())
            else -> null
        } ?: when (stage) {
            "ask" -> Wording.SignIn.notSignedInOn(host = Mesh.label(login.hostId))
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
