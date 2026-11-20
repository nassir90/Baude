package dev.baude.android

import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import androidx.compose.runtime.mutableStateOf
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.LinkedBlockingQueue
import kotlin.concurrent.thread

/** JSON lines with a host process (wire.py's) on a channel of their own: written in turn from a queue, a ping every
 * PING_MS, read in chunks on the thread that runs it, and let go of after SILENT_MS without a word or at a line longer
 * than MAX_LINE. */
class WireLine(private val input: InputStream, private val output: OutputStream, private val close: () -> Unit) {
    private val queue = LinkedBlockingQueue<ByteArray>()
    private val chunk = ByteArray(1 shl 16)
    private var have = 0 // (of chunk: read, not yet taken as a line's)
    private var at = 0

    /** Messages waiting to go out. */
    val waiting: Int get() = queue.size

    /** Till there's room for more to go out (BACKLOG at most waiting), or it's closed: what's passed on through the
     * phone is read no faster than the link it goes on takes it, as mesh.py's tunnels are. */
    fun room() {
        while (queue.size > BACKLOG && !closed) Thread.sleep(10)
    }
    @Volatile private var heard = SystemClock.elapsedRealtime() + START_MS + OPEN_MS
    @Volatile var closed = false
        private set

    fun send(o: JSONObject) {
        if (!closed) checked(o)?.let { queue.put((it.toString() + "\n").toByteArray(Charsets.UTF_8)) }
    }

    fun shut() {
        if (closed) return
        closed = true
        queue.put(ByteArray(0))
        try {
            close()
        } catch (_: Exception) {
        }
    }

    /** Each line read, to take, till the channel ends (pinged: also called at each ping). */
    fun run(pinged: () -> Unit = {}, take: (JSONObject) -> Unit) {
        val writer = thread(name = "wire-out", isDaemon = true) {
            try {
                while (true) {
                    val b = queue.take()
                    if (b.isEmpty()) break
                    output.write(b)
                    if (queue.isEmpty()) output.flush()
                }
            } catch (_: Exception) {
                shut()
            }
        }
        val pinger = thread(name = "wire-ping", isDaemon = true) {
            try {
                while (!closed) {
                    Thread.sleep(PING_MS)
                    send(Msg.Ping().json())
                    pinged()
                    if (SystemClock.elapsedRealtime() - heard > SILENT_MS) shut()
                }
            } catch (_: InterruptedException) {
            }
        }
        try {
            while (!closed) {
                val text = line() ?: break
                heard = SystemClock.elapsedRealtime()
                val o = try {
                    JSONObject(text)
                } catch (_: Exception) {
                    continue // (not a line of the host process's: a shell's greeting, say)
                }
                WireStrict.check("read", o)
                if (o.optString("op") != Op.PING) take(o)
            }
        } catch (_: IOException) {
        } finally {
            shut()
            pinger.interrupt()
            writer.join(1_000)
        }
    }

    private fun line(): String? {
        val buf = ByteArrayOutputStream()
        while (true) {
            if (at == have) {
                have = input.read(chunk)
                at = 0
                if (have < 0) {
                    have = 0
                    return if (buf.size() > 0) buf.toString("UTF-8") else null
                }
                heard = SystemClock.elapsedRealtime() // (a long line coming slowly isn't silence)
            }
            var end = at
            while (end < have && chunk[end] != NL) end++
            buf.write(chunk, at, end - at)
            if (buf.size() > MAX_LINE) throw IOException("a line longer than ${MAX_LINE shr 20} MB")
            if (end < have) {
                at = end + 1
                return buf.toString("UTF-8")
            }
            at = have
        }
    }

    companion object {
        const val MAX_LINE = 48 shl 20 // (a host's longest: a transcript's whole lines, base64'd)
        const val BACKLOG = WireConstants.BACKLOG // (mesh.BACKLOG: messages waiting to go out on a link before more are passed to it)
        private const val NL: Byte = 10
    }
}

/** This phone in the mesh, as a member: a link made from here to a host of yours that's up, as a host process makes
 * one (mesh.py: hello, then join {host} on another "<command> connect" over that host's ssh), one to each mesh the
 * phone reaches (a host of yours whose machine isn't reached through a link already). Its record (who it is, and the
 * hosts it's linked to) is told the mesh, and the records each link brings are passed on to the others, so meshes the
 * phone links to become one, as a host process that links to two joins them. Each tunnel the mesh opens to it is
 * served by PhoneHost as a host process serves one (open, then data both ways, till close); one to another member is
 * passed on, to the link whose side of the mesh reaches that member (route). So every member, and Baude through any
 * of them, reaches the phone's agents, though the phone has no way in of its own. Its record says it isn't to be dialled (dial false:
 * there's no sshd on a phone). */
object PhoneLink {
    val state = mutableStateOf("none") // (none, linking, linked: one link or more joined)
    val via = mutableStateOf<String?>(null) // (the hosts it's linked through, as you named them)
    val why = mutableStateOf<String?>(null) // (why a link isn't, last time)
    private val main = Handler(Looper.getMainLooper())
    private val links = ConcurrentHashMap<String, Link>() // (a host's connection id: the link made over its ssh)
    private val records = ConcurrentHashMap<String, Pair<Msg.Record, Link>>() // (a member: its record, the link it came by
    // first; where a tunnel to it goes is route's)
    private val fails = HashMap<String, Int>()
    private val nextTry = HashMap<String, Long>() // (a host whose link failed: not tried again before this)
    private val pending = java.util.Collections.newSetFromMap(ConcurrentHashMap<String, Boolean>()) // (being linked)
    @Volatile private var seq = 0L

    /** A link: its channel, the host it joined (peer), and its tunnels by number: those the far end opened (ins: t)
     * and those opened from here (outs: the far end's r). */
    private class Link(val conn: Connection, val wire: WireLine) {
        @Volatile var peer: String? = null
        @Volatile var told = false // (its host's records have come: what it reaches is known)
        val ins = ConcurrentHashMap<Int, End>()
        val outs = ConcurrentHashMap<Int, End>()
        val next = java.util.concurrent.atomic.AtomicInteger(1)
    }

    /** A tunnel's end here: served (a Tunnel), or passed on to another link's tunnel (Passed). */
    private interface End {
        fun deliver(msg: JSONObject)
        fun opened() {}
        fun closed(error: String?)
    }

    private class Tunnel(val link: Link, val t: Int, override val origin: String?, override val sender: JSONObject?) : Head(), End {
        override fun send(msg: JSONObject) {
            checked(msg, answer = true)?.let { link.wire.send(Msg.Data(r = t, msg = it).json()) }
        }
        override fun deliver(msg: JSONObject) {
            WireStrict.check("read", msg)
            PhoneHost.take(this, msg)
        }
        override fun closed(error: String?) = PhoneHost.take(this, null)
    }

    /** One end of a tunnel passed through the phone: what comes on it goes out on the other link, as key there
     * ("out" t: one opened from here; "in" r: one the far end opened). */
    private class Passed(val link: Link, val out: Boolean, val n: Int) : End {
        private fun frame(o: JSONObject) = link.wire.send(o.put(if (out) "t" else "r", n))
        override fun deliver(msg: JSONObject) {
            link.wire.room()
            frame(Msg.Data(msg = msg).json())
        }
        override fun opened() = frame(Msg.Opened().json())
        override fun closed(error: String?) {
            (if (out) link.outs else link.ins).remove(n)
            frame(Msg.Close(error = error).json())
        }
    }

    /** (main thread: a host's connection changed) A link to each of your hosts that's up and isn't reached through
     * one already: one at a time, so the first one's records say what it reaches before another's made. */
    fun look() {
        // (one joining: what it reaches known first)
        if (pending.isNotEmpty() || links.values.any { it.peer == null || !it.told }) return
        val reached = reachable()
        val now = SystemClock.elapsedRealtime()
        val conn = Mesh.hosts.firstNotNullOfOrNull { h -> // (one that refused, or failed, lately: past it, to the next)
            Mesh.conn(h.id)?.takeIf { it.state == "up" && !links.containsKey(it.id) && it.info?.id !in reached &&
                (nextTry[it.id] ?: 0L) <= now }
        } ?: return
        pending.add(conn.id)
        shown()
        thread(name = "phone-link", isDaemon = true) { // (off the main thread: a channel's opened over the network)
            var said: String? = null
            var link: Link? = null
            try {
                val ch = conn.exec() ?: throw Exception("${conn.host?.label ?: "the host"} isn't connected")
                link = Link(conn, WireLine(ch.inputStream, ch.outputStream) { ch.disconnect() })
                links[conn.id] = link
                pending.remove(conn.id) // (joining now: links has it)
                try {
                    ch.connect(15_000)
                    run(link)
                } finally {
                    ch.disconnect()
                }
            } catch (e: Exception) {
                said = e.message ?: e.javaClass.simpleName
            } finally {
                pending.remove(conn.id)
                link?.let { dropped(it) }
            }
            main.post {
                val joined = link?.peer != null
                why.value = said ?: if (joined) "the link to ${conn.host?.label ?: "its host"} dropped" else why.value
                val n = if (joined) 0 else (fails[conn.id] ?: 0) + 1
                fails[conn.id] = n
                val wait = RETRY_MS[minOf(n, RETRY_MS.size - 1)]
                nextTry[conn.id] = if (joined) 0L else SystemClock.elapsedRealtime() + wait
                shown()
                look() // (another host, at once, if there's one to try)
                main.postDelayed({ look() }, wait)
            }
        }
    }

    /** One request to a member's host process on a tunnel of its own, opened from here as from sender (a chat of
     * this phone's: what's sent through it is from that chat, as the far host stamps it: wire.py's Senders): its reply,
     * or an error one if it didn't answer; null if there's no way to it (no link of the phone's reaches it, or its
     * tunnel didn't open). */
    fun request(to: String, sender: JSONObject?, msg: JSONObject, ms: Long = 30_000): JSONObject? {
        val asked = JSONObject(msg.toString()).put("req", 1)
        ownRefusal(asked)?.let { return it }
        val link = route(to) ?: return null
        val n = link.next.getAndIncrement()
        val end = Asking()
        link.outs[n] = end
        link.wire.send(Msg.Open(t = n, to = to, ttl = WireConstants.TTL, from = PhoneHost.id,
            sender = sender?.let { Msg.Sender.from(it) }).json())
        try {
            val error = try {
                end.ready.get(OPEN_MS, java.util.concurrent.TimeUnit.MILLISECONDS)
            } catch (_: Exception) {
                "it didn't answer"
            }
            if (error != null) return null
            link.wire.send(Msg.Data(t = n, msg = checked(asked) ?: asked).json())
            return try {
                end.reply.get(ms, java.util.concurrent.TimeUnit.MILLISECONDS)
            } catch (_: Exception) {
                Msg.Error(error = "it didn't answer").json()
            }
        } finally {
            if (link.outs.remove(n) != null) link.wire.send(Msg.Close(t = n).json())
        }
    }

    /** A tunnel's end opened from here for one request: ready once it's open (null) or closed (why), reply its
     * answer. */
    private class Asking : End {
        val ready = java.util.concurrent.CompletableFuture<String?>()
        val reply = java.util.concurrent.CompletableFuture<JSONObject>()
        override fun deliver(msg: JSONObject) {
            WireStrict.check("read", msg)
            if (msg.optString("op") != Op.PING) reply.complete(msg)
        }
        override fun opened() {
            ready.complete(null)
        }
        override fun closed(error: String?) {
            ready.complete(error ?: "the tunnel was closed")
            reply.complete(Msg.Error(error = error ?: "the tunnel was closed").json())
        }
    }

    /** A member's name, as its record says it (its id's first 8 if there's none). */
    fun nameOf(member: String): String = records[member]?.first?.info?.name?.ifBlank { null }
        ?: Mesh.states.values.toList().firstNotNullOfOrNull { c -> c.info?.takeIf { it.id == member } }?.name?.ifBlank { null }
        ?: member.take(8)

    /** The members its links reach now, as a host process works it out (mesh.Mesh.reroute): breadth first from the
     * hosts it's linked to, through links both ends' records list. (A record a host still has of one that's gone,
     * kept a while, isn't one reached.) */
    private fun reachable(): Set<String> = links.values.mapNotNull { it.peer }.flatMap { hops(it).keys }.toSet()

    /** The members reached from a host (not back through this phone), each by how many links away. */
    private fun hops(from: String): Map<String, Int> {
        fun listed(u: String) = records[u]?.first?.links.orEmpty()
        val seen = hashMapOf(from to 0)
        var frontier = listOf(from)
        var n = 0
        while (frontier.isNotEmpty()) {
            n++
            frontier = frontier.flatMap { u -> listed(u).filter { v -> v != PhoneHost.id && v !in seen && u in listed(v) } }
                .distinct().onEach { seen[it] = n }
        }
        return seen
    }

    /** The link a tunnel to a member goes on: the one whose host reaches it in fewest links (null: none does). */
    private fun route(to: String): Link? = links.values.filter { it.peer != null }
        .mapNotNull { l -> hops(l.peer!!)[to]?.let { l to it } }.minByOrNull { it.second }?.first

    private fun shown() = main.post {
        val up = links.values.filter { it.peer != null }
        state.value = if (up.isNotEmpty()) "linked" else if (links.isNotEmpty() || pending.isNotEmpty()) "linking" else "none"
        via.value = (up.ifEmpty { links.values.toList() }).mapNotNull { it.conn.host?.label }.joinToString(", ").ifBlank { null }
        if (up.isNotEmpty()) why.value = null
    }

    private fun hello() = Msg.HostHello(id = PhoneHost.id, name = PhoneHost.name, group = PhoneHost.name,
        host = PhoneHost.name, dial = false)

    /** Its record, as the mesh has a member's: who it is, its providers (none: it serves no models), its links. */
    private fun record() = Msg.Record(id = PhoneHost.id, seq = seq, info = Msg.MemberInfo(id = PhoneHost.id,
        name = PhoneHost.name, group = PhoneHost.name, host = PhoneHost.name, dial = false, providers = emptyList()),
        links = links.values.mapNotNull { it.peer }.distinct().sorted())

    /** Its record, anew (its links changed), to every link. */
    private fun publish() = synchronized(this) { // (its seq and what's sent with it: one telling at a time, in order)
        seq = maxOf(seq + 1, System.currentTimeMillis() * 1_000_000)
        val mine = record()
        for (l in links.values) if (l.peer != null) l.wire.send(Msg.MeshFrame(records = listOf(mine)).json())
    }

    private fun Number?.whole(): Long? = (this as? Int)?.toLong() ?: this as? Long

    /** A member's record as it came, if it's one, cut to size (mesh._record): {id, seq, info, links}, info a host's
     * hello (_hello_of) with its providers as the mesh is told of them (published). */
    private fun cut(o: JSONObject): Msg.Record? {
        val r = Msg.Record.from(o)
        val id = r.id?.takeIf(::isId) ?: return null
        val seq = (o.opt("seq") as? Number).whole() ?: return null
        val raw = o.optJSONObject("info") ?: return null
        val i = Msg.MemberInfo.from(raw)
        if (i.id != id) return null
        fun text(k: String, max: Int) = (raw.opt(k) as? String)?.take(max)
        val providers = raw.optJSONArray("providers")?.let { a ->
            (0 until minOf(a.length(), 32)).mapNotNull { a.optJSONObject(it) }.filter { isProvider(it.opt("id")) }.map { p ->
                val m = Msg.Provider.from(p)
                Msg.Provider(id = m.id, kind = (m.kind?.ifEmpty { null } ?: "openai").take(32),
                    name = (m.name?.ifEmpty { null } ?: m.id!!).take(64), http = p.opt("http") == true,
                    models = p.optJSONArray("models")?.let { ms ->
                        (0 until minOf(ms.length(), 200)).mapNotNull { ms.optJSONObject(it) }.mapNotNull { model(it) } }.orEmpty())
            }
        }.orEmpty()
        val info = Msg.MemberInfo(id = id, name = (text("name", 64)?.ifEmpty { null } ?: id.take(8)),
            group = text("group", 64).orEmpty(), host = text("host", 255).orEmpty(), version = (i.version.whole() ?: 0L),
            bonjour = text("bonjour", 255), dial = if (raw.opt("dial") == false) false else null, providers = providers)
        return Msg.Record(id = id, seq = seq, info = info, links = (r.links.orEmpty().take(256).filter(::isId).toSortedSet().toList()))
    }

    /** A model as the mesh is told of it (mesh._model). */
    private fun model(o: JSONObject): Msg.ProviderModel? {
        val v = o.opt("value") as? String ?: return null
        return Msg.ProviderModel(value = v.take(200), displayName = ((o.opt("displayName") as? String)?.ifEmpty { null } ?: v).take(200),
            id = (o.opt("id") as? String)?.take(200), tools = o.opt("tools") as? Boolean, thinking = o.opt("thinking") as? Boolean,
            window = (o.opt("window") as? Number).whole(), version = (o.opt("version") as? String)?.take(32), digest = (o.opt("digest") as? String)?.take(80),
            input = o.optJSONArray("input")?.let { a -> (0 until minOf(a.length(), 8)).mapNotNull { a.opt(it) as? String }.map { it.take(32) } })
    }

    private fun isProvider(v: Any?) = v is String && !v.contains("..") &&
        Regex("[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}(/[A-Za-z0-9][A-Za-z0-9_.:-]{0,63})?").matches(v)

    /** One link, till it drops. */
    private fun run(link: Link) {
        val wire = link.wire
        wire.send(Msg.Hello(client = "baude-android", req = 0).json())
        // (a head through a tunnel hears from the phone at least as often as its link's pinged)
        wire.run(pinged = { for (e in link.ins.values) if (e is Tunnel) e.send(Msg.Ping().json()) }) { f ->
            if (link.peer != null) return@run frame(link, f)
            when (f.optString("op")) {
                Op.HELLO -> wire.send(Msg.Join(req = 1, host = hello()).json())
                Op.JOINED -> {
                    link.peer = Msg.Joined.from(f).host?.id?.takeIf(::isId) ?: throw Exception("the host gave no id")
                    publish()
                    val known = records.values.map { it.first } // (what the other links reach: this one told)
                    if (known.isNotEmpty()) wire.send(Msg.MeshFrame(records = known).json())
                    shown()
                }
                Op.ERROR -> throw Exception(Msg.Error.from(f).said ?: "the host said no")
            }
        }
    }

    /** A link gone: its tunnels closed (those passed on, at their other end too), what came by it forgotten, and the
     * mesh told this phone's links now. */
    private fun dropped(link: Link) {
        link.wire.shut()
        links.remove(link.conn.id)
        val still = reachable() // (what another link reaches: kept, and passed on by it from now on)
        records.entries.removeAll { it.value.second === link && it.key !in still }
        for ((id, v) in records.entries.toList()) if (v.second === link) route(id)?.let { records[id] = v.first to it }
        for (e in link.ins.values + link.outs.values) e.closed("the phone's link to ${link.conn.host?.label ?: "a host"} dropped")
        link.ins.clear()
        link.outs.clear()
        publish()
    }

    /** A frame on a link (mesh.py's): records, a tunnel opened to here or through here, a tunnel's message or end. */
    private fun frame(link: Link, f: JSONObject) {
        when (f.optString("op")) {
            Op.MESH -> {
                val new = ArrayList<Msg.Record>()
                val came = f.optJSONArray("records")
                for (r in (0 until (came?.length() ?: 0)).mapNotNull { came!!.optJSONObject(it)?.let(::cut) }) {
                    val id = r.id!!
                    val at = r.seq!!.toLong()
                    if (id == PhoneHost.id) { // (this phone's own, from before a restart: its record told anew, newer)
                        synchronized(this) {
                            if (at > seq) {
                                seq = at
                                publish()
                            }
                        }
                        continue
                    }
                    val have = records[id]
                    if (have == null || at > have.first.seq!!.toLong()) {
                        records[id] = r to (have?.second?.takeIf { links.containsValue(it) } ?: link) // (the link it
                        // came by first: not passed back on it)
                        new.add(r)
                    }
                }
                if (new.isNotEmpty()) for (l in links.values) if (l !== link && l.peer != null) // (passed on: not back)
                    l.wire.send(Msg.MeshFrame(records = new).json())
                if (!link.told) { // (what this host reaches known now: another mesh's host linked to, if there's one)
                    link.told = true
                    main.post { look() }
                }
            }
            Op.OPEN -> {
                val o = Msg.Open.from(f)
                val t = o.t?.toInt()?.takeIf { it >= 0 } ?: return
                link.ins.remove(t)?.closed(null) // (a number used again: the tunnel that had it is gone)
                val to = o.to.orEmpty()
                // (who what's sent through it is from: the member it came from, and the chat its relay named)
                val sender = o.sender?.let { PhoneHost.cleanSender(it.json()) }
                if (to == PhoneHost.id) {
                    link.ins[t] = Tunnel(link, t, o.from?.takeIf(::isId), sender)
                    link.wire.send(Msg.Opened(r = t).json())
                    return
                }
                val ahead = route(to)?.takeIf { it !== link }
                val ttl = o.ttl?.toInt() ?: 0
                if (ahead == null || ttl <= 1) {
                    link.wire.send(Msg.Close(r = t, error = "${PhoneHost.name} has no way to it").json())
                    return
                }
                val n = ahead.next.getAndIncrement()
                link.ins[t] = Passed(ahead, true, n)
                ahead.outs[n] = Passed(link, false, t)
                ahead.wire.send(Msg.Open(t = n, to = to, ttl = ttl - 1, from = o.from?.takeIf(::isId),
                    sender = sender?.let { Msg.Sender.from(it) }).json())
            }
            Op.OPENED -> link.outs[Msg.Opened.from(f).r?.toInt() ?: -1]?.opened()
            Op.DATA -> {
                val d = Msg.Data.from(f)
                val end = if (d.t != null) link.ins[d.t.toInt()] else link.outs[d.r?.toInt() ?: -1]
                d.msg?.let { end?.deliver(it) }
            }
            Op.CLOSE -> {
                val c = Msg.Close.from(f)
                val end = if (c.t != null) link.ins.remove(c.t.toInt()) else link.outs.remove(c.r?.toInt() ?: -1)
                end?.closed(c.error)
            }
        }
    }

    private val RETRY_MS = longArrayOf(3_000, 10_000, 30_000, 120_000)
}
