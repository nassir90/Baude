package dev.baude.android

import android.os.Handler
import android.os.Looper
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf

/** Where a chat is, for the links in what's said: its host, its folder, the host's home and what it published. */
data class ChatPlace(val hostId: String, val cwd: String?, val home: String?, val artifacts: List<Artifact>)

val LocalChatPlace = compositionLocalOf<ChatPlace?> { null }

/** A file or folder named in a chat that exists: its absolute path and whether it's a folder. */
data class Hit(val path: String, val dir: Boolean)

/** Which paths exist on a chat's host, from its folders' listings (list_dir), each asked for once and kept for 30 s.
 * version changes as an answer comes: what's drawn from it is drawn again. */
object PathCheck {
    val version = mutableIntStateOf(0)
    private class Listing(val names: Set<String>, val dirs: Set<String>, val more: Boolean, val at: Long)

    private const val KEPT_MS = 30_000L
    private val main = Handler(Looper.getMainLooper())
    private val cache = HashMap<String, Listing>()
    private val asking = HashSet<String>()

    /** raw as an absolute path on the place's host (~ is its home, a relative one is from the chat's folder). */
    fun absolute(place: ChatPlace, raw: String): String? {
        var p = raw
        if (p == "~" || p.startsWith("~/")) p = (place.home ?: return null).trimEnd('/') + p.drop(1)
        if (!p.startsWith("/")) p = (place.cwd ?: return null).trimEnd('/') + "/" + p
        return try {
            java.nio.file.Paths.get(p).normalize().toString().ifEmpty { "/" }
        } catch (_: Exception) {
            null
        }
    }

    /** The file or folder raw names, if it's known to exist; null while that's not known or if it doesn't. */
    fun find(place: ChatPlace, raw: String): Hit? {
        version.intValue
        if (!takes(place.hostId, "list_dir")) return null
        val path = absolute(place, raw) ?: return null
        val folder = path.substringBeforeLast('/').ifEmpty { "/" }
        val name = path.substringAfterLast('/')
        if (name.isEmpty()) return null
        val key = place.hostId + "\u0000" + folder
        val got = cache[key]
        if (got == null || android.os.SystemClock.elapsedRealtime() - got.at > KEPT_MS) ask(place.hostId, folder, key)
        if (got == null || name !in got.names) return null
        return Hit(path, name in got.dirs)
    }

    private fun ask(hostId: String, folder: String, key: String) {
        if (!asking.add(key)) return
        main.post {
            Mesh.ask(hostId, Msg.ListDir(path = folder).json()) { r ->
                main.post {
                    asking.remove(key)
                    val d = Msg.Dir.from(r)
                    val entries = if (r.optString("op") == Op.DIR && d.error.isNullOrBlank()) d.entries.orEmpty() else emptyList()
                    val was = cache[key]
                    val now = Listing(entries.mapNotNull { it.name }.toSet(), entries.filter { it.dir == true }.mapNotNull { it.name }.toSet(),
                        d.more == true, android.os.SystemClock.elapsedRealtime())
                    cache[key] = now
                    if (was == null || was.names != now.names || was.dirs != now.dirs) version.intValue++
                }
            }
        }
    }
}

/** What a tapped file link asks of the Files page. */
class Wanted(val hostId: String, val path: String, val line: Int?, val dir: Boolean)

/** A tapped link to a file or folder: the Files page shows it. */
object FileLinks {
    val wanted = mutableStateOf<Wanted?>(null)
    var showFiles: () -> Unit = {}

    fun open(w: Wanted) {
        wanted.value = w
        showFiles()
    }

    /** A [link](target)'s file: its absolute path, and its line (#L12, #L12-L20 or :12 after it). */
    fun resolve(place: ChatPlace, target: String): Pair<String, Int?>? {
        val m = Regex("""(?:file://)?(.+?)(?:#L?(\d+)(?:-L?\d+)?|:(\d+)(?::\d+)?)?""").matchEntire(target) ?: return null
        val line = (m.groups[2]?.value ?: m.groups[3]?.value)?.toIntOrNull()
        val raw = try {
            java.net.URLDecoder.decode(m.groupValues[1].replace("+", "%2B"), "UTF-8")
        } catch (_: Exception) {
            m.groupValues[1]
        }
        return PathCheck.absolute(place, raw)?.let { it to line }
    }
}
