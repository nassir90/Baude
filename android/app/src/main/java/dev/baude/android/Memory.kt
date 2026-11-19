package dev.baude.android

import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.EditNote
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import org.json.JSONObject

/** A change from one text to another, by lines: the lines [from, to) of the first, made lines. */
data class Hunk(val from: Int, val to: Int, val lines: List<String>)

/** The changes from a to b, by line (the longest lines in common kept, as difflib's are). */
fun hunks(a: List<String>, b: List<String>): List<Hunk> {
    val n = a.size
    val m = b.size
    val l = Array(n + 1) { IntArray(m + 1) }
    for (i in n - 1 downTo 0) for (j in m - 1 downTo 0)
        l[i][j] = if (a[i] == b[j]) l[i + 1][j + 1] + 1 else maxOf(l[i + 1][j], l[i][j + 1])
    val out = ArrayList<Hunk>()
    var i = 0
    var j = 0
    var start: Pair<Int, Int>? = null
    fun close() {
        start?.let { (si, sj) -> out.add(Hunk(si, i, b.subList(sj, j).toList())) }
        start = null
    }
    while (i < n || j < m) {
        if (i < n && j < m && a[i] == b[j]) {
            close(); i++; j++
        } else {
            if (start == null) start = i to j
            if (j < m && (i == n || l[i][j + 1] >= l[i + 1][j])) j++ else i++
        }
    }
    close()
    return out
}

/** a's lines with the hunks given (of hunks(a, …)) made. */
fun applied(a: List<String>, made: List<Hunk>): List<String> {
    val out = ArrayList<String>()
    var at = 0
    for (h in made.sortedBy { it.from }) {
        out.addAll(a.subList(at, h.from))
        out.addAll(h.lines)
        at = h.to
    }
    out.addAll(a.subList(at, a.size))
    return out
}

/** ours and theirs, each changed from base, made one; null if they changed the same lines (as botmemory's merge). */
fun merge3(base: String, ours: String, theirs: String): String? {
    val b = base.split("\n")
    val mine = hunks(b, ours.split("\n"))
    val other = hunks(b, theirs.split("\n"))
    val all = ArrayList(mine)
    for (h in other) {
        val same = mine.firstOrNull { it == h }
        if (same != null) continue
        if (mine.any { o -> h.from < o.to && o.from < h.to || h.from == o.from && (h.from == h.to || o.from == o.to) }) return null
        all.add(h)
    }
    return applied(b, all).joinToString("\n")
}

private val UNREACHED = setOf("the server isn't there", "the server didn't answer", "the server's gone", "not connected",
    "the connection went")

/** A Bot's memory (botmemory.BotMemory): the notes it keeps, as you've seen them, its changes since shown where they
 * are to accept or reject, written back 600 ms after each edit or answer. Saved while the Bot's written it since, its
 * change and yours are made one (or, the same lines, its shown as changes). */
@Composable
fun MemoryPage(hostId: String, sid: String, here: Boolean) {
    val c = LocalBaude.current
    val ctx = LocalContext.current
    var text by remember(hostId, sid) { mutableStateOf<String?>(null) } // (the Bot's, as last read)
    var seen by remember(hostId, sid) { mutableStateOf("") }           // (what you've accepted)
    var mtime by remember(hostId, sid) { mutableStateOf<Long?>(null) }
    var failed by remember(hostId, sid) { mutableStateOf<String?>(null) }
    var dirty by remember(hostId, sid) { mutableIntStateOf(0) }       // (edits not yet saved: a count, for the timer)
    var saving by remember { mutableStateOf(false) }
    var doc by remember(hostId, sid) { mutableStateOf("") }
    fun took(r: JSONObject) {
        text = r.optString("text")
        seen = if (r.has("seen") && !r.isNull("seen")) r.optString("seen") else r.optString("text")
        mtime = if (r.isNull("mtime")) null else r.optLong("mtime")
        if (hunks(seen.split("\n"), text!!.split("\n")).isEmpty()) doc = text!!
        failed = null
    }
    fun load() = Mesh.ask(hostId, JSONObject().put("op", "memory").put("session", sid)) { r ->
        if (r.optString("op") == "memory") { if (dirty == 0 && !saving) took(r) } else failed = r.optString("error")
    }
    fun write(t: String, s: String, base: String, tries: Int = 3) {
        saving = true
        val msg = JSONObject().put("op", "memory").put("session", sid).put("text", t).put("seen", s)
        mtime?.let { msg.put("base_mtime", it) }
        Mesh.ask(hostId, msg) { r ->
            saving = false
            when {
                r.optString("op") == "memory" -> { took(r); dirty = 0 }
                r.has("text") && tries > 0 -> { // (the Bot wrote it since: its change and yours made one)
                    val theirs = r.optString("text")
                    mtime = if (r.isNull("mtime")) null else r.optLong("mtime")
                    val merged = merge3(base, t, theirs)
                    if (merged == null) Toast.makeText(ctx, "The Bot changed the same lines. Its version is shown as " +
                        "changes to accept or reject.", Toast.LENGTH_LONG).show()
                    write(merged ?: theirs, s, theirs, tries - 1)
                }
                r.optString("error") in UNREACHED -> Toast.makeText(ctx, "Not saved yet: can't connect to the host",
                    Toast.LENGTH_SHORT).show()
                else -> Toast.makeText(ctx, "Couldn't save: " + r.optString("error"), Toast.LENGTH_SHORT).show()
            }
        }
    }
    LaunchedEffect(here) { if (here) load() }
    DisposableEffect(hostId, sid) {
        val l: (String, JSONObject) -> Unit = { at, msg ->
            if (at == hostId && msg.optString("op") == "turn_ended" && msg.optString("session") == sid) load()
        }
        Mesh.listeners.add(l)
        onDispose { Mesh.listeners.remove(l) }
    }
    LaunchedEffect(dirty) { // (saved 600 ms after the last edit)
        if (dirty == 0) return@LaunchedEffect
        delay(600)
        if (!saving) write(doc, doc, text ?: "")
    }
    val bot = text
    val offline = failed?.let { if (it in UNREACHED) "Can't connect to the host." else "Couldn't load the memory." }
    if (bot == null) {
        when {
            failed == null -> StatusPage(Icons.Filled.EditNote, "No memory yet", "Notes the Bot keeps. You can edit them.")
            failed in UNREACHED -> StatusPage(Icons.Filled.EditNote, "Can't connect to the host", "It's shown once the host is back.")
            else -> StatusPage(Icons.Filled.EditNote, "Couldn't load the memory", failed)
        }
        return
    }
    val a = seen.split("\n")
    val pending = hunks(a, bot.split("\n"))
    Column(Modifier.fillMaxSize()) {
        if (offline != null) GreyBanner("$offline Showing the last saved copy.")
        Box(Modifier.fillMaxSize().inside(c).alpha(if (offline != null) 0.5f else 1f)) {
            if (pending.isEmpty()) {
                BasicTextField(doc, { doc = it; dirty++ }, enabled = offline == null, textStyle = Type.body.copy(color = c.fg),
                    cursorBrush = SolidColor(c.accentBg), modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState())
                        .padding(horizontal = Gap.chatH, vertical = Gap.chatV))
                if (doc.isEmpty()) Text("Notes the Bot keeps. You can edit them.", style = Type.body, color = c.dim,
                    modifier = Modifier.padding(horizontal = Gap.chatH, vertical = Gap.chatV))
                return@Box
            }
            // its changes where they are, each to accept or reject (the text around them as you've seen it)
            Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = Gap.chatH, vertical = Gap.chatV),
                verticalArrangement = Arrangement.spacedBy(4.dp)) {
                var at = 0
                for ((k, h) in pending.withIndex()) {
                    if (h.from > at) Text(a.subList(at, h.from).joinToString("\n"), style = Type.body, color = c.fg)
                    Proposal(a.subList(h.from, h.to), h.lines,
                        accept = { val s = applied(a, listOf(h)).joinToString("\n"); seen = s; write(bot, s, bot) },
                        reject = {
                            val t = applied(a, pending.filterIndexed { i, _ -> i != k }).joinToString("\n")
                            text = t; write(t, seen, bot)
                        })
                    at = h.to
                }
                if (at < a.size) Text(a.subList(at, a.size).joinToString("\n"), style = Type.body, color = c.fg)
            }
        }
    }
}

/** One of the Bot's changes: what it took out (red) and put in (green), Accept and Reject. */
@Composable
private fun Proposal(removed: List<String>, added: List<String>, accept: () -> Unit, reject: () -> Unit) {
    val c = LocalBaude.current
    val red = if (c == DarkBaude) Color(0xFFFF7B63) else Color(0xFFC01C28)
    val green = if (c == DarkBaude) Color(0xFF8FF0A4) else Color(0xFF26A269)
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(6.dp)).border(1.dp, c.rule, RoundedCornerShape(6.dp))) {
        for (l in removed) Text(l.ifEmpty { " " }, style = Type.body, color = c.fg,
            modifier = Modifier.fillMaxWidth().background(red.copy(alpha = 0.18f)).padding(horizontal = 6.dp, vertical = 1.dp))
        for (l in added) Text(l.ifEmpty { " " }, style = Type.body, color = c.fg,
            modifier = Modifier.fillMaxWidth().background(green.copy(alpha = 0.18f)).padding(horizontal = 6.dp, vertical = 1.dp))
        Row(Modifier.padding(6.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Button("Accept", suggested = true, onClick = accept)
            Button("Reject", onClick = reject)
        }
    }
}
