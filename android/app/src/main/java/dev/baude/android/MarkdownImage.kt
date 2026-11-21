package dev.baude.android

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.LruCache
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import java.util.Base64
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlin.coroutines.resume

/** A picture of a reply, decoded (its size as it is, the bitmap sampled to what's shown). */
private class Picture(val bitmap: Bitmap, val width: Int, val height: Int)

private object Pictures {
    val cache = object : LruCache<String, Picture>(32 * 1024 * 1024) {
        override fun sizeOf(key: String, value: Picture) = value.bitmap.byteCount
    }
}

private const val MAX_PICTURE_BYTES = 20_000_000

/** Up to n bytes of a stream (InputStream.readNBytes is for Android 13 and later). */
fun java.io.InputStream.readUpTo(n: Int): ByteArray {
    val out = java.io.ByteArrayOutputStream()
    val buf = ByteArray(8192)
    while (out.size() < n) {
        val got = read(buf, 0, minOf(buf.size, n - out.size()))
        if (got < 0) break
        out.write(buf, 0, got)
    }
    return out.toByteArray()
}

/** A picture's bytes: a web one fetched, a file of the chat's host read from it; null if it can't be had. */
private suspend fun bytesOf(place: ChatPlace?, src: String): ByteArray? {
    if (src.startsWith("http://") || src.startsWith("https://")) return withContext(Dispatchers.IO) {
        try {
            val conn = java.net.URL(src).openConnection() as java.net.HttpURLConnection
            conn.connectTimeout = 15_000
            conn.readTimeout = 15_000
            conn.inputStream.use { it.readUpTo(MAX_PICTURE_BYTES) }
        } catch (_: Exception) {
            null
        }
    }
    place ?: return null
    val path = PathCheck.absolute(place, src.removePrefix("file://")) ?: return null
    return suspendCancellableCoroutine { k ->
        Mesh.ask(place.hostId, Msg.ReadFile(path = path, limit = MAX_PICTURE_BYTES).json()) { r ->
            val f = Msg.File.from(r)
            k.resume(if (r.optString("op") == Op.FILE && f.error.isNullOrBlank() && f.truncated != true)
                try { Base64.getDecoder().decode(f.data.orEmpty()) } catch (_: Exception) { null } else null)
        }
    }
}

private fun decode(bytes: ByteArray, edge: Int): Picture? {
    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
    if (bounds.outWidth <= 0) return null
    var sample = 1
    while (maxOf(bounds.outWidth, bounds.outHeight) / (sample * 2) >= edge) sample *= 2
    val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = sample })
        ?: return null
    return Picture(bmp, bounds.outWidth, bounds.outHeight)
}

/** A `![alt](src)` line of a reply (widgets.MarkdownImage): its source as a link, the picture 4 under it (a host's
 * file read, a web one fetched); while a web one comes, or if it can't be had, only "Image: <link>". */
@Composable
fun MarkdownImage(alt: String, src: String) {
    val c = LocalBaude.current
    val place = LocalChatPlace.current
    val uri = LocalUriHandler.current
    val density = LocalDensity.current
    val shown = src.removePrefix("file://")
    val web = shown.startsWith("http://") || shown.startsWith("https://")
    val cacheKey = if (web) shown else (place?.hostId.orEmpty() + ":" + (place?.let { PathCheck.absolute(it, shown) } ?: shown))
    var picture by remember(cacheKey) { mutableStateOf(Pictures.cache.get(cacheKey)) }
    var failed by remember(cacheKey) { mutableStateOf(false) }
    LaunchedEffect(cacheKey) {
        if (picture != null) return@LaunchedEffect
        val got = bytesOf(place, shown)?.let {
            withContext(Dispatchers.Default) { decode(it, (Tokens.Markdown.imageEdge.value * density.density).toInt()) }
        }
        if (got != null) {
            Pictures.cache.put(cacheKey, got)
            picture = got
        } else failed = true
    }
    fun open() {
        if (web) try { uri.openUri(shown) } catch (_: Exception) {}
        else place?.let { FileLinks.open(Wanted(it.hostId, PathCheck.absolute(it, shown) ?: shown, null, false)) }
    }
    val link = SpanStyle(color = c.accent, textDecoration = TextDecoration.Underline)
    val pic = picture
    if (pic == null) {
        val label = alt.ifBlank { src }
        val mark = "\u0001"
        val whole = Wording.Images.link(link = mark)
        Text(buildAnnotatedString {
            append(whole.substringBefore(mark))
            pushLink(LinkAnnotation.Clickable(shown, TextLinkStyles(link)) { open() })
            append(label)
            pop()
            append(whole.substringAfter(mark))
        }, style = Type.body, color = c.fg)
        return
    }
    Column {
        Text(buildAnnotatedString {
            pushLink(LinkAnnotation.Clickable(shown, TextLinkStyles(link)) { open() })
            append(shown)
            pop()
        }, style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.MiddleEllipsis, modifier = Modifier.fillMaxWidth())
        BoxWithConstraints(Modifier.padding(top = Tokens.Markdown.imageGap)) {
            val longest = maxOf(pic.width, pic.height).toFloat()
            val scale = minOf(1f, Tokens.Markdown.imageEdge.value / longest)
            val w = minOf((pic.width * scale).dp, maxWidth)
            val h = w * pic.height / pic.width
            Image(pic.bitmap.asImageBitmap(), Wording.Images.clickTip(name = shown.ifBlank { alt }), contentScale = ContentScale.FillBounds,
                modifier = Modifier.width(w).height(h).clip(RoundedCornerShape(Tokens.Markdown.imageRadius)))
        }
    }
}
