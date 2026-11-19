package dev.baude.android

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import org.json.JSONArray
import org.json.JSONObject

/** Meta, Baude's helper, for this phone: this phone can't run claude, so its agent runs on a host you pick, as a
 * session of its own there that's kept nowhere (ephemeral: its conversation is the phone's, gone once it's cleared or
 * the host's let go of it). It reads with Read, Grep and Glob there, and knows the app by the light index sent with
 * your first message (your hosts and their sessions); it has none of the desktop's app tools, so it says what to do
 * rather than doing it. */
object Meta {
    val host = mutableStateOf<String?>(null)
    var chat = Chat("", "")
    private var started = false
    private val waiting = ArrayList<String>() // (sent before its session's made: sent once it is)
    private var heard: ((String, JSONObject) -> Unit)? = null

    val SYSTEM = listOf(
        "You are Meta, the helper inside Baude for Android, a phone app for Baude: it lists the user's chats " +
            "and Bots on their hosts (machines running Baude's host process, over ssh, and the members of their " +
            "mesh), and shows each chat with its pages (Memory, Browser and Routines for a Bot, Subagents, " +
            "Processes, Artifacts). You run on one of those hosts, not on the phone: you can read files there " +
            "with Read, Grep and Glob, and nothing else. You can't change the app or its chats yourself: say " +
            "where in the app to do it (the home screen's New session, a chat's model and permission mode under " +
            "its title, ⋮ for Send Feedback…, Hosts for adding a host).",
        "What you know of the app comes with the user's first message: a short index, a line a session, with " +
            "short ids. Take what's there as settled rather than asking for it.",
        "Be brief and plain: a sentence or two, no headings.",
    ).joinToString("\n\n")

    /** The light index: the hosts, each one's sessions (title, folder, how it stands, its Bot), the chat open. */
    fun index(): String {
        val lines = ArrayList<String>()
        lines.add("Baude for Android · ${Mesh.hosts.size} host(s) added")
        for ((id, label) in (Mesh.hosts.map { it.id to it.label } + Mesh.members.keys.map { "mesh:$it" to Mesh.label("mesh:$it") })) {
            val conn = Mesh.conn(id)
            lines.add("host $label: ${conn?.state ?: "not connected"}")
            for (s in Mesh.sessions[id].orEmpty().filter { !it.optBoolean("draft") }.take(60)) {
                val bot = s.optJSONObject("bot")?.optString("name")
                lines.add("  ${s.optString("id").take(8)} " + listOfNotNull(
                    (s.str("title") ?: "(untitled)").let { "\"$it\"" }, shortPath(id, s.optString("cwd")),
                    s.optString("state"), bot?.let { "Bot $it" }, if (s.optBoolean("archived")) "archived" else null,
                    s.str("model")).joinToString(" · "))
            }
        }
        Mesh.open?.let { lines.add("open: ${it.sid.take(8)} on ${Mesh.label(it.hostId)}") }
        return lines.joinToString("\n")
    }

    /** What you asked sent: a new session for Meta on its host the first time (the index with it), else the next
     * message to it. */
    fun send(text: String) {
        val h = host.value ?: return
        val c = chat
        c.lines.add(Line("you", text))
        c.thinking.value = true
        c.said.value = null
        if (!started || c.hostId != h) {
            started = true
            c.hostId = h
            listen(h)
            val haiku = models(h).firstOrNull { it.value == "haiku" || it.name.lowercase().startsWith("haiku") }
            val msg = JSONObject().put("op", "start").put("prompt", index() + "\n\n" + text).put("cwd", "~")
                .put("mode", "default").put("ephemeral", true).put("title", "Meta").put("system", SYSTEM)
                .put("allowed_tools", JSONArray(listOf("Read", "Grep", "Glob"))).put("attach", true)
            haiku?.let { msg.put("model", it.value) }
            Mesh.ask(h, msg) { r ->
                val id = r.optJSONObject("session")?.optString("id")
                if (r.optString("op") == "started" && id != null) {
                    c.sid = id
                    waiting.toList().also { waiting.clear() }.forEach { input(c, h, it) }
                } else {
                    waiting.clear()
                    started = false
                    c.thinking.value = false
                    c.said.value = "Couldn't start Meta on ${Mesh.label(h)}: " + r.optString("error")
                }
            }
            return
        }
        if (c.sid.isEmpty()) waiting.add(text) else input(c, h, text)
    }

    private fun input(c: Chat, h: String, text: String) {
        val msg = JSONObject().put("type", "user").put("message", JSONObject().put("role", "user").put("content", text))
        Mesh.ask(h, JSONObject().put("op", "input").put("session", c.sid).put("msg", msg)) { r ->
            if (r.optString("op") == "error") {
                c.thinking.value = false
                c.said.value = "Couldn't send: " + r.optString("error")
            }
        }
    }

    /** What Meta's claude says, live: its text and tool calls as the chat's lines, what it asks, its turn's end. */
    private fun listen(h: String) {
        heard?.let { Mesh.listeners.remove(it) }
        val l: (String, JSONObject) -> Unit = l@{ at, msg ->
            val c = chat
            if (at != h || msg.optString("session") != c.sid || c.sid.isEmpty()) return@l
            when (msg.optString("op")) {
                "exited" -> c.thinking.value = false
                "claude" -> {
                    val m = msg.optJSONObject("msg") ?: return@l
                    Ask.of(m)?.let { a -> if (c.asks.none { it.rid == a.rid }) c.asks.add(a) }
                    when (m.optString("type")) {
                        "assistant" -> if (m.isNull("parent_tool_use_id")) for (b in m.optJSONObject("message")
                            ?.optJSONArray("content").list()) when (b.optString("type")) {
                            "text" -> b.optString("text").trim().ifBlank { null }?.let { c.lines.add(Line("bot", it)) }
                            "tool_use" -> c.lines.add(Line("tool", toolLine(b)))
                        }
                        "result" -> {
                            c.thinking.value = false
                            if (m.optBoolean("is_error")) c.lines.add(Line("error", m.optString("result").ifBlank { "Meta couldn't finish" }))
                        }
                    }
                }
            }
        }
        heard = l
        Mesh.listeners.add(l)
    }

    /** A new conversation: the one there let go of (its host drops it, as nobody's attached). */
    fun clear() {
        val c = chat
        if (started && c.sid.isNotEmpty()) {
            Mesh.conn(c.hostId)?.send(JSONObject().put("op", "stop").put("session", c.sid))
            Mesh.conn(c.hostId)?.send(JSONObject().put("op", "detach").put("session", c.sid))
        }
        started = false
        waiting.clear()
        chat = Chat(host.value ?: "", "").also { it.sender = { t, _ -> send(t) } }
        version.value++
    }

    val version = mutableStateOf(0) // (a new conversation: drawn anew)

    init {
        chat.sender = { t, _ -> send(t) }
    }
}

/** Meta's page: the host it runs on, its conversation (as a chat's), Clear. */
@Composable
fun MetaPage(chatHost: String, here: Boolean) {
    val c = LocalBaude.current
    Mesh.tick.value
    Meta.version.value
    val up = Mesh.reached()
    androidx.compose.runtime.LaunchedEffect(up, chatHost) {
        if (Meta.host.value == null || up.none { it.first == Meta.host.value })
            (up.firstOrNull { it.first == chatHost } ?: up.firstOrNull())?.let { Meta.host.value = it.first }
    }
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(start = 10.dp, end = 6.dp, top = 2.dp, bottom = 2.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(2.dp)) {
            Text("Runs on", style = Type.caption, color = c.dim)
            val h = Meta.host.value
            Picker(h?.let { Mesh.label(it) } ?: "No host connected", "Where Meta runs", up.map { it.second to it.first }, h,
                enabled = up.isNotEmpty()) {
                if (it != Meta.host.value) {
                    Meta.host.value = it
                    Meta.clear()
                }
            }
            Spacer(Modifier.weight(1f))
            if (Meta.chat.lines.isNotEmpty()) Button("Clear") { Meta.clear() }
        }
        Column(Modifier.weight(1f)) {
            ChatPage(Meta.chat, "Meta", hint = "Ask Meta", attach = false)
        }
    }
}
