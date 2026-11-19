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
        MeshService.keep(this)
        val kept = savedInstanceState?.getStringArrayList("screen")
        if (kept != null && kept.isNotEmpty()) screen.value = kept // (turned, or dark mode switched: where it was)
        else {
            if (Build.VERSION.SDK_INT >= 33) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
            opened(intent)
        }
        setContent {
            BaudeTheme {
                val now = screen.value
                BackHandler(now[0] != "contacts") { back() } // (Hosts too: back to the home screen)
                when (now[0]) {
                    "chat" -> ChatShell(now[1], now[2], ::back, open = { h, s -> go(listOf("chat", h, s)) },
                        feedback = { go(listOf("feedback", now[1], now[2])) })
                    "hosts" -> HostsScreen(::back)
                    "feedback" -> FeedbackScreen(feedbackContext(now), ::back)
                    else -> Home(open = { h, s -> go(listOf("chat", h, s)) }, hosts = { go(listOf("hosts")) },
                        feedback = { go(listOf("feedback")) })
                }
                HostKeyAsk()
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        opened(intent)
    }

    /** Opened from a Bot's notification: its chat. */
    private fun opened(intent: Intent?) {
        val host = intent?.getStringExtra("host") ?: return
        val session = intent.getStringExtra("session") ?: return
        go(listOf("chat", host, session))
    }

    private fun go(to: List<String>) {
        if (screen.value[0] == "chat" && to[0] != "chat" && to[0] != "feedback") Mesh.close()
        screen.value = if (to[0] == "contacts") listOf("contacts") else to
    }

    private fun back() {
        val now = screen.value
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
        return listOfNotNull("Chat: " + (s?.optJSONObject("bot")?.optString("name")?.let { "Bot $it" }
            ?: s?.str("title") ?: "(untitled)"),
            "session: ${now[2]}", s?.optString("cwd")?.ifBlank { null }?.let { "folder: $it" }, "host: ${Mesh.label(now[1])}")
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putStringArrayList("screen", ArrayList(screen.value))
    }

    override fun onStart() {
        super.onStart()
        Mesh.onScreen(true)
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
