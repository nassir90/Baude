package dev.baude.android

/** What typed is, if Baude handles it itself and never sends it (BaudeWindow.on_send's /new, /clear, /cd and the
 * rest): the ones that go even when the box couldn't send (context exhausted, its host gone). */
fun isLocal(typed: String): Boolean {
    val t = typed.trim()
    return t in setOf("/clear", "/new", "/cd", "/login", "/logout") || t.startsWith("/cd ") || t.startsWith("/login ")
}

/** A turn's error that signing in again would fix (hostlogin.NEEDS_LOGIN, as it is there): claude's own words for it
 * ("Invalid API key · Please run /login", "OAuth token has expired", "Failed to authenticate: this login reached its
 * time limit and cannot be renewed. Run `claude auth login` to sign in again…", "Not logged in"), or the API's. */
private val NEEDS_LOGIN = Regex("Please run /login|\\brun /login\\b|claude (?:auth )?login|claude setup-token|Invalid API key|" +
    "OAuth token (?:has )?(?:expired|revoked)|\\b(?:not|n't) (?:logged|signed) in|authentication_(?:error|failed)|" +
    "Failed to authenticate|API Error: 401\\b|" +
    "\\b(?:login|sign-in|session|token|credentials?)\\b[^.\\n]{0,40}\\b(?:expired|time limit|cannot be renewed|revoked)|" +
    "\\b(?:log|sign) in again", RegexOption.IGNORE_CASE)

fun needsLogin(text: String?): Boolean = !text.isNullOrEmpty() && NEEDS_LOGIN.containsMatchIn(text)

/** A sign-in error as its card says why (hostlogin.reason): its first sentence. */
fun loginReason(text: String): String =
    AUTH_ESCAPES.replace(text, "").trim().split(Regex("(?<=[.!?])\\s+"), limit = 2)[0].take(200)

/** A terminal's link and colour codes, in what claude auth login writes (chatinput's _AUTH_ESCAPES). */
val AUTH_ESCAPES = Regex("\u001b\\][^\u0007\u001b]*(?:\u0007|\u001b\\\\)|\u001b\\[[0-9;?]*[A-Za-z]")

/** A command of Baude's own in the box (a host's chat only), handled here: true if it was one (nothing is sent or
 * queued then). empty: the box emptied. A phone's chats are all another machine's: /login signs in there (its card),
 * /logout and /cd are said to be there. */
fun localCommand(chat: Chat, typed: String, working: Boolean, empty: () -> Unit, openPage: (String) -> Unit,
                 open: (String, String) -> Unit): Boolean {
    if (chat.sender != null) return false
    val t = typed.trim()
    val at = chat.hostId
    if (t == "/btw" || t.startsWith("/btw ")) { // (even mid-turn: it doesn't interrupt)
        val question = t.removePrefix("/btw").trim()
        if (question.isNotEmpty()) {
            empty()
            askSide(chat, question)
            openPage("btw")
        }
        return true
    }
    if (t == "/login" || t.startsWith("/login ")) { // (claude auth login, on the chat's host: its card)
        empty()
        Logins.start(chat, t.removePrefix("/login").trim().split(Regex("\\s+")).filter { it.isNotEmpty() })
        return true
    }
    if (t == "/logout") {
        empty()
        chat.lines.add(Line("note", Wording.SignIn.signsOutThere(host = Mesh.label(at))))
        return true
    }
    if (t == "/new" || t == "/clear") {
        if (working) interrupt(chat) else {
            empty()
            newChatOn(chat, open, Mesh.session(at, chat.sid)?.cwd?.ifBlank { null })
        }
        return true
    }
    if (t == "/cd" || t.startsWith("/cd ")) {
        if (working) interrupt(chat) else {
            empty()
            chat.lines.add(Line("note", Wording.SlashCommands.cdElsewhere(host = Mesh.label(at))))
        }
        return true
    }
    return false
}
