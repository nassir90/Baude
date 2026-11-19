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

/** The app: your Bots as contacts, a Bot's chat, your hosts. */
class MainActivity : ComponentActivity() {
    private val screen = mutableStateOf<List<String>>(listOf("contacts"))

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        Mesh.start(this)
        MeshService.keep(this)
        if (Build.VERSION.SDK_INT >= 33) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        opened(intent)
        setContent {
            BaudeTheme {
                val now = screen.value
                BackHandler(now.size > 1) { back() }
                when (now[0]) {
                    "chat" -> ChatScreen(now[1], now[2], ::back)
                    "hosts" -> HostsScreen(::back)
                    else -> Contacts(open = { go(listOf("chat", it.hostId, it.id)) }, hosts = { go(listOf("hosts")) })
                }
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
        if (screen.value[0] == "chat" && to[0] != "chat") Mesh.close()
        screen.value = if (to[0] == "contacts") listOf("contacts") else to
    }

    private fun back() {
        if (screen.value[0] == "chat") Mesh.close()
        screen.value = listOf("contacts")
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
