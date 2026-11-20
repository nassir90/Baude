package dev.baude.android

import android.graphics.BitmapFactory
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.InsertDriveFile
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.ArrowUpward
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.HelpOutline
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Base64
import java.util.Date
import java.util.Locale

// ------------------------------------------------------------------------------------------------ side questions --

/** A side question (/btw): asked of the chat's conversation without taking it up (start's ephemeral, forked from the
 * chat), its answer, and what's asked after it; kept while the chat's open, as Baude keeps them while its window is. */
class SideQuestion(val question: String) {
    var sid = ""
    val thread = mutableStateListOf<Line>()   // (its answers, and what's asked after: you and bot)
    val status = mutableStateOf("running")    // (running, done, error, interrupted)
    val text = StringBuilder()                // (the answer coming, as it streams)
}

/** (the chat open) What its side questions' sessions say: their answers, their turns' ends; what they ask, denied. */
fun sideQuestions(chat: Chat): (String, JSONObject) -> Unit = l@{ at, msg ->
    if (at != chat.hostId) return@l
    if (msg.optString("op") == "connected") { // (its host back after a drop: those it was answering were let go of)
        chat.btw.filter { it.status.value == "running" }.forEach { it.status.value = "interrupted" }
        return@l
    }
    val op = msg.optString("op")
    val session = when (op) {
        Op.EXITED -> Msg.Exited.from(msg).session
        Op.DELETED -> Msg.Deleted.from(msg).session
        Op.CLAUDE -> Msg.Claude.from(msg).session
        else -> return@l
    }
    val q = chat.btw.firstOrNull { it.sid.isNotEmpty() && it.sid == session.orEmpty() } ?: return@l
    when (op) {
        Op.EXITED, Op.DELETED -> if (q.status.value == "running") q.status.value = "interrupted"
        Op.CLAUDE -> {
            val m = Msg.Claude.from(msg).msg ?: return@l
            Ask.of(m)?.let { a -> // (no tools that ask: denied, as Baude's side questions deny them)
                Mesh.ask(at, Msg.Input(session = q.sid, msg = JSONObject()
                    .put("type", "control_response").put("response", JSONObject().put("subtype", "success")
                        .put("request_id", a.rid).put("response", JSONObject().put("behavior", "deny")
                            .put("message", "Side questions can't run tools that need permission.")))).json()) {}
            }
            when (m.optString("type")) {
                "assistant" -> if (m.isNull("parent_tool_use_id")) for (b in m.optJSONObject("message")
                    ?.optJSONArray("content").list()) if (b.optString("type") == "text") q.text.append(b.optString("text"))
                "result" -> {
                    val said = q.text.toString().trim()
                    q.text.setLength(0)
                    q.thread.add(Line(if (m.optBoolean("is_error")) "error" else "bot",
                        said.ifEmpty { if (m.optBoolean("is_error")) m.optString("result").ifBlank { "The side question failed." } else "" }))
                    q.status.value = if (m.optBoolean("is_error")) "error" else "done"
                }
            }
        }
    }
}

private const val PREFACE = "(This is a side question, asked while you work on the conversation above. Answer it " +
    "briefly from what you already know; don't continue the task or use tools.)\n\n"

/** Side questions, as Baude's page has them: a box to ask one, then each (newest first), opening into its thread
 * with a box to ask after it. Each is a session of its host's of its own (ephemeral, forked from the chat, in plan
 * mode), let go of with the chat. */
@Composable
fun SideQuestionsPage(chat: Chat) {
    val c = LocalBaude.current
    val hostId = chat.hostId
    var shown by remember(chat) { mutableStateOf<SideQuestion?>(null) }
    fun ask(q: SideQuestion, text: String) {
        q.thread.add(Line("you", text))
        q.status.value = "running"
        val msg = JSONObject().put("type", "user").put("message", JSONObject().put("role", "user")
            .put("content", (if (q.thread.count { it.kind == "you" } == 1) PREFACE else "") + text))
        Mesh.ask(hostId, Msg.Input(session = q.sid, msg = msg).json()) { r ->
            if (r.optString("op") == Op.ERROR) { q.status.value = "error"; q.thread.add(Line("error", Msg.Error.from(r).said.orEmpty())) }
        }
    }
    fun start(text: String) {
        val s = Mesh.session(hostId, chat.sid)
        val q = SideQuestion(text)
        chat.btw.add(0, q)
        val model = s?.model?.ifBlank { null }
        val msg = Msg.Start(ephemeral = true, prompt = "", forkFrom = chat.sid, model = model,
            mode = if (program(model) == "codex") "default" else "plan", attach = true, answers = true).json()
        Mesh.ask(hostId, msg) { r ->
            val id = Msg.Started.from(r).session?.id
            if (r.optString("op") != Op.STARTED || id == null) {
                q.status.value = "error"
                q.thread.add(Line("error", "The side question couldn't be asked: " + Msg.Error.from(r).said.orEmpty()))
                return@ask
            }
            q.sid = id
            ask(q, text)
        }
    }
    val q = shown
    if (q != null) {
        BackHandler { shown = null }
        Column(Modifier.fillMaxSize()) {
            Row(Modifier.fillMaxWidth().padding(start = 13.dp, end = 7.dp, top = 8.dp, bottom = 8.dp),
                verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                if (q.status.value != "done") AgentStatus(if (q.status.value == "error") "failed" else q.status.value)
                Text(q.question, style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f))
                IconButton(Icons.AutoMirrored.Filled.ArrowBack, "All side questions") { shown = null }
            }
            Column(Modifier.weight(1f).padding(top = 6.dp).inside(c)) {
                LazyColumn(Modifier.weight(1f), contentPadding = PaddingValues(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    items(q.thread.drop(1)) { Said(it) }
                    if (q.status.value == "running") item { Text("Working…", style = Type.caption, color = c.dim) }
                }
                AskBox("Ask after it", enabled = q.sid.isNotEmpty() && q.status.value != "running" && q.status.value != "interrupted") { ask(q, it) }
            }
        }
        return
    }
    Scrolled {
        Box(Modifier.padding(start = 10.dp, end = 10.dp, top = 10.dp)) {
            AskBox("Ask a side question", enabled = offers(hostId, "ephemeral") && Mesh.session(hostId, chat.sid) != null, ::start)
        }
        if (chat.btw.isNotEmpty()) BoxedList(chat.btw.toList(), "") { sq ->
            ActionRow(sq.question, sq.thread.getOrNull(1)?.text?.lines()?.firstOrNull(), onClick = { shown = sq },
                prefix = if (sq.status.value != "done") ({ AgentStatus(if (sq.status.value == "error") "failed" else sq.status.value) }) else null,
                suffix = { androidx.compose.material3.Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, null, tint = c.dim) })
        }
    }
}

/** A box to ask in (the composer's look: rounded 12, its ring), its send button off while it's empty. */
@Composable
private fun AskBox(hint: String, enabled: Boolean = true, send: (String) -> Unit) {
    val c = LocalBaude.current
    var text by remember { mutableStateOf("") }
    Row(Modifier.fillMaxWidth().padding(8.dp).clip(RoundedCornerShape(Gap.composerRadius)).background(c.inside)
        .border(1.dp, c.rule, RoundedCornerShape(Gap.composerRadius)).padding(6.dp), verticalAlignment = Alignment.Bottom) {
        Box(Modifier.weight(1f).padding(horizontal = 6.dp, vertical = 7.dp)) {
            if (text.isEmpty()) Text(hint, style = Type.body, color = c.dim)
            BasicTextField(text, { text = it }, enabled = enabled, textStyle = Type.body.copy(color = c.fg),
                cursorBrush = SolidColor(c.accentBg), modifier = Modifier.fillMaxWidth().heightIn(max = 140.dp))
        }
        val ready = enabled && text.isNotBlank()
        IconButton(Icons.Filled.ArrowUpward, "Ask", enabled = ready, tint = if (ready) c.accentFg else c.fg,
            background = if (ready) c.accentBg else null) { send(text.trim()); text = "" }
    }
}

// ---------------------------------------------------------------------------------------------------------- files --

/** The chat's folder, as Baude's Files page has it: folders first, then files, git's ignored ones faint; a folder
 * opened into (the phone's narrow for a tree: one folder at a time, up to go back), a file read (its text; a picture
 * shown). Read from its host (list_dir, read_file). */
@Composable
fun FilesPage(hostId: String, sid: String) {
    val c = LocalBaude.current
    val root = Mesh.session(hostId, sid)?.cwd?.ifBlank { null }
    if (!offers(hostId, "list_dir")) return StatusPage(Icons.Filled.Folder, "Files", tooOld(hostId, "list its folders for a phone"))
    if (root == null) return StatusPage(Icons.Filled.Folder, "Files", "No folder yet")
    var at by remember(root) { mutableStateOf(root) }
    var entries by remember(at) { mutableStateOf<List<Msg.DirEntry>?>(null) }
    var error by remember(at) { mutableStateOf<String?>(null) }
    var file by remember(root) { mutableStateOf<String?>(null) }
    var again by remember { mutableStateOf(0) }
    LaunchedEffect(at, again) {
        Mesh.ask(hostId, Msg.ListDir(path = at).json()) { r ->
            val d = Msg.Dir.from(r)
            if (r.optString("op") == Op.DIR && d.error.isNullOrBlank()) entries = d.entries.orEmpty()
            else error = said(r, d.error).ifBlank { "Couldn't read it" }
        }
    }
    file?.let { f -> return FileView(hostId, f) { file = null } }
    if (at != root) BackHandler { at = at.substringBeforeLast('/') }
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(start = 12.dp, end = 6.dp, top = 6.dp, bottom = 2.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(at.substringAfterLast('/').ifEmpty { "/" }, style = Type.title, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(shortPath(hostId, at.substringBeforeLast('/')), style = Type.caption, color = c.dim, maxLines = 1,
                    overflow = TextOverflow.Ellipsis)
            }
            if (at != root) IconButton(Icons.Filled.ArrowUpward, "Up a folder") { at = at.substringBeforeLast('/') }
            IconButton(Icons.Filled.Refresh, "Read the folders again") { entries = null; error = null; again++ }
        }
        Scrolled {
            when {
                error != null -> Empty(error!!)
                entries == null -> Empty("Reading it…")
                else -> BoxedList(entries!!, "Nothing in this folder") { e ->
                    val path = "$at/${e.name.orEmpty()}"
                    val dir = e.dir == true
                    Box(Modifier.alpha(if (e.ignored == true) 0.55f else 1f)) {
                        ActionRow(e.name.orEmpty(), if (dir) null else size(e.size?.toLong() ?: 0L),
                            onClick = { if (dir) at = path else file = path },
                            prefix = { androidx.compose.material3.Icon(if (dir) Icons.Filled.Folder
                                else Icons.AutoMirrored.Filled.InsertDriveFile, null, tint = c.dim, modifier = Modifier.size(18.dp)) })
                    }
                }
            }
        }
    }
}

private fun size(n: Long) = when {
    n < 1024 -> "$n bytes"
    n < 1024 * 1024 -> "%.1f KB".format(n / 1024.0)
    else -> "%.1f MB".format(n / 1024.0 / 1024.0)
}

private val PICTURES = setOf("png", "jpg", "jpeg", "gif", "webp", "bmp")

/** A file of the host's, read (read_file): its text in the code's type, a picture shown, or why it can't be. */
@Composable
private fun FileView(hostId: String, path: String, back: () -> Unit) {
    val c = LocalBaude.current
    BackHandler(onBack = back)
    var data by remember(path) { mutableStateOf<ByteArray?>(null) }
    var why by remember(path) { mutableStateOf<String?>(null) }
    var cut by remember(path) { mutableStateOf(false) }
    LaunchedEffect(path) {
        Mesh.ask(hostId, Msg.ReadFile(path = path, limit = 1_000_000).json()) { r ->
            val f = Msg.File.from(r)
            if (r.optString("op") == Op.FILE && f.error.isNullOrBlank()) {
                data = Base64.getDecoder().decode(f.data.orEmpty())
                cut = f.truncated == true || (f.size?.toLong() ?: 0L) > (data?.size ?: 0)
            } else why = said(r, f.error).ifBlank { "Couldn't read it" }
        }
    }
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(start = 12.dp, end = 6.dp, top = 6.dp, bottom = 2.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(path.substringAfterLast('/'), style = Type.title, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text("on ${Mesh.label(hostId)}" + if (cut) " · its first part" else "", style = Type.caption, color = c.dim)
            }
            IconButton(Icons.AutoMirrored.Filled.ArrowBack, "Back", onClick = back)
        }
        Box(Modifier.fillMaxSize().padding(top = 6.dp).inside(c)) {
            val d = data
            when {
                why != null -> Empty(why!!)
                d == null -> Empty("Reading it…")
                path.substringAfterLast('.').lowercase() in PICTURES -> BitmapFactory.decodeByteArray(d, 0, d.size)?.let {
                    Image(it.asImageBitmap(), path, contentScale = ContentScale.Fit, modifier = Modifier.fillMaxSize().padding(8.dp))
                } ?: Empty("Not a picture this phone can read")
                d.take(8192).contains(0.toByte()) -> Empty("${path.substringAfterLast('/')}: not text to read here")
                else -> Text(String(d, Charsets.UTF_8), style = Type.mono, color = c.fg, modifier = Modifier.fillMaxSize()
                    .verticalScroll(rememberScrollState()).horizontalScroll(rememberScrollState()).padding(12.dp))
            }
        }
    }
}

// ------------------------------------------------------------------------------------------------------------ git --

/** The chat's folder's git, as Baude's Git page has it: its branch and how it stands with its upstream, then its
 * history (Show more), a commit opening into its card (author, message, its files' + and −, the branches it's on),
 * a file into its diff. Read from its host (git), nothing fetched. */
@Composable
fun GitPage(hostId: String, sid: String) {
    val c = LocalBaude.current
    val cwd = Mesh.session(hostId, sid)?.cwd?.ifBlank { null }
    if (!offers(hostId, "git")) return StatusPage(Icons.Filled.Schedule, "Git", tooOld(hostId, "read its git for a phone"))
    if (cwd == null) return StatusPage(Icons.Filled.Schedule, "Git", "No folder yet")
    var log by remember(cwd) { mutableStateOf<Msg.GitReply?>(null) }
    val commits = remember(cwd) { mutableStateListOf<Msg.Commit>() }
    var why by remember(cwd) { mutableStateOf<String?>(null) }
    var commit by remember(cwd) { mutableStateOf<String?>(null) }
    var again by remember { mutableStateOf(0) }
    fun more(skip: Int) = Mesh.ask(hostId, Msg.Git(what = "log", cwd = cwd, limit = 50, skip = skip).json()) { r ->
        val g = Msg.GitReply.from(r)
        if (r.optString("op") == Op.GIT && g.error.isNullOrBlank()) {
            log = g
            if (skip == 0) commits.clear()
            commits.addAll(g.commits.orEmpty())
        } else why = said(r, g.error).ifBlank { "Couldn't read it" }
    }
    LaunchedEffect(cwd, again) { more(0) }
    commit?.let { sha -> return CommitCard(hostId, cwd, sha) { commit = null } }
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(start = 12.dp, end = 6.dp, top = 6.dp, bottom = 2.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(shortPath(hostId, log?.top?.ifBlank { null } ?: cwd), style = Type.caption, color = c.dim, maxLines = 1,
                overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            IconButton(Icons.Filled.Refresh, "Read the history again") { why = null; log = null; again++ }
        }
        when {
            why != null -> StatusPage(Icons.Filled.Schedule, "Git", why)
            log == null -> Empty("Reading it…")
            else -> Scrolled {
                val b = log!!.branch
                b?.name?.ifBlank { null }?.let { name ->
                    Text(name, style = Type.bold, color = c.fg, modifier = Modifier.padding(start = 12.dp, end = 12.dp, top = 4.dp))
                }
                b?.upstream?.ifBlank { null }?.let { up ->
                    val a = b.ahead?.toInt() ?: 0
                    val be = b.behind?.toInt() ?: 0
                    val how = if (a == 0 && be == 0) "up to date" else listOfNotNull(
                        a.takeIf { it > 0 }?.let { "$it commit${if (it != 1) "s" else ""} ahead" },
                        be.takeIf { it > 0 }?.let { "$it commit${if (it != 1) "s" else ""} behind" }).joinToString(", ")
                    Text("Tracks $up: $how", style = Type.caption, color = c.dim, modifier = Modifier.padding(horizontal = 12.dp))
                }
                BoxedList(commits.toList(), "No commits") { m ->
                    ActionRow(m.subject.orEmpty(), listOf(m.author.orEmpty(), stamped(m.at?.toLong() ?: 0L),
                        m.sha.orEmpty().take(7)).joinToString(" · ") + m.refs.orEmpty().let {
                        if (it.isEmpty()) "" else " · " + it.joinToString(", ") }, onClick = { commit = m.sha.orEmpty() })
                }
                if (log!!.more == true) Text("Show more", style = Type.body, color = c.accent, modifier = Modifier
                    .padding(horizontal = 12.dp, vertical = 4.dp).clip(RoundedCornerShape(6.dp)).clickable { more(commits.size) })
            }
        }
    }
}

private fun stamped(at: Long) = SimpleDateFormat("d MMM yyyy, HH:mm", Locale.ENGLISH).format(Date(at * 1000))

@Composable
private fun CommitCard(hostId: String, cwd: String, sha: String, back: () -> Unit) {
    val c = LocalBaude.current
    BackHandler(onBack = back)
    var card by remember(sha) { mutableStateOf<Msg.GitReply?>(null) }
    var diff by remember(sha) { mutableStateOf<String?>(null) }
    var diffText by remember(sha) { mutableStateOf<String?>(null) }
    LaunchedEffect(sha) {
        Mesh.ask(hostId, Msg.Git(what = "card", cwd = cwd, sha = sha).json()) { r ->
            card = Msg.GitReply.from(r).let { g -> if (r.optString("op") == Op.ERROR) Msg.GitReply(error = said(r, null)) else g }
        }
    }
    LaunchedEffect(diff) {
        val p = diff ?: return@LaunchedEffect
        diffText = null
        Mesh.ask(hostId, Msg.Git(what = "diff", cwd = cwd, sha = sha, path = p).json()) { r ->
            val g = Msg.GitReply.from(r)
            diffText = said(r, g.error).ifBlank { null } ?: g.text.orEmpty()
        }
    }
    diff?.let { p ->
        BackHandler { diff = null }
        Column(Modifier.fillMaxSize()) {
            Row(Modifier.fillMaxWidth().padding(start = 12.dp, end = 6.dp, top = 6.dp, bottom = 2.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(p, style = Type.bold, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                IconButton(Icons.AutoMirrored.Filled.ArrowBack, "The commit") { diff = null }
            }
            Box(Modifier.fillMaxSize().padding(top = 6.dp).inside(c)) {
                val t = diffText
                if (t == null) Empty("Reading it…")
                else { // (a line at a time, as it's scrolled to: a long diff isn't all drawn)
                    val lines = remember(t) { t.lines() }
                    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(8.dp)) {
                        items(lines) { l ->
                            Text(l, style = Type.mono, color = when {
                                l.startsWith("+") && !l.startsWith("+++") -> c.added
                                l.startsWith("-") && !l.startsWith("---") -> c.removed
                                l.startsWith("@@") -> c.accent
                                else -> c.fg
                            })
                        }
                    }
                }
            }
        }
        return
    }
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(start = 12.dp, end = 6.dp, top = 6.dp, bottom = 2.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(sha.take(9), style = Type.title.copy(fontFamily = Type.mono.fontFamily), color = c.fg, modifier = Modifier.weight(1f))
            IconButton(Icons.AutoMirrored.Filled.ArrowBack, "The log", onClick = back)
        }
        val k = card
        when {
            k == null -> Empty("Reading the commit…")
            !k.error.isNullOrBlank() -> Empty(k.error)
            else -> Scrolled {
                val msg = k.message.orEmpty()
                Column(Modifier.padding(horizontal = 12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(msg.lines().first(), style = Type.bold, color = c.fg)
                    Text("${k.author.orEmpty()} · ${stamped(k.at?.toLong() ?: 0L)} · ${k.sha.orEmpty()}", style = Type.caption, color = c.dim)
                    if (k.committer.orEmpty() != k.author.orEmpty()) Text("Committed by ${k.committer.orEmpty()}",
                        style = Type.caption, color = c.dim)
                }
                val files = k.files.let { a -> (0 until (a?.length() ?: 0)).mapNotNull { a!!.optJSONArray(it) } }
                val added = files.sumOf { if (it.isNull(1)) 0 else it.optInt(1) }
                val removed = files.sumOf { if (it.isNull(2)) 0 else it.optInt(2) }
                Text("${files.size} file${if (files.size != 1) "s" else ""}  +$added −$removed", style = Type.caption,
                    color = c.dim, modifier = Modifier.padding(start = 12.dp, top = 8.dp))
                BoxedList(files, "") { f ->
                    ActionRow(f.optString(0), if (f.isNull(1)) "binary" else "+${f.optInt(1)} −${f.optInt(2)}",
                        onClick = { diff = f.optString(0) })
                }
                val rest = msg.lines().drop(1).joinToString("\n").trim()
                if (rest.isNotEmpty()) Text(rest, style = Type.body, color = c.fg, modifier = Modifier.padding(horizontal = 12.dp))
                val on = k.branches.orEmpty()
                Text(if (on.isEmpty()) "On no branch (rewritten, or only in the reflog)" else "On ${on.joinToString(", ")}",
                    style = Type.caption, color = c.dim, modifier = Modifier.padding(12.dp))
            }
        }
    }
}

// --------------------------------------------------------------------------------------------------------- signal --

private val KINDS = mapOf("file" to "Files", "commit" to "Commits", "branch" to "Branches", "pr" to "Pull requests",
    "env" to "Env vars", "constant" to "Constants", "symbol" to "Code symbols", "config" to "Config keys",
    "service" to "Services", "k8s" to "Kubernetes objects", "package" to "Packages", "cli" to "Commands",
    "external" to "External tools", "tool" to "Tools", "subagent" to "Subagent types", "skill" to "Skills",
    "route" to "HTTP routes", "db" to "Database tables and columns")

/** Signal's names for the chat, as Baude's Signal page lists them: those its replies named that the folder's glossary
 * has, by kind, each how it stands (linked, several matches, looking up, not found, stale) and what it is. Read from
 * its host (signal_names), as Signal last left them there: the reader runs where Baude does. */
@Composable
fun SignalPage(hostId: String, sid: String) {
    val c = LocalBaude.current
    val s = Mesh.session(hostId, sid)
    val cwd = s?.cwd?.ifBlank { null }
    if (!offers(hostId, "signal_names")) return StatusPage(Icons.Filled.HelpOutline, "Signal", tooOld(hostId, "list Signal's names for a phone"))
    if (cwd == null) return StatusPage(Icons.Filled.HelpOutline, "Signal", "No folder yet")
    var got by remember(cwd, sid) { mutableStateOf<Msg.SignalNamesReply?>(null) }
    var filter by remember { mutableStateOf("") }
    var again by remember { mutableStateOf(0) }
    LaunchedEffect(cwd, sid, again) {
        val msg = Msg.SignalNames(cwd = cwd, session = sid).json()
        s.cliId?.ifBlank { null }?.let { msg.put("cli_id", it) }
        Mesh.ask(hostId, msg) { r ->
            got = Msg.SignalNamesReply.from(r).let { g -> if (r.optString("op") == Op.ERROR) Msg.SignalNamesReply(error = said(r, null)) else g }
        }
    }
    val g = got
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.padding(start = 8.dp, end = 6.dp, top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            SearchField(filter, { filter = it }, "Filter names", Modifier.weight(1f))
            IconButton(Icons.Filled.Refresh, "Read them again") { got = null; again++ }
        }
        when {
            g == null -> Empty("Reading them…")
            !g.error.isNullOrBlank() -> Empty(g.error)
            else -> {
                val refs = g.refs.orEmpty()
                if (refs.isEmpty()) return@Column StatusPage(Icons.Filled.HelpOutline, "Signal",
                    "No names yet: the files, commits, env vars and routes in Claude's replies show up here.")
                fun n(st: String) = refs.count { it.status == st }
                Scrolled {
                    Text(listOf("${n("resolved")} linked", "${n("ambiguous")} with several matches", "${n("pending")} looking up",
                        "${n("not_found")} not found", "${n("stale")} stale").joinToString(" · ") +
                        (g.glossary?.ifBlank { null }?.let { "\nGlossary: $it" } ?: ""), style = Type.caption, color = c.dim,
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp))
                    val f = filter.trim().lowercase()
                    for ((kind, rows) in refs.filter { f.isEmpty() || it.surface.orEmpty().lowercase().contains(f) }
                        .groupBy { it.kind.orEmpty() }) {
                        Text(KINDS[kind] ?: kind, style = Type.folder, color = c.dim, modifier = Modifier.padding(start = 14.dp, top = 8.dp))
                        BoxedList(rows, "") { r ->
                            val st = r.status.orEmpty()
                            ActionRow(r.surface.orEmpty(), r.summary.orEmpty(), prefix = {
                                androidx.compose.material3.Icon(when (st) {
                                    "resolved" -> Icons.Filled.CheckCircle
                                    "pending" -> Icons.Filled.Schedule
                                    "ambiguous" -> Icons.Filled.HelpOutline
                                    else -> Icons.Filled.Warning
                                }, st, tint = when (st) {
                                    "resolved" -> c.success
                                    "ambiguous", "pending" -> c.dim
                                    else -> c.warning
                                }, modifier = Modifier.size(16.dp))
                            })
                        }
                    }
                }
            }
        }
    }
}
