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
import androidx.compose.foundation.layout.heightIn
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

/** Do two changes (of the same text) touch the same lines? One going in at another's edge (a line added just above
 * or below it) doesn't. */
private fun overlap(a: Hunk, b: Hunk) = a.from < b.to && b.from < a.to

/** A Bot's memory as this phone has it, kept while the app runs (whichever page is shown: a view model's part):
 * base, the Bot's text as its host last said it (what a save is merged against); seen, what you've accepted, with
 * your edits; text, what's saved, seen with the Bot's changes not yet accepted or rejected in it (botmemory's
 * written()). Saved 600 ms after a change, at once on leaving the page, and again a little later if it couldn't be. */
class MemoryState(val hostId: String, val sid: String) {
    var base by mutableStateOf<String?>(null)
    var seen by mutableStateOf("")
    var text by mutableStateOf("")
    var mtime: Long? = null
    var failed by mutableStateOf<String?>(null)  // (why it couldn't be read, or saved, last)
    var edits by mutableIntStateOf(0)            // (changes made here not saved yet: 0 when it's as saved)
    private var saving = false
    private var retrying = false // (a try again waiting: one at a time)
    private var wait = 5_000L

    val pending: List<Hunk> get() = hunks(seen.split("\n"), text.split("\n"))

    private fun took(r: Msg.MemoryReply) {
        base = r.text.orEmpty()
        text = base!!
        seen = r.seen ?: base!!
        mtime = r.mtime?.toLong()
        failed = null
    }

    /** Read from its host: as it is there, or (changed here meanwhile) the Bot's changes since merged into yours. */
    fun load() = Mesh.ask(hostId, Msg.Memory(session = sid).json()) { r ->
        if (r.optString("op") != Op.MEMORY) { failed = Msg.Error.from(r).said.orEmpty().ifBlank { "no reason given" }; return@ask }
        val got = Msg.MemoryReply.from(r)
        if (edits == 0 || base == null) return@ask took(got)
        val theirs = got.text.orEmpty()
        if (theirs != base) text = merge3(base!!, text, theirs) ?: text
        base = theirs
        mtime = got.mtime?.toLong()
        failed = null
    }

    /** You've edited what you've seen: what's saved is your text with each of the Bot's changes still in it (one
     * is only ever gone once you reject it; you edit around them, never in them: the page has them apart). */
    fun edit(now: String) {
        val a = seen.split("\n")
        val mine = hunks(a, now.split("\n"))
        val theirs = pending
        text = if (mine.none { u -> theirs.any { overlap(u, it) } }) // (yours and its, each where it is: yours first
            applied(a, (mine + theirs).sortedWith(compareBy<Hunk>({ it.from },   // where both go in at one place)
                { if (it in mine && it.from == it.to) 0 else if (it.from == it.to) 1 else 2 }))).joinToString("\n")
        else merge3(seen, now, text) ?: text // (in one of its changes, which the page doesn't let you edit: kept as it is)
        seen = now
        edits++
    }

    fun accept(h: Hunk) {
        seen = applied(seen.split("\n"), listOf(h)).joinToString("\n")
        edits++
    }

    fun reject(k: Int) {
        val p = pending
        text = applied(seen.split("\n"), p.filterIndexed { i, _ -> i != k }).joinToString("\n")
        edits++
    }

    /** Saved now (if there's anything to save, and it isn't being saved): merged with what the Bot wrote since, if it
     * did; tried again a little later if it couldn't be. */
    fun save(tries: Int = 3, said: (String) -> Unit = {}) {
        if (edits == 0 || saving || base == null) return
        saving = true
        val n = edits
        val t = text
        val s = seen
        val msg = Msg.Memory(session = sid, text = t, seen = s).json()
        mtime?.let { msg.put("base_mtime", it) }
        Mesh.ask(hostId, msg) { r ->
            saving = false
            val no = Msg.Error.from(r) // (memory's refusal: the memory as the Bot left it, with it)
            when {
                r.optString("op") == Op.MEMORY -> {
                    val got = Msg.MemoryReply.from(r)
                    wait = 5_000L
                    base = got.text.orEmpty()
                    mtime = got.mtime?.toLong()
                    failed = null
                    if (edits == n) { took(got); edits = 0 } else save(said = said) // (changed meanwhile: that saved next)
                }
                no.text != null && tries > 0 -> { // (the Bot wrote it since: its change and yours made one)
                    val theirs = no.text
                    val merged = merge3(base ?: theirs, text, theirs)
                    if (merged == null) said("The Bot changed the same lines. Its version is shown as changes to accept or reject.")
                    text = merged ?: theirs
                    base = theirs
                    mtime = no.mtime?.toLong()
                    save(tries - 1, said)
                }
                else -> {
                    failed = no.said.orEmpty().ifBlank { "no reason given" }
                    said(if (failed in UNREACHED) "Not saved yet: can't connect to the host" else "Couldn't save: $failed")
                    if (!retrying) { // (tried again, for as long as it isn't saved: one try waiting at a time)
                        retrying = true
                        val again = wait
                        wait = (wait * 2).coerceAtMost(60_000L)
                        Mesh.later(again) { retrying = false; save(said = said) }
                    }
                }
            }
        }
    }

    companion object {
        private val held = HashMap<String, MemoryState>()
        fun of(hostId: String, sid: String) = held.getOrPut("$hostId/$sid") { MemoryState(hostId, sid) }
    }
}

/** A Bot's memory (botmemory.BotMemory): the notes it keeps, as you've seen them and edited them, with its changes
 * since shown where they are to accept or reject; you edit around them, as Baude's editor lets you. Saved 600 ms after
 * each change, and at once as you leave the page (another page, Back). */
@Composable
fun MemoryPage(hostId: String, sid: String, here: Boolean) {
    val c = LocalBaude.current
    val ctx = LocalContext.current
    val m = remember(hostId, sid) { MemoryState.of(hostId, sid) }
    val say = { t: String -> Toast.makeText(ctx, t, Toast.LENGTH_SHORT).show() }
    LaunchedEffect(here) { if (here) m.load() else m.save(said = say) }
    DisposableEffect(m) { onDispose { m.save(said = say) } }
    DisposableEffect(hostId, sid) {
        val l: (String, JSONObject) -> Unit = { at, msg ->
            if (at == hostId && msg.optString("op") == Op.TURN_ENDED && Msg.TurnEnded.from(msg).session.orEmpty() == sid) m.load()
        }
        Mesh.listeners.add(l)
        onDispose { Mesh.listeners.remove(l) }
    }
    LaunchedEffect(m.edits) { // (saved 600 ms after the last change)
        if (m.edits == 0) return@LaunchedEffect
        delay(600)
        m.save(said = say)
    }
    val failed = m.failed
    val offline = failed?.let { if (it in UNREACHED) "Can't connect to the host." else "Couldn't load the memory." }
    if (m.base == null) {
        when {
            failed == null -> StatusPage(Icons.Filled.EditNote, "No memory yet", "Notes the Bot keeps. You can edit them.")
            failed in UNREACHED -> StatusPage(Icons.Filled.EditNote, "Can't connect to the host", "It's shown once the host is back.")
            else -> StatusPage(Icons.Filled.EditNote, "Couldn't load the memory", failed)
        }
        return
    }
    val a = m.seen.split("\n")
    val pending = remember(m.seen, m.text) { m.pending }
    Column(Modifier.fillMaxSize()) {
        if (offline != null) GreyBanner("$offline " + if (m.edits > 0) "Your changes are saved once it's back." else "Showing the last saved copy.")
        Box(Modifier.fillMaxSize().inside(c)) {
            if (pending.isEmpty()) {
                BasicTextField(m.seen, { m.edit(it) }, textStyle = Type.body.copy(color = c.fg),
                    cursorBrush = SolidColor(c.accentBg), modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState())
                        .padding(horizontal = Gap.chatH, vertical = Gap.chatV))
                if (m.seen.isEmpty()) Text("Notes the Bot keeps. You can edit them.", style = Type.body, color = c.dim,
                    modifier = Modifier.padding(horizontal = Gap.chatH, vertical = Gap.chatV))
                return@Box
            }
            // its changes where they are, each to accept or reject; what's around them yours to edit
            Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = Gap.chatH, vertical = Gap.chatV),
                verticalArrangement = Arrangement.spacedBy(4.dp)) {
                @Composable
                fun segment(from: Int, to: Int) { // (what's around its changes: yours to edit; between two of them, or
                    if (from >= to) {               // before one at the top, a line to write in)
                        BasicTextField("", { now -> if (now.isNotEmpty())
                            m.edit((a.subList(0, from) + now.split("\n") + a.subList(from, a.size)).joinToString("\n")) },
                            textStyle = Type.body.copy(color = c.fg), cursorBrush = SolidColor(c.accentBg),
                            modifier = Modifier.fillMaxWidth().heightIn(min = 22.dp))
                        return
                    }
                    BasicTextField(a.subList(from, to).joinToString("\n"), { now ->
                        m.edit((a.subList(0, from) + now.split("\n") + a.subList(to, a.size)).joinToString("\n"))
                    }, textStyle = Type.body.copy(color = c.fg), cursorBrush = SolidColor(c.accentBg), modifier = Modifier.fillMaxWidth())
                }
                var at = 0
                for ((k, h) in pending.withIndex()) {
                    segment(at, h.from)
                    Proposal(a.subList(h.from, h.to), h.lines, accept = { m.accept(h) }, reject = { m.reject(k) })
                    at = h.to
                }
                segment(at, a.size)
            }
        }
    }
}

/** One of the Bot's changes: what it took out (red) and put in (green), Accept and Reject. */
@Composable
private fun Proposal(removed: List<String>, added: List<String>, accept: () -> Unit, reject: () -> Unit) {
    val c = LocalBaude.current
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(6.dp)).border(1.dp, c.rule, RoundedCornerShape(6.dp))) {
        for (l in removed) Text(l.ifEmpty { " " }, style = Type.body, color = c.fg,
            modifier = Modifier.fillMaxWidth().background(c.removed.copy(alpha = 0.18f)).padding(horizontal = 6.dp, vertical = 1.dp))
        for (l in added) Text(l.ifEmpty { " " }, style = Type.body, color = c.fg,
            modifier = Modifier.fillMaxWidth().background(c.added.copy(alpha = 0.18f)).padding(horizontal = 6.dp, vertical = 1.dp))
        Row(Modifier.padding(6.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Button("Accept", suggested = true, onClick = accept)
            Button("Reject", onClick = reject)
        }
    }
}
