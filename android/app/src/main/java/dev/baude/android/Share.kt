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
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
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
        val to = intent.getStringExtra(ShortcutManagerCompat.EXTRA_SHORTCUT_ID)?.let { id ->
            id.substringBefore('/', "").ifBlank { null }?.let { it to id.substringAfter('/') }
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
            ShortcutManagerCompat.reportShortcutUsed(Mesh.app, id(chat.hostId, chat.sid))
        } catch (_: Exception) {
        }
    }

    private fun id(hostId: String, sid: String) = "$hostId/$sid"

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
     * new or its name or picture changed; one taken away once its host lists its Bots without it (or the host's
     * gone), but kept while its host isn't reached yet, so the share sheet has it whether or not it's connected. */
    private fun publish() {
        val app = Mesh.app
        val most = ShortcutManagerCompat.getMaxShortcutCountPerActivity(app).coerceAtLeast(1)
        val bots = Mesh.contacts.take(most)
        val wanted = bots.map { id(it.hostId, it.id) }.toSet()
        val listed = Mesh.sessions.keys
        val settled = Mesh.hosts.all { Mesh.conn(it.id)?.state == "up" } // (the mesh as your hosts list it, whole)
        val gone = ShortcutManagerCompat.getDynamicShortcuts(app).map { it.id }.filter { id ->
            val host = id.substringBefore('/')
            id !in wanted && when {
                host in listed -> true
                host == "phone" -> false
                host.startsWith("mesh:") -> settled
                else -> Mesh.hosts.none { it.id == host }
            }
        }
        if (gone.isNotEmpty()) {
            ShortcutManagerCompat.removeDynamicShortcuts(app, gone)
            ShortcutManagerCompat.removeLongLivedShortcuts(app, gone)
            gone.forEach { published.remove(it) }
        }
        bots.forEachIndexed { rank, bot ->
            Mesh.picture(bot)
            val picture = Mesh.pictures[bot.pictureKey]
            val id = id(bot.hostId, bot.id)
            val was = bot.name + "\n" + (if (picture != null) bot.pictureKey else "")
            if (published[id] == was) return@forEachIndexed
            val person = Person.Builder().setName(bot.name).setKey(id).setBot(true).build()
            val open = Intent(app, MainActivity::class.java).setAction(Intent.ACTION_VIEW)
                .putExtra("host", bot.hostId).putExtra("session", bot.id)
            val info = ShortcutInfoCompat.Builder(app, id).setShortLabel(bot.name).setLongLabel(bot.name)
                .setIcon(IconCompat.createWithAdaptiveBitmap(icon(bot.name, picture?.asAndroidBitmap())))
                .setIntent(open).setPerson(person).setLongLived(true).setCategories(setOf(CATEGORY)).setRank(rank)
                .build()
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
        IconButton(Icons.Filled.Close, "Cancel", onClick = cancel)
    }
}
