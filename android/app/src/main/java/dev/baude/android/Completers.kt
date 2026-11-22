package dev.baude.android

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import org.json.JSONArray
import org.json.JSONObject

// ------------------------------------------------------------------------------------------------ pick list --

/** One row of a pick list: a logo, a name and a dimmed line about it (tip: its fuller description). */
class PickRow(val icon: ImageVector, val name: String, val about: String, val tip: String? = null)

/** A list to pick from as you type, the focus left in the text (widgets.PickList): rows, and under them how many there
 * are at the right and, at the left, dots while more are on their way. */
@Composable
fun PickList(rows: List<PickRow>, lit: Int, note: String, coming: Boolean, width: Dp, aboutEnd: Boolean,
             modifier: Modifier = Modifier, pick: (Int) -> Unit) {
    val c = LocalBaude.current
    val shape = RoundedCornerShape(12.dp)
    val state = rememberLazyListState()
    LaunchedEffect(lit, rows.size) {
        if (rows.isNotEmpty() && state.layoutInfo.visibleItemsInfo.none { it.index == lit }) state.scrollToItem(lit)
    }
    Column(modifier.width(width).clip(shape).background(c.dialog).border(1.dp, c.rule, shape),
        verticalArrangement = Arrangement.spacedBy(2.dp)) {
        if (rows.isNotEmpty()) LazyColumn(Modifier.heightIn(max = Tokens.Picklist.height), state = state) {
            itemsIndexed(rows) { i, r ->
                Row(Modifier.fillMaxWidth().background(if (i == lit) c.fg.copy(alpha = 0.10f) else androidx.compose.ui.graphics.Color.Transparent)
                    .clickable { pick(i) }.padding(horizontal = 8.dp, vertical = 6.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Icon(r.icon, null, tint = c.fg, modifier = Modifier.size(16.dp.icon))
                    Text(r.name, style = Type.bold, color = c.fg, maxLines = 1, overflow = TextOverflow.MiddleEllipsis,
                        modifier = Modifier.widthIn(max = 260.dp))
                    Text(r.about, style = Type.caption, color = c.dim, maxLines = 1,
                        overflow = if (aboutEnd) TextOverflow.Ellipsis else TextOverflow.MiddleEllipsis,
                        modifier = Modifier.weight(1f).then(if (r.tip != null) Modifier.semantics { contentDescription = r.tip } else Modifier))
                }
            }
        }
        Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp).padding(bottom = 4.dp), horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically) {
            WorkingDots(coming)
            Text(note, style = Type.caption, color = c.dim, modifier = Modifier.weight(1f), textAlign = androidx.compose.ui.text.style.TextAlign.End)
        }
    }
}

/** Which row of a list is lit: the first, unless the arrows have moved it (what's picked then stays picked as the list
 * is made again, while what's typed is the same). */
class Lit {
    var moved by mutableStateOf(false)
    private var key by mutableStateOf("")
    private var query by mutableStateOf("")

    fun at(keys: List<String>, q: String): Int =
        if (moved && q == query) keys.indexOf(key).takeIf { it >= 0 } ?: 0 else 0

    fun move(keys: List<String>, q: String, step: Int) {
        if (keys.isEmpty()) return
        val j = (at(keys, q) + step).coerceIn(0, keys.lastIndex)
        key = keys[j]
        query = q
        moved = true
    }

    fun reset() { moved = false }
}

// ------------------------------------------------------------------------------------------------- the / list --

/** Claude Code's commands for a chat, as its program says them: the host's last init at first, the chat's own asked
 * once the / list first opens in it, then as its stream says they change (commands_changed). */
object SlashCommands {
    val byChat = mutableStateMapOf<String, List<JSONObject>>()
    private val asked = HashSet<String>()

    private fun key(chat: Chat) = "${chat.hostId}/${chat.sid}"

    private fun list(a: JSONArray?): List<JSONObject> = a.list().filter { it.optString("name").isNotEmpty() }

    fun claude(chat: Chat): List<JSONObject> =
        byChat[key(chat)] ?: list(Mesh.conn(chat.hostId)?.info?.init?.optJSONArray("commands"))

    fun changed(chat: Chat, commands: JSONArray?) {
        byChat[key(chat)] = list(commands)
    }

    /** The chat's own list asked for, once. */
    fun ask(chat: Chat) {
        if (!takes(chat.hostId, "control") || !asked.add(key(chat))) return
        controlAnswer(chat.hostId, chat.sid, JSONObject().put("subtype", "initialize").put("hooks", JSONObject.NULL)) { r, _ ->
            r?.optJSONArray("commands")?.let { changed(chat, it) }
        }
    }

    /** Baude's own commands, handled by it and never sent. */
    val own: List<JSONObject> by lazy {
        fun cmd(name: String, about: String, hint: String, vararg aliases: String) = JSONObject().put("name", name)
            .put("description", about).put("argumentHint", hint).put("aliases", JSONArray(aliases.toList()))
        listOf(cmd("new", Wording.SlashCommands.new, "", "clear"), cmd("cd", Wording.SlashCommands.cd, "<path>"),
            cmd("btw", Wording.SlashCommands.btw, "<question>"),
            cmd("login", Wording.SlashCommands.login, "[--console | --sso | --email <address>]"),
            cmd("logout", Wording.SlashCommands.logout, ""))
    }

    /** Baude's, then Claude's sorted by name, minus any named as one of Baude's (set_commands). */
    fun all(chat: Chat): List<JSONObject> {
        val seen = own.flatMap { c -> listOf(c.optString("name")) + c.optJSONArray("aliases").texts() }.toSet()
        return own + claude(chat).filter { it.optString("name") !in seen }.sortedBy { it.optString("name") }
    }
}

private fun JSONArray?.texts(): List<String> = if (this == null) emptyList() else (0 until length()).mapNotNull { optString(it).ifEmpty { null } }

/** The / list over the box (chatinput.SlashCompleter): where its /word is, what's typed after it, and what matches. */
class SlashView(val start: Int, val query: String, val matches: List<JSONObject>)

object SlashList {
    private val WORD = Regex("""(?:^|(?<=\s))/(\S*)$""")

    /** Whether a place in the text is in a code block or a `code` span (a / there is a path's, a shell's). */
    fun inCode(text: String, offset: Int): Boolean {
        val before = text.substring(0, offset)
        val at = before.count { it == '\n' }
        return fenceSpans(text.split("\n"), tildes = false, typing = setOf(at)).any { at in it.first..it.second } ||
            before.substringAfterLast('\n').count { it == '`' } % 2 == 1
    }

    /** (where its / is, what's typed after it) of the /word the caret's at the end of, or null if we shouldn't complete:
     * a space, a new line or the text's end next; the prompt's first word or a / just typed; not in code. */
    fun word(text: String, cursor: Int, typed: Int, typedIn: Int): Pair<Int, String>? {
        if (cursor < 0 || cursor > text.length) return null
        val m = WORD.find(text.substring(0, cursor)) ?: return null
        val start = m.range.first
        if (cursor < text.length && text[cursor].toString().isNotBlank()) return null
        if (start != 0 && start != typed + typedIn) return null
        if (inCode(text, start)) return null
        return start to m.groupValues[1].lowercase()
    }

    private fun rank(c: JSONObject, q: String): Int? {
        val name = c.optString("name").lowercase()
        val aliases = c.optJSONArray("aliases").texts().map { it.lowercase() }
        return when {
            name.startsWith(q) -> 0
            aliases.any { it.startsWith(q) } -> 1
            (listOf(name) + aliases).any { q in it } -> 2
            q.isNotEmpty() && q in c.optString("description").lowercase() -> 3
            else -> null
        }
    }

    fun view(chat: Chat, text: String, cursor: Int, typed: Int, typedIn: Int): SlashView? {
        val (start, q) = word(text, cursor, typed, typedIn) ?: return null
        if ("/" in q) return null // (a path's, not a command)
        val scored = SlashCommands.all(chat).mapNotNull { c -> rank(c, q)?.let { it to c } }
        return SlashView(start, q, scored.sortedBy { it.first }.map { it.second })
    }

    fun row(c: JSONObject): PickRow {
        val about = c.optString("description").trim()
        val also = c.optJSONArray("aliases").texts().joinToString(", ") { "/$it" }
        return PickRow(if (c.optBoolean("builtin")) Adwaita.Claude else Adwaita.UtilitiesTerminal, "/" + c.optString("name"),
            listOf(if (also.isNotEmpty()) Wording.SlashCommands.also(names = also) else "", c.optString("argumentHint"),
                about.lines().first()).filter { it.isNotEmpty() }.joinToString(" · "), about.ifEmpty { null })
    }

    /** The picked command in place of the /word, the caret after it and a space (one that was next goes, so there's
     * one). */
    fun accept(v: TextFieldValue, view: SlashView, name: String): TextFieldValue {
        val caret = v.selection.end
        val end = if (caret < v.text.length && v.text[caret] == ' ') caret + 1 else caret
        val put = "/$name "
        return TextFieldValue(v.text.substring(0, view.start) + put + v.text.substring(end), TextRange(view.start + put.length))
    }
}

// ------------------------------------------------------------------------------------------------- the @ list --

private val REF_STEPS = longArrayOf(3600, 86400, 7 * 86400, 30 * 86400)

/** The @ list over a chat's box (chatinput.ChatRefCompleter, metalook.RefCompleter): every host's chats by their
 * titles, then the names the chat's folder has; ranked as lookup.ref_palette_rank does. */
object MentionList {
    val REF_RE = Regex("""(?<![\w@])@(?:\{([^}\n]+)\}|([\w][\w./:\-]*[\w/]|\w))""")
    private val BARE = Regex("""[\w][\w./:\-]*[\w/]|\w""")

    /** A name as plain @ has it: @name, or @{name} when it has characters a bare one can't. */
    fun plainRef(surface: String) = "@" + if (BARE.matches(surface)) surface else "{$surface}"

    /** The one spelling of a name as written: trimmed, an ellipsised hash without its dots. */
    fun refKey(text: String): String = text.trim().replace(Regex("\\s+"), " ").replace(Regex("(?<=[0-9a-f]{7})(?:…|\\.\\.\\.)$"), "")

    /** Every host's chats by title (a Bot by its name), newest first (the chat's host's first, as they're ranked), then
     * the folder's. */
    fun items(chat: Chat): List<Ref> {
        val at = chat.hostId
        val cwd = Mesh.session(at, chat.sid)?.cwd.orEmpty()
        val chats = Mesh.sessions.flatMap { (h, list) -> list.filter { it.draft != true && !named(it).isNullOrBlank() }.map { h to it } }
            .sortedByDescending { (_, s) -> (s.said ?: s.updated)?.toDouble() ?: 0.0 }.map { (h, s) ->
                val when_ = (s.said ?: s.updated)?.toDouble()
                val folder = s.cwd?.takeIf { it.isNotBlank() }?.let { shortPath(h, it) } ?: Wording.Chats.noFolder
                val where = if (h != at) "${Mesh.label(h)} · $folder" else folder
                Ref(named(s).orEmpty(), null, "", "claude", Wording.Chats.chat,
                    where + (when_?.let { " · " + ago(it.toLong()) } ?: ""), where, when_, false, h != at)
            }
        return chats + Catalogue.items(at, cwd)
    }

    private fun named(s: Msg.Session): String? = chatName(s)

    private fun item(r: Ref): Double? = r.at

    private fun recency(r: Ref, now: Double): Int {
        val w = item(r) ?: return REF_STEPS.size
        val age = now - w
        return REF_STEPS.indexOfFirst { age < it }.let { if (it < 0) REF_STEPS.size else it }
    }

    private fun word(name: String, canon: String, extra: String, w: String): Int? {
        if (name.startsWith(w)) return 0
        if (Regex("(?:^|[./_\\-\\s:])" + Regex.escape(w)).containsMatchIn(name)) return 1
        if (w in name) return 2
        if (canon.isNotEmpty() && (canon.startsWith(w) || w in canon)) return 3
        var from = 0
        if (w.all { ch -> name.indexOf(ch, from).also { if (it >= 0) from = it + 1 } >= 0 }) return 4
        if (w in extra) return 5
        return null
    }

    /** How well a name matches what's typed (lower is better; a list of numbers compared in turn), or null
     * (lookup.ref_palette_rank). */
    private fun rank(r: Ref, q: String, now: Double): List<Double>? {
        val name = (r.label ?: r.surface).lowercase()
        val words = q.split(Regex("\\s+")).filter { it.isNotEmpty() }
        if (words.isEmpty()) {
            val step = recency(r, now)
            return listOf(0.0, 0.0, 0.0, step.toDouble(), if (step < REF_STEPS.size) -item(r)!! else 0.0, name.length.toDouble())
        }
        val canon = r.canonical.lowercase()
        val extra = "${r.where ?: r.summary} ${r.what}".lowercase()
        val ranks = words.map { w -> word(name, canon, extra, w) ?: return null }
        val at = words.map { name.indexOf(it) }
        val inOrder = at.all { it >= 0 } && at == at.sorted()
        return listOf(ranks.max().toDouble(), ranks.sum().toDouble(), if (name == words.joinToString(" ")) 0.0 else 1.0,
            if (inOrder) 0.0 else 1.0, recency(r, now).toDouble(), name.length.toDouble())
    }

    private val BY_RANK = Comparator<List<Double>> { a, b ->
        for (i in 0 until minOf(a.size, b.size)) { val d = a[i].compareTo(b[i]); if (d != 0) return@Comparator d }
        0
    }

    /** The items matching what's typed, best first, and how many matched. */
    fun search(items: List<Ref>, q0: String): Pair<List<Ref>, Int> {
        val q = q0.trim().lowercase()
        val now = System.currentTimeMillis() / 1000.0
        val ranked = items.mapNotNull { r -> rank(r, q, now)?.let { it to r } }
            .sortedWith(compareBy<Pair<List<Double>, Ref>>({ it.second.faint }, { it.second.later }).thenComparing({ it.first }, BY_RANK)
                .thenBy { it.second.surface.lowercase() })
        return ranked.map { it.second } to ranked.size
    }

    /** What's typed after the @ at `at`, while its search is on: (where its @ is, the query), else null. */
    fun word(text: String, at: Int, caret: Int): Pair<Int, String>? {
        if (at < 0 || at >= text.length || caret <= at) return null
        val typed = text.substring(at, caret)
        if (!typed.startsWith("@") || "\n" in typed) return null
        return at to typed.substring(1).trimStart('{')
    }

    fun row(r: Ref) = PickRow(refIcon(r.icon), r.label ?: r.surface, "${r.what} · ${r.summary}")

    /** The names the box colours: every @name an item knows (its surface, or as the catalogue spells it). */
    fun known(items: List<Ref>): (String) -> Boolean {
        val names = HashSet<String>()
        for (r in items) { names.add(r.surface); names.add(refKey(r.surface)) }
        return { it in names }
    }

    /** The picked name in place of the @word, a space after it. */
    fun accept(v: TextFieldValue, from: Int, surface: String): TextFieldValue {
        val caret = v.selection.end
        val end = if (caret < v.text.length && v.text[caret] == ' ') caret + 1 else caret
        val put = plainRef(surface) + " "
        return TextFieldValue(v.text.substring(0, from) + put + v.text.substring(end), TextRange(from + put.length))
    }
}

/** A logo for a kind of name (RefIcon): the catalogue's own aren't drawn here, so a few stand in. */
fun refIcon(name: String): ImageVector = when (name) {
    "claude" -> Adwaita.Claude
    "file" -> Adwaita.TextXGeneric
    "folder" -> Adwaita.Folder
    "git" -> Adwaita.MediaPlaylistShuffle
    else -> Adwaita.UtilitiesTerminal
}

// ---------------------------------------------------------------------------------------------- what's marked --

/** Where a / or an @ was just typed (the marks the desktop's text buffer keeps), shifted by the edits that follow. */
class Marks {
    var typed = -1   // (the last 1-2 character insert with a / in it: where it went, and the / in it)
    var typedIn = 0
    var at = -1      // (the @ that starts the search)
    var dismissed: String? = null // (the / list left with Esc: for what was typed then)

    fun edited(old: String, new: String) {
        if (old == new) return
        var p = 0
        val max = minOf(old.length, new.length)
        while (p < max && old[p] == new[p]) p++
        var s = 0
        while (s < max - p && old[old.length - 1 - s] == new[new.length - 1 - s]) s++
        val removed = old.length - p - s
        val inserted = new.substring(p, new.length - s)
        val delta = inserted.length - removed
        fun shift(m: Int): Int = when {
            m < 0 -> -1
            p > m -> m
            removed == 0 -> if (p == m) m else m + delta // (put in at the mark: it stays left of it)
            p + removed <= m -> m + delta
            else -> -1 // (what it marked is gone)
        }
        val was = typed
        typed = shift(typed).let { if (it < 0 && was >= 0) p else it }
        at = shift(at)
        if ("/" in inserted && inserted.length <= 2) {
            typed = p
            typedIn = inserted.lastIndexOf('/')
        }
        if (inserted == "@" && (p == 0 || new[p - 1] == '\n' || new[p - 1].isWhitespace() || new[p - 1] in "([{\"'")) at = p
    }
}
