package dev.baude.android

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.Rect
import android.graphics.Shader
import android.graphics.Typeface
import android.net.Uri
import android.os.Handler
import android.os.Looper
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.unit.dp
import androidx.core.app.Person
import androidx.core.content.IntentCompat
import androidx.core.content.pm.ShortcutInfoCompat
import androidx.core.content.pm.ShortcutManagerCompat
import androidx.core.graphics.drawable.IconCompat

/** What another app shared: its text (a link's too), and its files. */
class Shared(val text: String?, val uris: List<Uri>)

/** Sharing from other apps, as a messaging app takes it: your Bots in the system's share sheet (each a sharing
 * shortcut, its picture or initials its icon, the most recently active first; in the launcher's long-press menu too),
 * and what's shared put in the chat it's sent to, ready to send: its text in the box, its files as the chat's own
 * attachments. The share sheet's plain Baude has you choose the chat on the home screen. */
object Shares {
    const val CATEGORY = "dev.baude.android.category.BOT" // (res/xml/shortcuts.xml's share-target)
    private val main = Handler(Looper.getMainLooper())

    /** What's shared, while you choose the chat it goes to. */
    val pending = mutableStateOf<Shared?>(null)

    /** The chat open was opened by sharing to it: back from it goes back to the app it was shared from. */
    var returns = false

    private var started = false // (this process has had the activity made: one made again after it isn't)
    private val io = java.util.concurrent.Executors.newSingleThreadExecutor()
    private var written: Pair<String, List<Picked>>? = null // (the open chat and its files as last kept for the app
    private var wrote = false                                  // going: none, this process, till it's first kept)
    private val published = HashMap<String, String>() // (a shortcut's id: what it was published as, this run)
    private var looking = false

    /** A shared item read from what the share sheet sent, and the Bot it was sent to (its shortcut's host and session),
     * if it was one; null if it isn't a share. Opened again from Recents: not shared again. */
    fun from(intent: Intent): Pair<Shared, Pair<String, String>?>? {
        val action = intent.action
        if (action != Intent.ACTION_SEND && action != Intent.ACTION_SEND_MULTIPLE) return null
        if (intent.flags and Intent.FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY != 0) return null
        val uris = ArrayList<Uri>()
        if (action == Intent.ACTION_SEND) IntentCompat.getParcelableExtra(intent, Intent.EXTRA_STREAM, Uri::class.java)?.let(uris::add)
        else IntentCompat.getParcelableArrayListExtra(intent, Intent.EXTRA_STREAM, Uri::class.java)?.let(uris::addAll)
        if (uris.isEmpty()) intent.clipData?.let { clip -> // (some apps give them only so)
            for (i in 0 until clip.itemCount) clip.getItemAt(i).uri?.let(uris::add)
        }
        val text = (intent.getCharSequenceArrayListExtra(Intent.EXTRA_TEXT)?.joinToString("\n")
            ?: intent.getCharSequenceExtra(Intent.EXTRA_TEXT)?.toString())?.trim()?.ifBlank { null }
            ?: intent.getStringExtra(Intent.EXTRA_SUBJECT)?.trim()?.ifBlank { null }?.takeIf { uris.isEmpty() }
        if (text == null && uris.isEmpty()) return null
        val to = intent.getStringExtra(ShortcutManagerCompat.EXTRA_SHORTCUT_ID)?.let { sid ->
            hostOf(sid)?.let { it to sid }
        }
        return Shared(text, uris) to to
    }

    /** What's shared put in the chat: its text after what's in the box, its files read and attached. */
    fun put(ctx: Context, chat: Chat, shared: Shared) {
        shared.text?.let { chat.draft.value = listOfNotNull(chat.draft.value, it).joinToString("\n") }
        if (shared.uris.isNotEmpty()) take(ctx, chat, shared.uris)
    }

    /** A message sent to a Bot: its shortcut ranked by it, as the share sheet ranks a messaging app's contacts. */
    fun used(chat: Chat) {
        if (Mesh.session(chat.hostId, chat.sid)?.bot == null) return
        try {
            ShortcutManagerCompat.reportShortcutUsed(Mesh.app, chat.sid)
        } catch (_: Exception) {
        }
    }

    private fun key(hostId: String, sid: String) = "$hostId/$sid"

    /** The host a Bot's chat (a shortcut's id: its session's) is on: where it's listed, or where it was last
     * published from (kept: a share sheet's pick comes before its host's reached). */
    private fun hostOf(sid: String): String? = Mesh.contacts.firstOrNull { it.id == sid }?.hostId
        ?: Mesh.app.getSharedPreferences("shares", Context.MODE_PRIVATE).getString(sid, null)

    /** (the activity put away: the app may go while it is) What's shared and not sent yet, kept for if it's made
     * again: a share waiting for its chat, whether the chat open goes back to the app it came from, and the files
     * attached in it (kept in the app's cache, as they're not the box's). */
    fun save(ctx: Context, out: android.os.Bundle) {
        out.putBoolean("share:returns", returns)
        pending.value?.let {
            out.putString("share:text", it.text)
            out.putParcelableArrayList("share:uris", ArrayList(it.uris))
        }
        val chat = Mesh.open
        if (chat != null) {
            out.putString("share:chat", key(chat.hostId, chat.sid))
            chat.draft.value?.let { out.putString("share:draft", it) }
        }
        val now = chat?.let { key(it.hostId, it.sid) to it.files.toList() }
        if (wrote && now == written) return
        wrote = true
        written = now
        val dir = java.io.File(ctx.cacheDir, "attached")
        io.execute {
            dir.deleteRecursively() // (none open: none kept)
            val (chatKey, files) = now ?: return@execute
            dir.mkdirs()
            files.forEachIndexed { i, f -> java.io.File(dir, "$i").writeBytes(f.bytes) }
            java.io.File(dir, "files.json").writeText(org.json.JSONArray(files.map {
                org.json.JSONObject().put("name", it.name).put("mime", it.mime) }).toString())
            java.io.File(dir, "chat").writeText(chatKey)
        }
    }

    /** (the activity made) If it's made again in a new process, what save kept put back: the chat open (screen: its
     * host and session) its files and box's text again. */
    fun restore(ctx: Context, saved: android.os.Bundle?, screen: List<String>) {
        val again = saved != null && !started
        started = true
        if (!again) return
        returns = saved!!.getBoolean("share:returns")
        androidx.core.os.BundleCompat.getParcelableArrayList(saved, "share:uris", Uri::class.java)?.let { uris ->
            pending.value = Shared(saved.getString("share:text"), uris)
        } ?: saved.getString("share:text")?.let { pending.value = Shared(it, emptyList()) }
        if (screen[0] != "chat" || saved.getString("share:chat") != key(screen[1], screen[2])) return
        val chat = Mesh.openChat(screen[1], screen[2])
        saved.getString("share:draft")?.let { chat.draft.value = it }
        val dir = java.io.File(ctx.cacheDir, "attached")
        val main = Handler(Looper.getMainLooper())
        io.execute {
            val files = try {
                if (java.io.File(dir, "chat").readText() != key(screen[1], screen[2])) return@execute
                val a = org.json.JSONArray(java.io.File(dir, "files.json").readText())
                (0 until a.length()).map { i -> a.getJSONObject(i).let {
                    Picked(it.optString("name"), it.optString("mime"), java.io.File(dir, "$i").readBytes()) } }
            } catch (_: Exception) {
                return@execute
            }
            main.post {
                if (Mesh.open === chat && chat.files.isEmpty()) {
                    chat.files.addAll(files)
                    written = key(chat.hostId, chat.sid) to chat.files.toList()
                    wrote = true
                }
            }
        }
    }

    /** (a host listed its sessions, a picture came, a host went) The shortcuts published again, shortly, if they've
     * changed. */
    fun look() {
        if (looking) return
        looking = true
        main.postDelayed({
            looking = false
            try {
                publish()
            } catch (e: Exception) { // (the launcher's or the system's say: tried again at the next change)
                android.util.Log.w("Shares", "shortcuts", e)
            }
        }, 1_500)
    }

    /** The Bots most recently active (as many as the launcher takes) each a sharing shortcut, published where it's
     * new, its name or picture changed, or the launcher's let go of it. One whose host hasn't listed its Bots yet is
     * kept (so the share sheet has it whether or not it's connected) in what room the live ones leave; one that's no
     * longer among the latest taken away, and one that's no longer a Bot (deleted, archived, made a chat again), or
     * whose host you've removed, disabled too, so a pinned one doesn't open an empty chat. */
    private fun publish() {
        val app = Mesh.app
        val most = ShortcutManagerCompat.getMaxShortcutCountPerActivity(app).coerceAtLeast(1)
        val existing = ShortcutManagerCompat.getDynamicShortcuts(app)
        published.keys.retainAll(existing.map { it.id }.toSet())
        val bots = Mesh.contacts.take(most)
        val wanted = bots.map { it.id }.toSet()
        val listed = Mesh.sessions.keys
        val settled = Mesh.hosts.all { Mesh.conn(it.id)?.state == "up" } // (the mesh as your hosts list it, whole)
        val hosts = app.getSharedPreferences("shares", Context.MODE_PRIVATE)
        fun gone(id: String): Boolean { // (no longer a Bot of yours: not listed anywhere, and its host has listed)
            if (Mesh.contacts.any { it.id == id }) return false
            val host = hostOf(id).orEmpty()
            return host in listed || host != "phone" && !host.startsWith("mesh:") && Mesh.hosts.none { it.id == host }
        }
        val dropped = existing.map { it.id }.filter { id ->
            id !in wanted && (gone(id) || hostOf(id) in listed || hostOf(id).orEmpty().startsWith("mesh:") && settled)
        }
        val kept = existing.filter { it.id !in wanted && it.id !in dropped }.sortedBy { it.rank }
        val over = kept.drop((most - bots.size).coerceAtLeast(0)).map { it.id } // (room made for the live ones)
        val off = dropped.filter(::gone)
        if (dropped.isNotEmpty() || over.isNotEmpty()) {
            ShortcutManagerCompat.removeDynamicShortcuts(app, dropped + over)
            ShortcutManagerCompat.removeLongLivedShortcuts(app, dropped + over)
            (dropped + over).forEach { published.remove(it) }
        }
        if (off.isNotEmpty()) ShortcutManagerCompat.disableShortcuts(app, off, "This Bot isn't there any more")
        hosts.edit().apply { off.forEach(::remove) }.apply()
        val disabled = ShortcutManagerCompat.getShortcuts(app, ShortcutManagerCompat.FLAG_MATCH_PINNED)
            .filter { !it.isEnabled }.map { it.id }.toSet()
        bots.forEachIndexed { rank, bot ->
            Mesh.picture(bot)
            val picture = Mesh.pictures[bot.pictureKey]
            val id = bot.id // (its session's: the same shortcut wherever its machine's reached from, a pinned one too)
            if (hosts.getString(id, null) != bot.hostId) hosts.edit().putString(id, bot.hostId).apply()
            val was = bot.hostId + "\n" + bot.name + "\n" + (if (picture != null) bot.pictureKey else "")
            if (published[id] == was && id !in disabled) return@forEachIndexed
            val person = Person.Builder().setName(bot.name).setKey(id).setBot(true).build()
            val open = Intent(app, MainActivity::class.java).setAction(Intent.ACTION_VIEW)
                .putExtra("host", bot.hostId).putExtra("session", bot.id)
            val info = ShortcutInfoCompat.Builder(app, id).setShortLabel(bot.name).setLongLabel(bot.name)
                .setIcon(IconCompat.createWithAdaptiveBitmap(icon(bot.name, picture?.asAndroidBitmap())))
                .setIntent(open).setPerson(person).setLongLived(true).setCategories(setOf(CATEGORY)).setRank(rank)
                .build()
            if (id in disabled) ShortcutManagerCompat.enableShortcuts(app, listOf(info)) // (a Bot again)
            if (ShortcutManagerCompat.pushDynamicShortcut(app, info)) published[id] = was
        }
    }

    /** A Bot's icon as an adaptive one: its picture, or its initials on its avatar's gradient (as Avatar draws them),
     * filling what the launcher's mask shows. */
    private fun icon(name: String, picture: Bitmap?): Bitmap {
        val size = 216 // (108dp's canvas at 2x: the inner 72 is what's sure to show)
        val out = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(out)
        if (picture != null) {
            val paint = Paint(Paint.FILTER_BITMAP_FLAG)
            fun cover(edge: Int) {
                val k = edge.toFloat() / minOf(picture.width, picture.height)
                val w = (picture.width * k).toInt()
                val h = (picture.height * k).toInt()
                canvas.drawBitmap(picture, null, Rect((size - w) / 2, (size - h) / 2, (size + w) / 2, (size + h) / 2), paint)
            }
            cover(size)
            cover(size * 72 / 108) // (the picture whole where the mask's sure to show it, not cut to its middle)
            return out
        }
        val (top, bottom, fg) = avatarColors(name)
        canvas.drawPaint(Paint().apply {
            shader = LinearGradient(0f, 0f, 0f, size.toFloat(), top.toArgb(), bottom.toArgb(), Shader.TileMode.CLAMP)
        })
        val text = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = fg.toArgb()
            typeface = Typeface.DEFAULT_BOLD
            textAlign = Paint.Align.CENTER
            textSize = 0.4f * size * 66 / 108 // (as Avatar's: 0.4 of the circle the mask shows)
        }
        val m = text.fontMetrics
        canvas.drawText(initials(name), size / 2f, size / 2f - (m.ascent + m.descent) / 2, text)
        return out
    }
}

/** Over the home screen while something's shared: the chat it goes to is the one you open. */
@Composable
fun ShareBanner(cancel: () -> Unit) {
    val c = LocalBaude.current
    Row(Modifier.fillMaxWidth().background(c.fg.copy(alpha = 0.08f)).padding(start = 12.dp, end = 4.dp),
        verticalAlignment = Alignment.CenterVertically) {
        Text("Choose a Bot or session to send this to", style = Type.caption, color = c.fg.copy(alpha = c.fg.alpha * 0.75f),
            modifier = Modifier.weight(1f).padding(vertical = 8.dp))
        IconButton(Adwaita.WindowClose, Wording.Common.cancel, onClick = cancel)
    }
}
