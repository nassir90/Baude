package dev.baude.android.screenshots

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import dev.baude.android.GitAvatars

/** Pictures as people and Bots have them, drawn here (nothing fetched): GitHub's identicon (pale grey to its edge, as
 * the page under it is in the light), and a portrait on a ground of its own, dark or light. */
object Pictures {
    private const val SIDE = 240

    /** GitHub's identicon: five by five squares of ink, mirrored, on #f0f0f0 with half a square round them. */
    fun identicon(seed: Long, ink: Int): ImageBitmap {
        val b = Bitmap.createBitmap(SIDE, SIDE, Bitmap.Config.ARGB_8888)
        val c = Canvas(b)
        c.drawColor(0xFFF0F0F0.toInt())
        val p = Paint().apply { color = ink }
        val cell = SIDE / 6f
        val rnd = java.util.Random(seed)
        for (y in 0 until 5) for (x in 0 until 3) if (rnd.nextBoolean()) for (at in setOf(x, 4 - x))
            c.drawRect(cell / 2 + at * cell, cell / 2 + y * cell, cell / 2 + (at + 1) * cell, cell / 2 + (y + 1) * cell, p)
        return b.asImageBitmap()
    }

    /** A head and shoulders on a plain ground. */
    fun portrait(ground: Int, skin: Int, shirt: Int, hair: Int): ImageBitmap {
        val b = Bitmap.createBitmap(SIDE, SIDE, Bitmap.Config.ARGB_8888)
        val c = Canvas(b)
        c.drawColor(ground)
        val p = Paint(Paint.ANTI_ALIAS_FLAG)
        p.color = shirt
        c.drawOval(RectF(SIDE * 0.12f, SIDE * 0.68f, SIDE * 0.88f, SIDE * 1.3f), p)
        p.color = hair
        c.drawCircle(SIDE * 0.5f, SIDE * 0.4f, SIDE * 0.22f, p)
        p.color = skin
        c.drawCircle(SIDE * 0.5f, SIDE * 0.44f, SIDE * 0.19f, p)
        return b.asImageBitmap()
    }

    /** GitAvatars' pictures for these emails, as if GitHub had given them (its map's private: set through reflection). */
    fun authors(pictures: Map<String, ImageBitmap>) {
        GitAvatars.off = true
        @Suppress("UNCHECKED_CAST")
        val kept = GitAvatars::class.java.getDeclaredField("pictures").apply { isAccessible = true }.get(GitAvatars)
            as HashMap<String, ImageBitmap>
        kept.clear()
        for ((email, picture) in pictures) kept[email.lowercase()] = picture
        GitAvatars.version.intValue++
    }
}
