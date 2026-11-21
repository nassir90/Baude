package dev.baude.android

/** What typed is, if Baude handles it itself and never sends it (BaudeWindow.on_send's /new, /clear, /cd and the
 * rest): the ones that go even when the box couldn't send (context exhausted, its host gone). */
fun isLocal(typed: String): Boolean {
    val t = typed.trim()
    return t in setOf("/clear", "/new", "/cd", "/login", "/logout") || t.startsWith("/cd ") || t.startsWith("/login ")
}

/** A turn's error that signing in again would fix (NEEDS_LOGIN). */
private val NEEDS_LOGIN = Regex("Please run /login|Invalid API key|OAuth token (?:has )?(?:expired|revoked)|" +
    "[Nn]ot (?:logged|signed) in|authentication_error", RegexOption.IGNORE_CASE)

fun needsLogin(text: String?): Boolean = !text.isNullOrEmpty() && NEEDS_LOGIN.containsMatchIn(text)

/** A command of Baude's own in the box (a host's chat only), handled here: true if it was one (nothing is sent or
 * queued then). empty: the box emptied. A phone's chats are all another machine's, so signing in and /cd are said to
 * be there. */
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
    if (t == "/login" || t == "/logout" || t.startsWith("/login ")) {
        empty()
        chat.lines.add(Line("note", Wording.SignIn.signsInThere(host = Mesh.label(at))))
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
