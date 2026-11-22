package dev.baude.android

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import org.json.JSONObject
import java.util.Base64

/** A terminal a host runs for the phone (the terminal op, baudelib/hostterminal.py), on a connection of its own to
 * the host: argv run there (its login shell with none), its bytes drawn here by a TermEmulator, what's typed here sent
 * back. Letting go of the connection (the app put away, the page left) leaves the program running there a while
 * (TERMINAL_KEEP_S); open() again attaches to it, its recent output drawn again first. close() ends it. All on the
 * main thread. */
class HostTerm(val hostId: String, private val argv: List<String>? = null, private val named: String? = null,
               attachTo: String? = null) {
    val screen = TermEmulator(80, 24) { send(it) }
    /** Its id on the host, once it's said (attach's, after). */
    var id: String? = attachTo; private set
    var title by mutableStateOf(named.orEmpty())
    /** Its program's exit status, once it's ended. */
    var ended by mutableStateOf<Int?>(null)
    /** Why there's no terminal (an error the host said, or no connection). */
    var said by mutableStateOf<String?>(null)
    /** Bumped as what's shown changes (TermView draws again). */
    var drawn by mutableLongStateOf(0L)
    /** Ctrl held from the keys row: the next key typed is sent with it. */
    var ctrl by mutableStateOf(false)
    /** While a page shows it: open again when the app's back on screen. */
    var wanted = false
    var onEnd: (Int) -> Unit = {}
    var onOpened: (String) -> Unit = {}
    private var conn: Connection? = null
    private var closed = false
    private var gen = 0

    val open get() = conn != null

    /** Its connection made (if it isn't), the terminal started there, or attached to again: whether there's a host to
     * reach. */
    fun open(): Boolean {
        if (closed || ended != null) return true
        if (conn != null) return true
        val g = ++gen
        conn = Mesh.watch(hostId, { _, m -> heard(g, m) }) { c ->
            if (g != gen) return@watch
            if (c.state == "up") c.send(request().json())
        }
        if (conn == null) said = Connection.NOT_CONNECTED
        return conn != null
    }

    private fun request() = id?.let { Msg.Terminal(req = REQ, attach = it, columns = screen.columns, rows = screen.rows) }
        ?: Msg.Terminal(req = REQ, argv = argv, title = named, columns = screen.columns, rows = screen.rows)

    /** The connection let go of: the program goes on there, to be attached to again. */
    fun leave() {
        gen++
        conn?.stop()
        conn = null
    }

    /** Ended: its program hung up (as a terminal closing does), the connection let go of a moment later. */
    fun close() {
        if (closed) return
        closed = true
        val c = conn
        gen++
        conn = null
        if (c != null) {
            c.send(Msg.TerminalClose(req = REQ).json())
            Mesh.later(1_000) { c.stop() }
        } else if (id != null && ended == null) { // (let go of already, running there: attached to, to end it)
            val there = id
            Mesh.watch(hostId, { _, _ -> }) { w ->
                if (w.state != "up") return@watch
                w.send(Msg.Terminal(req = REQ, attach = there).json())
                w.send(Msg.TerminalClose(req = REQ).json())
                Mesh.later(1_000) { w.stop() }
            }
        }
        Terms.forget(this)
    }

    /** (on its connection's thread) What the host says of it. */
    private fun heard(g: Int, m: JSONObject) {
        if (m.optInt("req", -1) != REQ) return
        when (m.optString("op")) {
            Op.TERMINAL_OPENED -> Msg.TerminalOpened.from(m).let { o -> Mesh.post { if (g == gen) opened(o) } }
            Op.TERMINAL_OUT -> Msg.TerminalOut.from(m).data?.let { d ->
                val bytes = try { Base64.getDecoder().decode(d) } catch (_: IllegalArgumentException) { return }
                Mesh.post { if (g == gen) feed(bytes) }
            }
            Op.TERMINAL_END -> Msg.TerminalEnd.from(m).let { e -> Mesh.post { if (g == gen) end(e) } }
            Op.ERROR -> Msg.Error.from(m).said.orEmpty().let { e -> Mesh.post { if (g == gen) failed(e) } }
        }
    }

    private fun opened(o: Msg.TerminalOpened) {
        val again = id != null
        id = o.id
        said = null
        if (again) screen.reset() // (what it wrote lately comes again: drawn from the start)
        if (title.isEmpty() || named == null) title = o.title.orEmpty()
        conn?.send(Msg.TerminalResize(req = REQ, columns = screen.columns, rows = screen.rows).json()) // (laid out meanwhile)
        o.id?.let(onOpened)
    }

    /** Bytes the program wrote, drawn. */
    fun feed(bytes: ByteArray) {
        screen.feed(bytes)
        screen.title?.let { if (it.isNotBlank()) title = it }
        drawn = screen.changes
    }

    private fun end(e: Msg.TerminalEnd) {
        val code = e.status?.toInt() ?: -1
        ended = code
        if (!e.said.isNullOrBlank()) feed("\r\n${Wording.Multiplex.notStarted(e.said)}\r\n".toByteArray())
        leave()
        onEnd(code)
    }

    private fun failed(e: String) {
        if (id != null && e.startsWith("no terminal")) { // (gone from the host meanwhile: a new one, if it's one of ours)
            id = null
            if (argv == null) { conn?.send(request().json()); return }
            ended = -1
            leave()
            onEnd(-1)
            return
        }
        said = e
    }

    /** What's typed: sent to the program as it is, Ctrl'd first if the keys row's Ctrl is held. */
    fun type(text: String) {
        if (text.isEmpty()) return
        if (ctrl) {
            ctrl = false
            val cp = text.codePointAt(0)
            val rest = text.substring(Character.charCount(cp))
            send((controlled(cp)?.let { byteArrayOf(it.toByte()) } ?: text.substring(0, Character.charCount(cp)).toByteArray()) +
                rest.toByteArray())
            return
        }
        send(text.toByteArray(Charsets.UTF_8))
    }

    fun send(bytes: ByteArray) {
        if (bytes.isEmpty() || ended != null) return
        conn?.send(Msg.TerminalIn(req = REQ, data = Base64.getEncoder().encodeToString(bytes)).json())
    }

    /** A new size, from the page it's laid out in: the screen's, and the program's (SIGWINCH there). */
    fun resize(columns: Int, rows: Int) {
        if (columns == screen.columns && rows == screen.rows) return
        screen.resize(columns, rows)
        drawn = screen.changes
        if (id != null) conn?.send(Msg.TerminalResize(req = REQ, columns = columns, rows = rows).json())
    }

    companion object {
        const val REQ = 1 // (the terminal's own: no request of the connection's is numbered so)

        /** The control character Ctrl and c make (Ctrl+C: 3), or null for one it doesn't change. */
        fun controlled(c: Int): Int? = when (c) {
            in 'a'.code..'z'.code -> c - 'a'.code + 1
            in '@'.code..'_'.code -> c - '@'.code
            ' '.code, '2'.code -> 0
            '3'.code -> 27; '4'.code -> 28; '5'.code -> 29; '6'.code -> 30; '7'.code, '/'.code -> 31
            '8'.code, '?'.code -> 127
            else -> null
        }
    }
}

/** The terminals open on this phone: the plain one each host's Terminal page has, and the pod shells; let go of as the
 * app goes off screen, attached to again as it comes back. */
object Terms {
    val live = mutableListOf<HostTerm>()
    private val plain = mutableMapOf<String, HostTerm>()

    /** The host's Terminal page's terminal: the one this phone had there, attached to again if it's still running
     * (kept in the settings: the app may have gone since), or a new one (the host's login shell). */
    fun plain(hostId: String): HostTerm {
        plain[hostId]?.let { if (it.ended == null) return it else forget(it) }
        val prefs = Mesh.app.getSharedPreferences("settings", android.content.Context.MODE_PRIVATE)
        val key = "terminal.$hostId"
        return HostTerm(hostId, attachTo = prefs.getString(key, null)).also { t ->
            t.onOpened = { prefs.edit().putString(key, it).apply() }
            t.onEnd = { prefs.edit().remove(key).apply() }
            plain[hostId] = t
            add(t)
        }
    }

    fun add(t: HostTerm) {
        if (t !in live) live.add(t)
    }

    fun forget(t: HostTerm) {
        live.remove(t)
        if (plain.entries.removeAll { it.value === t })
            Mesh.app.getSharedPreferences("settings", android.content.Context.MODE_PRIVATE).edit().remove("terminal.${t.hostId}").apply()
    }

    /** The app on screen or not (MainActivity's start and stop): each terminal's connection let go of, or made again
     * for those a page shows. */
    fun onScreen(now: Boolean) {
        for (t in live.toList()) if (now) { if (t.wanted) t.open() } else t.leave()
    }
}
