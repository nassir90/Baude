package dev.baude.android

import android.Manifest
import android.content.Intent
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.mutableStateOf

/** The app: the home screen (Baude's sidebar: your chats and Bots on every host), a chat with its pages, your hosts,
 * Send Feedback. */
class MainActivity : ComponentActivity() {
    private val screen = mutableStateOf<List<String>>(listOf("contacts"))

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        Mesh.start(this)
        HomePrefs.load(this)
        Outbox.start() // (feedback not filed yet: tried again)
        Meta.restore() // (its conversation and journal, as they were kept; its session attached again once its host's up)
        Meta.go = { b ->
            when (b) {
                "send_feedback" -> go(listOf("feedback"))
                "hosts" -> go(listOf("hosts"))
                "home" -> go(listOf("contacts"))
            }
        }
        MeshService.keep(this)
        Update.start(this) // (a newer build on the Baude site: downloaded and offered, Update at the home screen's foot)
        val kept = savedInstanceState?.getStringArrayList("screen")
        if (kept != null && kept.isNotEmpty()) screen.value = kept // (turned, or dark mode switched: where it was)
        else {
            if (Build.VERSION.SDK_INT >= 33) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
            opened(intent)
        }
        Shares.restore(this, savedInstanceState, screen.value) // (made again after the app went: what was shared)
        setContent {
            BaudeTheme {
                val now = screen.value
                // (Hosts too: back to the home screen; something shared, not sent: back to the app it's from)
                BackHandler(now[0] != "contacts" || Shares.pending.value != null) { back() }
                when (now[0]) {
                    "chat" -> androidx.compose.runtime.key(now[1], now[2]) { // (another chat: its own pages, from Chat)
                        ChatShell(now[1], now[2], ::back, open = { h, s -> go(listOf("chat", h, s)) },
                            feedback = { go(listOf("feedback", now[1], now[2])) })
                    }
                    "hosts" -> HostsScreen(::back)
                    "phone" -> PhoneScreen(::back, open = { go(listOf("chat", "phone", it)) }, make = { go(listOf("new-agent")) })
                    "new-agent" -> NewAgentScreen({ go(listOf("phone")) }) { go(listOf("chat", "phone", it)) }
                    "feedback" -> FeedbackScreen(feedbackContext(now), ::back)
                    else -> Home(open = { h, s -> go(listOf("chat", h, s)) }, hosts = { go(listOf("hosts")) },
                        feedback = { go(listOf("feedback")) }, phone = { go(listOf("phone")) },
                        note = { if (Shares.pending.value != null) ShareBanner(::back) })
                }
                HostKeyAsk()
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        opened(intent)
    }

    /** Opened from a Bot's notification or shortcut: its chat; from A new version is ready's Update: the install.
     * Shared to from another app: to a Bot (its shortcut in the share sheet), its chat with what's shared in it; to
     * Baude, the home screen to choose the chat. */
    private fun opened(intent: Intent?) {
        if (intent?.getBooleanExtra("update", false) == true) {
            intent.removeExtra("update")
            Update.install(this)
        }
        intent?.let(Shares::from)?.let { (shared, to) ->
            setIntent(Intent(this, MainActivity::class.java)) // (made again, as it's turned: not shared again)
            Shares.pending.value = shared
            go(if (to != null) listOf("chat", to.first, to.second) else listOf("contacts"))
            return
        }
        val host = intent?.getStringExtra("host") ?: return
        val session = intent.getStringExtra("session") ?: return
        go(listOf("chat", host, session))
    }

    private fun go(to: List<String>) {
        if (screen.value[0] == "chat" && to[0] != "chat" && to[0] != "feedback") Mesh.close()
        screen.value = if (to[0] == "contacts") listOf("contacts") else to
        if (to[0] != "feedback") Shares.returns = false // (Send Feedback from it, and back: still the shared one)
        val shared = Shares.pending.value
        if (to[0] == "chat" && shared != null) { // (the chat chosen for what's shared)
            Shares.pending.value = null
            Shares.put(this, Mesh.openChat(to[1], to[2]), shared)
            Shares.returns = true
        }
    }

    private fun back() {
        val now = screen.value
        if (now[0] == "contacts" && Shares.pending.value != null || now[0] == "chat" && Shares.returns) {
            if (now[0] == "chat") Mesh.close()
            Shares.pending.value = null
            Shares.returns = false
            screen.value = listOf("contacts")
            moveTaskToBack(true) // (back to the app it was shared from, as a messaging app goes)
            return
        }
        if (now[0] == "feedback" && now.size == 3) { // (back to the chat it was sent from)
            screen.value = listOf("chat", now[1], now[2])
            return
        }
        if (now[0] == "chat") Mesh.close()
        screen.value = listOf("contacts")
    }

    /** What Send Feedback says it's about: the screen, and a chat's session, folder and host. */
    private fun feedbackContext(now: List<String>): List<String> {
        if (now.size < 3) return listOf("Home screen")
        val s = Mesh.session(now[1], now[2])
        return listOfNotNull("Chat: " + (s?.bot?.let { "Bot ${it.name.orEmpty()}" }
            ?: s?.title?.ifBlank { null } ?: Wording.Common.untitled),
            "session: ${now[2]}", s?.cwd?.ifBlank { null }?.let { "folder: $it" }, "host: ${Mesh.label(now[1])}")
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putStringArrayList("screen", ArrayList(screen.value))
        Shares.save(this, outState)
    }

    override fun onStart() {
        super.onStart()
        Mesh.onScreen(true)
        Update.resumed(this)
    }

    override fun onStop() {
        super.onStop()
        Mesh.onScreen(false)
    }

    override fun onResume() {
        super.onResume()
        Mesh.shown = true
    }

    override fun onPause() {
        super.onPause()
        Mesh.shown = false
    }
}
