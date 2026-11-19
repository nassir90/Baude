package dev.baude.android

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.automirrored.filled.Redo
import androidx.compose.material.icons.automirrored.filled.Undo
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import org.json.JSONArray
import org.json.JSONObject

/** Meta, Baude's helper, for this phone: this phone can't run claude, so its agent runs on a host you pick, as a
 * session of its own there that's kept nowhere (ephemeral: its conversation is the phone's, gone once it's cleared or
 * the host's let go of it). It operates the app as desktop Meta operates Baude: its tools (MetaTools) are the phone's
 * own, an MCP server its claude runs relayed back over the phone's connection (start's app_tools), so they act here;
 * it knows the app by the light index sent with your first message, and reads files on its host with Read, Grep and
 * Glob. */
object Meta {
    val host = mutableStateOf<String?>(null)
    var chat = Chat("", "")
    var opener: (String, String) -> Unit = { _, _ -> } // (a chat's row tapped under a reply: opened, as the app opens one)
    private var started = false
    private val waiting = ArrayList<String>() // (sent before its session's made: sent once it is)
    private val relays = HashSet<Int>()      // (its tools' relays open on its host: their numbers)
    private val shown = ArrayList<Line>()    // (what its tools showed, and the actions they took, this turn: under its
                                              // reply once it's said)

    /** Shown under this turn's reply, once it ends. */
    fun under(line: Line) {
        if (chat.thinking.value) shown.add(line) else chat.lines.add(line)
    }

    private fun shownNow() {
        chat.lines.addAll(shown)
        shown.clear()
    }
    private var heard: ((String, JSONObject) -> Unit)? = null

    val SYSTEM = listOf(
        "You are Meta, the helper inside Baude for Android, a phone app for Baude, which lists the user's chats and " +
            "Bots on their hosts (machines running Baude's host process, and the members of their mesh) and shows each " +
            "chat with its pages. You operate the app for the user: you read its state, change its settings, and " +
            "organise and change what's in it, with your tools. Do what's asked; when it's ambiguous which things are " +
            "meant, say which you'd take, or highlight them, before changing anything. You run on one of the hosts, " +
            "where Read, Grep and Glob read its files.",
        "What you know of the app comes with the user's first message: a short index, a line or two per part, with " +
            "short ids. app_index opens a part in full (its rows, a filter to narrow them): look rather than guess, and " +
            "don't ask the user for what the index has.",
        "Everything you change is an action, with an id, and can be undone: the user sees each under your reply with " +
            "Undo. undo and redo take one back or put it again. A host's sessions aren't deleted from the phone: " +
            "archive them.",
        "Forms: asked to help the user start a session, bring up new_session with open_form, fill in what you know " +
            "(fill_form), and leave the rest and the submitting to them.",
        "Settings: set_setting changes one at once, and its row shows under your reply. show also lists chats as rows " +
            "the user can tap to open.",
        "Be brief and plain: a sentence or two, no headings. Don't describe what your tools did: the user sees it.",
    ).joinToString("\n\n")

    /** The light index: what this is, its settings, the hosts and each one's sessions (title, folder, how it stands,
     * its Bot), the chat open, and its parts to open. */
    fun index(): String {
        val lines = ArrayList<String>()
        lines.add("Baude for Android · ${Mesh.hosts.size} host(s) added")
        lines.add("settings: sidebar_group=${HomePrefs.by.value} sidebar_host=${HomePrefs.host.value.let { if (it == "all") "all" else Mesh.label(it) }}")
        for ((id, label) in (Mesh.hosts.map { it.id to it.label } + Mesh.members.keys.map { "mesh:$it" to Mesh.label("mesh:$it") })) {
            val conn = Mesh.conn(id)
            lines.add("host $label: ${conn?.state ?: "not connected"}")
            for (s in Mesh.sessions[id].orEmpty().filter { !it.optBoolean("draft") }.take(60)) {
                val bot = s.optJSONObject("bot")?.str("name")
                lines.add("  ${s.optString("id").take(8)} " + listOfNotNull(
                    (s.str("title") ?: "(untitled)").let { "\"$it\"" }, shortPath(id, s.optString("cwd")),
                    s.optString("state"), bot?.let { "Bot $it" }, groupOf(id, s).ifEmpty { null }?.let { "group $it" },
                    if (s.optBoolean("archived")) "archived" else null, s.str("model")).joinToString(" · "))
            }
        }
        Mesh.open?.let { lines.add("open: ${it.sid.take(8)} on ${Mesh.label(it.hostId)}") }
        lines.add("parts (app_index): ${MetaTools.PARTS}")
        return lines.joinToString("\n")
    }

    /** What you asked sent: a new session for Meta on its host the first time (the index with it), else the next
     * message to it. */
    fun send(text: String) {
        val h = host.value ?: return
        val c = chat
        c.lines.add(Line("you", text))
        MetaTools.highlight.clear() // (highlighted till the user's next message)
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
                .put("app_tools", JSONArray(listOf(MetaTools.NAME)))
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
            if (at != h) return@l
            when (msg.optString("op")) { // (its tools: their lines served here, answered back to the host)
                "mcp_open" -> if (msg.optString("name") == MetaTools.NAME && (msg.optString("session") == c.sid || c.sid.isEmpty()))
                    relays.add(msg.optInt("conn"))
                "mcp" -> if (msg.optInt("conn") in relays) {
                    val n = msg.optInt("conn")
                    MetaTools.line(msg.optString("line")) { answer ->
                        if (answer != null) Mesh.conn(h)?.send(JSONObject().put("op", "mcp").put("conn", n).put("line", answer))
                    }
                }
                "mcp_close" -> relays.remove(msg.optInt("conn"))
            }
            if (msg.optString("session") != c.sid || c.sid.isEmpty()) return@l
            when (msg.optString("op")) {
                "exited" -> { c.thinking.value = false; shownNow() }
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
                            shownNow()
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
        relays.clear()
        shown.clear()
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
    MetaTools.form.value?.let { f -> // (a form Meta brought up, as it's filled: the user submits it)
        androidx.compose.runtime.key(MetaTools.formVersion.value) {
            NewSession(null, null, done = { MetaTools.form.value = null }, opened = { h, s -> Meta.opener(h, s) },
                given = f.optJSONObject("values"))
        }
    }
}

/** What Meta showed under a reply: chats (a row each, tapped to open), or a setting's row (its choices). */
@Composable
fun MetaRows(text: String) {
    val c = LocalBaude.current
    val o = try { JSONObject(text) } catch (_: Exception) { return }
    Mesh.tick.value
    o.optJSONArray("chats")?.let { rows ->
        Column(Modifier.fillMaxWidth().clip(androidx.compose.foundation.shape.RoundedCornerShape(12.dp)).background(c.card)
            .border(1.dp, c.rule, androidx.compose.foundation.shape.RoundedCornerShape(12.dp))) {
            for (i in 0 until rows.length()) {
                val r = rows.optJSONArray(i) ?: continue
                val (h, sid) = r.optString(0) to r.optString(1)
                val s = Mesh.session(h, sid)
                if (i > 0) androidx.compose.foundation.layout.Box(Modifier.fillMaxWidth().height(1.dp).background(c.rule))
                ActionRow(s?.optJSONObject("bot")?.str("name") ?: s?.str("title") ?: "(untitled)",
                    listOfNotNull(Mesh.label(h), s?.optString("cwd")?.let { shortPath(h, it) }).joinToString(" · "),
                    onClick = { Meta.opener(h, sid) },
                    suffix = { androidx.compose.material3.Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight,
                        null, tint = c.dim) })
            }
        }
    }
    when (o.optString("setting")) {
        "sidebar_group" -> Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Group the home screen by", style = Type.body, color = c.fg, modifier = Modifier.weight(1f))
            Picker(if (HomePrefs.by.value == "date") "Date" else "Folder", "Group by",
                listOf("Folder" to "folder", "Date" to "date"), HomePrefs.by.value) { HomePrefs.by.value = it; HomePrefs.save() }
        }
        "sidebar_host" -> Row(verticalAlignment = Alignment.CenterVertically) {
            Text("The home screen shows", style = Type.body, color = c.fg, modifier = Modifier.weight(1f))
            val up = Mesh.reached()
            Picker(HomePrefs.host.value.let { if (it == "all") "All hosts" else Mesh.label(it) }, "Hosts shown",
                listOf("All hosts" to "all") + up.map { it.second to it.first }, HomePrefs.host.value) {
                HomePrefs.host.value = it; HomePrefs.save()
            }
        }
    }
}

/** One of Meta's changes under its reply: what it did, Undo (or Redo, once it's undone). */
@Composable
fun MetaAction(id: String) {
    val c = LocalBaude.current
    val a = MetaTools.actions.firstOrNull { it.id == id } ?: return
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(a.said, style = Type.caption, color = if (a.undone.value) c.dim else c.fg, modifier = Modifier.weight(1f))
        if (a.undone.value) IconButton(Icons.AutoMirrored.Filled.Redo, "Redo") { a.redo(); a.undone.value = false }
        else IconButton(Icons.AutoMirrored.Filled.Undo, "Undo") { a.undo(); a.undone.value = true }
    }
}
