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
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import org.json.JSONArray
import org.json.JSONObject

/** Meta, Baude's helper, for this phone: this phone can't run claude, so its agent runs on a host you pick, as a
 * session of its own there that's kept nowhere (ephemeral), waiting for the phone a long while (start's grace) when
 * it's away, and attached again, its tools with it, when it's back. It operates the app as desktop Meta operates
 * Baude: its tools (MetaTools) are the phone's own, an MCP server its claude runs relayed back over the phone's
 * connection (start's app_tools), so they act here; it knows the app by the light index sent with your first message.
 * Its conversation and its journal are kept on this phone (meta.json, as desktop's meta.json keeps them): a session
 * that's gone meanwhile (its host restarted) is started again with what was said before, as desktop Meta's is. */
object Meta {
    val host = mutableStateOf<String?>(null)
    var chat = Chat("", "")
    var opener: (String, String) -> Unit = { _, _ -> } // (a chat's row tapped under a reply: opened)
    var pageOpener: (String) -> Unit = {}               // (a page's row: shown, in the chat open)
    var pages: () -> List<Page> = { emptyList() }      // (the chat open's pages)
    var go: (String) -> Unit = {}                       // (a button's screen: send_feedback, hosts, home)
    private var started = false
    private val waiting = ArrayList<String>() // (sent before its session's made: sent once it is)
    private val relays = HashSet<Int>()      // (its tools' relays open on its host: their numbers)
    private var heard: ((String, JSONObject) -> Unit)? = null
    private val shown = ArrayList<Line>()    // (what its tools showed, and the actions they took, this turn: under its
                                              // reply once it's said)
    private var loaded = false
    private val running = HashMap<String, String>() // (its tools' calls the phone's running: "relay/id" -> what they are)
    val late = ArrayList<String>() // (calls that ran here after its host had told it they failed: said with the next
                                   // message, so it doesn't do them again)
    const val GRACE_S = 6 * 3600 // (how long its session waits on its host for the phone to come back)

    /** Shown under this turn's reply, once it ends. */
    fun under(line: Line) {
        if (chat.thinking.value) shown.add(line) else chat.lines.add(line)
    }

    private fun shownNow() {
        chat.lines.addAll(shown)
        shown.clear()
        save()
    }

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
        "Forms: asked to help the user add a host, make a group or start an agent, bring up its form with open_form " +
            "(app_index forms), fill in what you know " +
            "(fill_form), and leave the rest and the submitting to them.",
        "Settings: set_setting changes one at once, and its row shows under your reply. show also lists chats or the " +
            "open chat's pages as rows the user can tap, the changelog, and buttons for what you don't do yourself " +
            "(app_index buttons).",
        "Be brief and plain: a sentence or two, no headings. Don't describe what your tools did: the user sees it.",
    ).joinToString("\n\n")

    /** The light index: what this is, its settings, the hosts and each one's sessions (title, folder, how it stands,
     * its Bot), the chat open, and its parts to open. */
    fun index(): String {
        val lines = ArrayList<String>()
        lines.add("Baude for Android · ${Mesh.hosts.size} host(s) added")
        lines.add("settings: sidebar_group=${HomePrefs.by.value} sidebar_host=${HomePrefs.host.value.let { if (it == "all") "all" else Mesh.label(it) }}")
        for ((id, label) in (Mesh.hosts.map { it.id to it.label } + Mesh.members.keys.map { "mesh:$it" to Mesh.label("mesh:$it") } +
                listOf("phone" to Mesh.label("phone")))) { // (this phone's own agents too)
            val conn = Mesh.conn(id)
            lines.add("host $label: ${conn?.state ?: "not connected"}")
            for (s in Mesh.sessions[id].orEmpty().filter { it.draft != true }.take(60)) {
                val bot = s.bot?.name?.ifBlank { null }
                lines.add("  ${s.id.orEmpty().take(8)} " + listOfNotNull(
                    (s.title?.ifBlank { null } ?: "(untitled)").let { "\"$it\"" }, shortPath(id, s.cwd.orEmpty()),
                    s.state.orEmpty(), bot?.let { "Bot $it" }, groupOf(id, s).ifEmpty { null }?.let { "group $it" },
                    if (s.archived == true) "archived" else null, s.model?.ifBlank { null }).joinToString(" · "))
            }
        }
        Mesh.open?.let { lines.add("open: ${it.sid.take(8)} on ${Mesh.label(it.hostId)}") }
        lines.add("parts (app_index): ${MetaTools.PARTS}")
        return lines.joinToString("\n")
    }

    // ---- kept on the phone

    private fun file() = java.io.File(Mesh.app.filesDir, "meta.json")

    /** Its conversation, its session's place, its journal and where its form is, written out (after each change). */
    fun save() {
        if (!loaded) return
        val o = JSONObject().put("host", host.value).put("sid", chat.sid).put("started", started)
            .put("lines", JSONArray(chat.lines.map { JSONArray(listOf(it.kind, it.text)) }))
            .put("actions", JSONArray(MetaTools.actions.map { it.json() })).put("next", MetaTools.next).put("turn", MetaTools.turn)
            .put("late", JSONArray(late))
        try {
            file().writeText(o.toString())
        } catch (_: Exception) {
        }
    }

    /** (the app started) What was kept read back, and its session attached again once its host is up. */
    fun restore() {
        if (loaded) return
        loaded = true
        val o = try {
            JSONObject(file().readText())
        } catch (_: Exception) {
            return
        }
        host.value = o.str("host")
        chat = Chat(host.value ?: "", o.optString("sid")).also { it.sender = { t, _ -> send(t) } }
        for (l in o.optJSONArray("lines").let { a -> (0 until (a?.length() ?: 0)).mapNotNull { a!!.optJSONArray(it) } })
            chat.lines.add(Line(l.optString(0), l.optString(1)))
        MetaTools.actions.addAll(o.optJSONArray("actions").list().map(Action::of))
        MetaTools.next = o.optInt("next", 1)
        MetaTools.turn = o.optInt("turn", 0)
        late.addAll(o.optJSONArray("late").strings())
        started = o.optBoolean("started") && chat.sid.isNotEmpty()
        host.value?.let { h ->
            listen(h)
            if (started && Mesh.conn(h)?.state == "up") reattach(h)
        }
    }

    /** Its session attached again (its host back, or the app started again): its tools carried to this connection, how
     * it stands now taken; gone meanwhile, the next message starts another with what was said before. */
    private fun reattach(h: String) {
        val c = chat
        if (!started || c.sid.isEmpty()) return
        val was = c.thinking.value
        Mesh.ask(h, Msg.Attach(session = c.sid, live = true, answers = true, appTools = true).json()) { r ->
            if (chat !== c) return@ask
            if (r.optString("op") != Op.ATTACHED) {
                started = false
                c.sid = ""
                c.thinking.value = false
                if (was) c.lines.add(Line("note", "Meta's session on ${Mesh.label(h)} ended: your next message starts it again"))
                save()
                return@ask
            }
            val got = Msg.Attached.from(r)
            val busy = got.session?.state in setOf("working", "waiting")
            c.thinking.value = busy
            c.asks.clear()
            c.asks.addAll(got.asks.orEmpty().mapNotNull(Ask::of))
            if (was && !busy) {
                c.lines.add(Line("note", "Meta's reply came while the phone was away: it isn't shown here"))
                shownNow()
            }
        }
    }

    /** A call of Meta's that ran on the phone after its host had told Meta it failed: kept, to be said with the next
     * message. */
    private fun ranLate(what: String, answer: String) {
        val said = try {
            JSONObject(answer).optJSONObject("result")?.optJSONArray("content").list().joinToString(" ") { it.optString("text") }
        } catch (_: Exception) { "" }
        late.add("$what: ${said.take(300)}")
        save()
    }

    /** What was said before, for a session started again (desktop's earlier_said): its last part. */
    private fun earlier(): String {
        val said = chat.lines.filter { it.kind == "you" || it.kind == "bot" }
            .joinToString("\n") { (if (it.kind == "you") "[the user] " else "[you] ") + it.text }
        return if (said.isEmpty()) "" else "Earlier in this conversation (your session for it ended, so it's said again):\n" +
            said.takeLast(12_000) + "\n\n"
    }

    /** What you asked sent: a new session for Meta on its host the first time (the index with it, and what was said
     * before), else the next message to it. */
    fun send(text: String) {
        val h = host.value ?: return
        val c = chat
        val before = earlier()
        val ran = if (late.isEmpty()) "" else "(While the phone was away, these calls of yours did run here after all, " +
            "though you were told they failed; they're done, so don't do them again:\n" + late.joinToString("\n") { "- $it" } + ")\n\n"
        late.clear()
        c.lines.add(Line("you", text))
        MetaTools.turn++
        MetaTools.highlight.clear() // (highlighted till the user's next message)
        c.thinking.value = true
        c.said.value = null
        save()
        if (!started || c.hostId != h) {
            started = true
            c.hostId = h
            listen(h)
            val haiku = models(h).firstOrNull { it.value == "haiku" || it.name.lowercase().startsWith("haiku") }
            val msg = Msg.Start(prompt = index() + "\n\n" + before + ran + text, cwd = "~", mode = "default",
                ephemeral = true, grace = GRACE_S, title = "Meta", system = SYSTEM,
                allowedTools = listOf("Read", "Grep", "Glob"), attach = true, appTools = listOf(MetaTools.NAME)).json()
            haiku?.let { msg.put("model", it.value) }
            Mesh.ask(h, msg) { r ->
                val id = Msg.Started.from(r).session?.id
                if (r.optString("op") == Op.STARTED && id != null) {
                    c.sid = id
                    save()
                    waiting.toList().also { waiting.clear() }.forEach { input(c, h, it) }
                } else {
                    waiting.clear()
                    started = false
                    c.thinking.value = false
                    c.said.value = "Couldn't start Meta on ${Mesh.label(h)}: " + Msg.Error.from(r).said.orEmpty()
                }
            }
            return
        }
        if (c.sid.isEmpty()) waiting.add(ran + text) else input(c, h, ran + text)
    }

    private fun input(c: Chat, h: String, text: String) {
        val msg = JSONObject().put("type", "user").put("message", JSONObject().put("role", "user").put("content", text))
        Mesh.ask(h, Msg.Input(session = c.sid, msg = msg).json()) { r ->
            if (r.optString("op") == Op.ERROR) {
                val e = Msg.Error.from(r)
                c.thinking.value = false
                c.said.value = "Couldn't send: " + e.said.orEmpty()
                if (e.error.orEmpty().startsWith("no session")) { started = false; c.sid = ""; save() }
            }
        }
    }

    /** What Meta's host says: its tools' lines (served here), and its claude's text, tool calls, asks and turn's end;
     * the host back, its session attached again. */
    private fun listen(h: String) {
        heard?.let { Mesh.listeners.remove(it) }
        val l: (String, JSONObject) -> Unit = l@{ at, msg ->
            val c = chat
            if (at != h) return@l
            val op = msg.optString("op")
            when (op) {
                "connected" -> return@l reattach(h)
                Op.MCP_OPEN -> Msg.McpOpen.from(msg).let { o ->
                    if (o.name == MetaTools.NAME && (o.session.orEmpty() == c.sid || c.sid.isEmpty())) relays.add(o.conn?.toInt() ?: 0)
                }
                Op.MCP -> Msg.Mcp.from(msg).takeIf { (it.conn?.toInt() ?: 0) in relays }?.let { o ->
                    val n = o.conn?.toInt() ?: 0
                    val line = o.line.orEmpty()
                    val call = try { JSONObject(line) } catch (_: Exception) { JSONObject() }
                    val what = call.optJSONObject("params")?.let { p -> "${p.optString("name")} ${p.optJSONObject("arguments") ?: "{}"}" }
                    val key = "$n/${call.opt("id")}"
                    if (call.optString("method") == "tools/call" && what != null) running[key] = what
                    MetaTools.line(line) { answer ->
                        if (answer == null) return@line
                        val sent = Mesh.conn(h)?.takeIf { it.state == "up" }?.send(Msg.Mcp(conn = n,
                            line = answer).json()) == true
                        val was = running.remove(key)
                        if (!sent && was != null) ranLate(was, answer) // (its host gone meanwhile: it failed it there)
                    }
                }
                Op.MCP_LATE -> { // (it ran here, but its host had failed it to Meta already: Meta's told it did run)
                    val o = Msg.McpLate.from(msg)
                    val r = try { JSONObject(o.line.orEmpty()) } catch (_: Exception) { JSONObject() }
                    running.remove("${o.conn?.toInt() ?: 0}/${r.opt("id")}")?.let { ranLate(it, o.line.orEmpty()) }
                }
                Op.MCP_CLOSE -> relays.remove(Msg.McpClose.from(msg).conn?.toInt() ?: 0)
            }
            val session = when (op) {
                Op.EXITED -> Msg.Exited.from(msg).session
                Op.CLAUDE -> Msg.Claude.from(msg).session
                else -> return@l
            }
            if (session.orEmpty() != c.sid || c.sid.isEmpty()) return@l
            when (op) {
                Op.EXITED -> { c.thinking.value = false; shownNow() }
                Op.CLAUDE -> {
                    val m = Msg.Claude.from(msg).msg ?: return@l
                    Ask.of(m)?.let { a -> if (c.asks.none { it.rid == a.rid }) c.asks.add(a) }
                    when (m.optString("type")) {
                        "assistant" -> if (m.isNull("parent_tool_use_id")) for (b in m.optJSONObject("message")
                            ?.optJSONArray("content").list()) when (b.optString("type")) {
                            "text" -> b.optString("text").trim().ifBlank { null }?.let { c.lines.add(Line("bot", it)) }
                            "tool_use" -> c.lines.add(Line("tool", toolLine(b)))
                        }
                        "result" -> {
                            c.thinking.value = false
                            if (m.optBoolean("is_error")) c.lines.add(Line("error", m.optString("result").ifBlank { Wording.Meta.claudeError }))
                            shownNow()
                        }
                    }
                }
            }
        }
        heard = l
        Mesh.listeners.add(l)
    }

    /** A new conversation: the one there let go of, the journal with it, nothing kept. */
    fun clear() {
        val c = chat
        if (started && c.sid.isNotEmpty()) {
            Mesh.conn(c.hostId)?.send(Msg.Stop(session = c.sid).json())
            Mesh.conn(c.hostId)?.send(Msg.Detach(session = c.sid).json())
        }
        started = false
        waiting.clear()
        relays.clear()
        shown.clear()
        MetaTools.actions.clear()
        MetaTools.highlight.clear()
        MetaTools.form.value = null
        MetaTools.turn = 0
        late.clear()
        running.clear()
        chat = Chat(host.value ?: "", "").also { it.sender = { t, _ -> send(t) } }
        version.value++
        save()
    }

    val version = mutableStateOf(0) // (a new conversation: drawn anew)

    init {
        chat.sender = { t, _ -> send(t) }
    }
}

/** Meta's page: its conversation (as a chat's), Clear at the foot. */
@Composable
fun MetaPage(chatHost: String, here: Boolean) {
    val c = LocalBaude.current
    Mesh.tick.value
    Meta.version.value
    val up = Mesh.reached("app_tools")
    androidx.compose.runtime.LaunchedEffect(up, chatHost) { // (a host it's on kept, even while it's away: its session
        val on = Meta.host.value
        if (on != null && Mesh.hosts.none { it.id == on } && on.removePrefix("mesh:") !in Mesh.members.keys) {
            Meta.host.value = null // (removed: picked again)
            Meta.clear()
        }
        if (Meta.host.value == null)                          // waits for the phone there)
            (up.firstOrNull { it.first == chatHost } ?: up.firstOrNull())?.let { Meta.host.value = it.first }
    }
    Column(Modifier.fillMaxSize()) {
        Column(Modifier.weight(1f)) {
            ChatPage(Meta.chat, "Meta", reachable = Meta.host.value?.let { Mesh.conn(it)?.state == "up" } == true,
                hint = "Ask Meta", attach = false)
        }
        Row(Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 2.dp)) {
            Button(Wording.Common.clear) { Meta.clear() }
        }
    }
    MetaTools.form.value?.takeIf { it.optString("state", "open") == "open" }?.let { f -> // (a form Meta brought up, as
        val done = { MetaTools.form.value = MetaTools.form.value?.let { JSONObject(it.toString())   // it's filled: the
            .put("state", if (it.optString("state") == "submitted") "submitted" else "closed") } }   // user submits)
        val submitted = { MetaTools.form.value = MetaTools.form.value?.let { JSONObject(it.toString()).put("state", "submitted") } }
        androidx.compose.runtime.key(MetaTools.formVersion.value) {
            val given = f.optJSONObject("values")
            when (f.optString("form")) {
                "spawn_agent" -> NewSession(null, null, done = done, opened = { h, s -> submitted(); Meta.opener(h, s) },
                    given = given, edited = MetaTools::edited, heading = Wording.NewSession.spawnAgent)
                "add_host" -> AddHost(done = done, given = given, edited = MetaTools::edited, added = { submitted() })
                "new_group" -> NewGroup(given, done = done, made = { submitted() })
            }
        }
    }
}

/** What Meta showed under a reply: chats (a row each, tapped to open), the open chat's pages, a setting's row (its
 * choices), the changelog (its host's own code's last commits), or a button for the user to press. */
@Composable
fun MetaRows(text: String) {
    val c = LocalBaude.current
    val o = try { JSONObject(text) } catch (_: Exception) { return }
    Mesh.tick.value
    @Composable
    fun boxed(rows: List<@Composable () -> Unit>) = Column(Modifier.fillMaxWidth()
        .clip(androidx.compose.foundation.shape.RoundedCornerShape(12.dp)).background(c.card)
        .border(1.dp, c.rule, androidx.compose.foundation.shape.RoundedCornerShape(12.dp))) {
        rows.forEachIndexed { i, r ->
            if (i > 0) androidx.compose.foundation.layout.Box(Modifier.fillMaxWidth().height(1.dp).background(c.rule))
            r()
        }
    }
    val chevron: @Composable () -> Unit = { androidx.compose.material3.Icon(Adwaita.GoNext, null, tint = c.dim) }
    o.optJSONArray("chats")?.let { rows ->
        boxed((0 until rows.length()).mapNotNull { rows.optJSONArray(it) }.map { r ->
            {
                val (h, sid) = r.optString(0) to r.optString(1)
                val s = Mesh.session(h, sid)
                ActionRow(s?.bot?.name?.ifBlank { null } ?: s?.title?.ifBlank { null } ?: Wording.Common.untitled,
                    listOfNotNull(Mesh.label(h), s?.let { shortPath(h, it.cwd.orEmpty()) },
                        s?.let { groupOf(h, it) }?.ifEmpty { null }?.let { Wording.MetaChats.groupNamed(name = it) }).joinToString(" · "),
                    onClick = { Meta.opener(h, sid) }, suffix = chevron)
            }
        })
    }
    o.optJSONArray("pages")?.let { rows ->
        boxed(rows.strings().map { p -> { ActionRow(p, null, onClick = { Meta.pageOpener(p) }, suffix = chevron) } })
    }
    o.str("changelog")?.let { h ->
        var log by androidx.compose.runtime.remember(text) { mutableStateOf<List<Msg.Commit>?>(null) }
        var why by androidx.compose.runtime.remember(text) { mutableStateOf<String?>(null) }
        androidx.compose.runtime.LaunchedEffect(text) {
            val checkout = Mesh.conn(h)?.info?.checkout?.ifBlank { null }
                ?: return@LaunchedEffect run { why = "Its host didn't say where its code is." }
            Mesh.ask(h, Msg.Git(what = "log", cwd = checkout, limit = 15).json()) { r ->
                val g = Msg.GitReply.from(r)
                if (r.optString("op") == Op.GIT && g.error.isNullOrBlank()) log = g.commits.orEmpty()
                else why = said(r, g.error).ifBlank { "Couldn't read it." }
            }
        }
        Column {
            Text("What's new on ${Mesh.label(h)}", style = Type.folder, color = c.dim, modifier = Modifier.padding(bottom = 4.dp))
            when {
                why != null -> Text(why!!, style = Type.caption, color = c.dim)
                log == null -> Text("Reading it…", style = Type.caption, color = c.dim)
                else -> boxed(log!!.map { m -> { ActionRow(m.subject.orEmpty(), java.text.SimpleDateFormat("d MMMM yyyy, HH:mm",
                    java.util.Locale.ENGLISH).format(java.util.Date((m.at?.toLong() ?: 0L) * 1000)) + " · " + m.sha.orEmpty().take(7)) } })
            }
        }
    }
    o.str("button")?.let { b -> // (a control for the user to press: Meta doesn't do these itself)
        var asking by androidx.compose.runtime.remember { mutableStateOf(false) }
        Button(o.str("label") ?: b, suggested = b != "trash", destructive = b == "trash") {
            when (b) {
                "trash" -> asking = true
                "host" -> { HomePrefs.host.value = o.optString("host"); HomePrefs.save(); Meta.go("home") }
                else -> Meta.go(b)
            }
        }
        if (asking) {
            val h = o.optString("host")
            val sid = o.optString("sid")
            val title = Mesh.session(h, sid)?.let { it.bot?.name?.ifBlank { null } ?: it.title?.ifBlank { null } } ?: Wording.Trash.thisSession
            androidx.compose.material3.AlertDialog(onDismissRequest = { asking = false },
                title = { Text(Wording.Trash.heading(title = title)) },
                text = { Text(Wording.Trash.body(where = Wording.Trash.onHost(host = Mesh.label(h)))) },
                confirmButton = { Button(Wording.Trash.move, destructive = true) {
                    asking = false
                    Mesh.ask(h, Msg.Delete(session = sid).json()) { r ->
                        if (r.optString("op") == Op.ERROR) Meta.chat.said.value =
                            Wording.Trash.hostRefused(op = "delete", host = Mesh.label(h), why = Msg.Error.from(r).said.orEmpty())
                    }
                } },
                dismissButton = { Button(Wording.Common.cancel) { asking = false } }, containerColor = c.dialog)
        }
    }
    when (o.optString("setting")) {
        "sidebar_group" -> Row(verticalAlignment = Alignment.CenterVertically) {
            Text(Wording.Sidebar.groupBy, style = Type.body, color = c.fg, modifier = Modifier.weight(1f))
            Picker(if (HomePrefs.by.value == "date") Wording.Sidebar.date else Wording.Common.folder, Wording.Sidebar.groupBy,
                listOf(Wording.Common.folder to "folder", Wording.Sidebar.date to "date"), HomePrefs.by.value) { HomePrefs.by.value = it; HomePrefs.save() }
        }
        "sidebar_host" -> Row(verticalAlignment = Alignment.CenterVertically) {
            Text("The home screen shows", style = Type.body, color = c.fg, modifier = Modifier.weight(1f))
            val up = Mesh.reached("subscribe")
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
        Text(if (a.undone.value) Wording.MetaActions.undone(action = a.said) else a.said, style = Type.caption, color = if (a.undone.value) c.dim else c.fg, modifier = Modifier.weight(1f))
        if (a.undone.value) IconButton(Adwaita.EditRedo, Wording.Common.redo) { MetaTools.button(a, undo = false) }
        else IconButton(Adwaita.EditUndo, Wording.Common.undo) { MetaTools.button(a, undo = true) }
    }
}

/** Meta's new_group form: the group's name (Baude's New group), made once the user presses Create. */
@Composable
fun NewGroup(given: JSONObject?, done: () -> Unit, made: () -> Unit) {
    val c = LocalBaude.current
    var name by androidx.compose.runtime.remember { mutableStateOf(given?.str("name").orEmpty()) }
    androidx.compose.runtime.LaunchedEffect(name) { MetaTools.edited(JSONObject().put("name", name)) }
    val wrong = MetaTools.wrong("new_group", JSONObject().put("name", name))
    androidx.compose.material3.AlertDialog(onDismissRequest = done, title = { Text(Wording.NewSession.newGroup) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("A list of chats of its own, picked in ⋮ → Groups.", style = Type.body, color = c.dim)
                Field(name, { name = it }, Wording.MetaApp.itsName)
                if (name.isNotBlank() && wrong != null) Text(wrong, style = Type.caption, color = c.destructive)
            }
        },
        confirmButton = { Button(Wording.Common.create, suggested = wrong == null) {
            if (wrong != null) return@Button
            val g = name.split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")
            HomePrefs.made.add(g)
            HomePrefs.save()
            made()
            done()
        } },
        dismissButton = { Button(Wording.Common.cancel, onClick = done) }, containerColor = c.dialog)
}
