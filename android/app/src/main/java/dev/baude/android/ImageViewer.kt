package dev.baude.android

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.activity.compose.BackHandler
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.foundation.MutatePriority
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculatePan
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.foundation.gestures.calculateCentroid
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.stopScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.FilterQuality
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.clipRect
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChange
import androidx.compose.ui.input.pointer.util.VelocityTracker
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.positionInRoot
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.onClick
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.DpOffset
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import java.util.Base64
import kotlin.coroutines.resume

/** A picture the large view can show: its bytes (at once, or made when it's asked for), where it is on its host (a
 * file there), the web address it came from, and its name. */
class ViewItem(
    val key: String, val data: ByteArray?, val hostId: String?, val path: String?, val name: String?, val url: String?,
    val load: (suspend () -> ByteArray?)? = null,
) {
    suspend fun bytes(): ByteArray? = data ?: load?.invoke()
}

class ViewerState(val items: List<ViewItem>, index: Int, val from: Rect?) {
    var index by mutableIntStateOf(index)
}

/** The chat's pictures, opened large (lightbox.Lightbox): over the whole of the chat's screen. */
object Viewer {
    var flyMs = Tokens.Lightbox.flyMs
    val state = mutableStateOf<ViewerState?>(null)

    fun open(items: List<ViewItem>, index: Int, from: Rect?) {
        if (items.isEmpty()) return
        state.value = ViewerState(items, index.coerceIn(0, items.lastIndex), from)
    }

    private fun place(chat: Chat): ChatPlace =
        ChatPlace(chat.hostId, Mesh.session(chat.hostId, chat.sid)?.cwd, Mesh.conn(chat.hostId)?.info?.home, chat.artifacts.toList())

    /** The chat's pictures in the order they stand: those in the box, what's said (yours, its tools', its replies'),
     * then what's just been sent. */
    fun items(chat: Chat): List<ViewItem> {
        val out = ArrayList<ViewItem>()
        val place = place(chat)
        for (f in chat.files) if (f.mime.startsWith("image/")) out.add(ViewItem("picked:" + System.identityHashCode(f), f.bytes, null, null, f.name, null))
        fun yours(line: Line) {
            line.files.forEachIndexed { i, f ->
                if (f.kind == "image" && f.data != null) out.add(ViewItem("you:${line.uuid}:$i", try { Base64.getDecoder().decode(f.data) } catch (_: Exception) { null },
                    null, null, f.name, null))
            }
        }
        for (line in chat.lines) when (line.kind) {
            "you" -> yours(line)
            "call" -> chat.calls.byId[line.text]?.let { call ->
                val path = call.input.optString("file_path").takeIf { call.base == "Read" && it.isNotBlank() }
                call.images.forEachIndexed { i, d ->
                    out.add(ViewItem("call:${call.id}:$i", try { Base64.getDecoder().decode(d) } catch (_: Exception) { null }, chat.hostId,
                        path?.let { PathCheck.absolute(place, it) }, null, null))
                }
            }
            "bot" -> for (b in cachedBlocks(line.text).filterIsInstance<Block.Image>()) {
                if (out.any { it.key == "md:" + b.src }) continue
                val shown = b.src.removePrefix("file://")
                val web = shown.startsWith("http://") || shown.startsWith("https://")
                out.add(ViewItem("md:" + b.src, null, chat.hostId, if (web) null else PathCheck.absolute(place, shown), b.alt.ifBlank { null },
                    if (web) shown else null) { bytesOf(place, shown) })
            }
        }
        for (s in chat.sent) for (line in s.lines) yours(line)
        return out
    }

    /** The picture of this key opened in the chat's sequence, flying out of where it is on screen. */
    fun openFrom(chat: Chat, key: String) {
        val items = items(chat)
        val i = items.indexOfFirst { it.key == key }
        if (i < 0) return
        open(items, i, ImageSpots.rects[key])
    }

    fun item(chat: Chat, key: String): ViewItem? = items(chat).firstOrNull { it.key == key }
}

/** Where each picture of the chat is on screen (in the window), kept as it's laid out: the large view flies from it
 * and back. */
object ImageSpots {
    val rects = HashMap<String, Rect>()
}

val LocalImageOpener = compositionLocalOf<(String) -> Unit> { {} }
val LocalImageItem = compositionLocalOf<(String) -> ViewItem?> { { null } }

/** A picture in a chat: a tap opens it large, a long press opens its menu (view, copy, open elsewhere). */
@Composable
fun ImageTouch(key: String, modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    val opener = LocalImageOpener.current
    val item = LocalImageItem.current
    var menu by remember { mutableStateOf<Offset?>(null) }
    var height by remember { mutableIntStateOf(0) }
    val density = LocalDensity.current
    androidx.compose.runtime.DisposableEffect(key) { onDispose { ImageSpots.rects.remove(key) } }
    Box(modifier.onGloballyPositioned {
        height = it.size.height
        ImageSpots.rects[key] = Rect(it.positionInRoot(), Size(it.size.width.toFloat(), it.size.height.toFloat()))
    }.semantics { role = androidx.compose.ui.semantics.Role.Image; onClick { opener(key); true } }
        .pointerInput(key) { detectTapGestures(onTap = { opener(key) }, onLongPress = { menu = it }) }) {
        content()
        menu?.let { at ->
            ImageMenu(item(key), { opener(key) }, DpOffset(with(density) { at.x.toDp() }, with(density) { (at.y - height).toDp() })) { menu = null }
        }
    }
}

/** An image's menu (widgets image menu): View, Open in browser (a web one), Copy image, Open with default app, Open in
 * folder (one that's a file on its host). */
@Composable
fun ImageMenu(item: ViewItem?, view: () -> Unit, at: DpOffset, dismiss: () -> Unit) {
    val ctx = LocalContext.current
    val uri = LocalUriHandler.current
    val scope = rememberCoroutineScope()
    Popover(true, dismiss, offset = at) {
        MenuChoice(Wording.Common.view, false) { dismiss(); view() }
        if (item?.url != null) MenuChoice(Wording.Images.openInBrowser, false) { dismiss(); try { uri.openUri(item.url) } catch (_: Exception) {} }
        MenuChoice(Wording.Images.copyImage, false) { dismiss(); scope.launch { ImageFiles.copy(ctx, item?.bytes()) } }
        MenuChoice(Wording.Images.openWithDefaultApp, false) { dismiss(); scope.launch { ImageFiles.open(ctx, item?.bytes()) } }
        if (item?.hostId != null && item.path != null && hostHit(item) != null) MenuChoice(Wording.Images.openInFolder, false) {
            dismiss()
            FileLinks.open(Wanted(item.hostId, item.path, null, false))
        }
    }
}

/** The file an image is on its host, if it's known to be there. */
@Composable
fun hostHit(item: ViewItem): Hit? {
    val host = item.hostId ?: return null
    val path = item.path ?: return null
    return PathCheck.find(ChatPlace(host, null, Mesh.conn(host)?.info?.home, emptyList()), path)
}

/** Copy and open of an image: its bytes written under the cache (a name of its hash), handed on by a FileProvider. */
object ImageFiles {
    private fun ext(b: ByteArray) = when {
        b.size > 4 && b[0] == 0x89.toByte() && b[1] == 'P'.code.toByte() -> ".png"
        b.size > 2 && b[0] == 0xFF.toByte() && b[1] == 0xD8.toByte() -> ".jpg"
        b.size > 4 && String(b, 0, 4, Charsets.ISO_8859_1) == "GIF8" -> ".gif"
        b.size > 4 && String(b, 0, 4, Charsets.ISO_8859_1) == "RIFF" -> ".webp"
        else -> ".png"
    }

    private fun uri(ctx: Context, bytes: ByteArray): Pair<android.net.Uri, String> {
        val hash = java.security.MessageDigest.getInstance("SHA-1").digest(bytes).joinToString("") { "%02x".format(it) }.take(16)
        val e = ext(bytes)
        val dir = java.io.File(ctx.cacheDir, "images").also { it.mkdirs() }
        val f = java.io.File(dir, hash + e)
        if (!f.exists()) f.writeBytes(bytes)
        val mime = when (e) { ".jpg" -> "image/jpeg"; ".gif" -> "image/gif"; ".webp" -> "image/webp"; else -> "image/png" }
        return androidx.core.content.FileProvider.getUriForFile(ctx, "${ctx.packageName}.images", f) to mime
    }

    suspend fun copy(ctx: Context, bytes: ByteArray?) {
        try {
            val (u, _) = withContext(Dispatchers.IO) { uri(ctx, bytes ?: throw IllegalStateException()) }
            ctx.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newUri(ctx.contentResolver, "image", u))
            toast(Wording.Images.copied)
        } catch (_: Exception) {
            toast(Wording.Images.cannotCopy)
        }
    }

    suspend fun open(ctx: Context, bytes: ByteArray?) {
        try {
            val (u, mime) = withContext(Dispatchers.IO) { uri(ctx, bytes ?: throw IllegalStateException()) }
            ctx.startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(u, mime).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (_: Exception) {
            toast(Wording.Images.cannotShow)
        }
    }
}

// ----------------------------------------------------------------------------------------------------- the view --

private class Loaded(val bitmap: Bitmap, val w: Int, val h: Int, val bytes: ByteArray)

private fun decodeBig(bytes: ByteArray): Loaded? {
    val o = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeByteArray(bytes, 0, bytes.size, o)
    if (o.outWidth <= 0) return null
    var n = 1
    while (maxOf(o.outWidth, o.outHeight) / (n * 2) >= 4096) n *= 2
    val b = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = n }) ?: return null
    return Loaded(b, o.outWidth, o.outHeight, bytes)
}

/** Whether data scaled to a grid across it is the picture small is (imageviews.same_picture): its pixels, 24 by 24,
 * within a little of small's. */
fun samePicture(a: Bitmap, b: Bitmap): Boolean {
    val x = Bitmap.createScaledBitmap(a, 24, 24, true)
    val y = Bitmap.createScaledBitmap(b, 24, 24, true)
    var total = 0L
    for (i in 0 until 24) for (j in 0 until 24) {
        val p = x.getPixel(i, j)
        val q = y.getPixel(i, j)
        total += Math.abs((p shr 16 and 255) - (q shr 16 and 255)) + Math.abs((p shr 8 and 255) - (q shr 8 and 255)) + Math.abs((p and 255) - (q and 255))
    }
    return total / (24 * 24 * 3.0) < 14
}

private suspend fun hostFile(hostId: String, path: String): ByteArray? = suspendCancellableCoroutine { k ->
    Mesh.ask(hostId, Msg.ReadFile(path = path, limit = 20_000_000).json()) { r ->
        val f = Msg.File.from(r)
        k.resume(if (r.optString("op") == Op.FILE && f.error.isNullOrBlank() && f.truncated != true)
            try { Base64.getDecoder().decode(f.data.orEmpty()) } catch (_: Exception) { null } else null)
    }
}

/** A picture's bytes decoded, then swapped for its file's on its host if that's the same picture, larger. */
@Composable
private fun loadedOf(item: ViewItem, got: (Loaded) -> Unit): Loaded? {
    val loaded by produceState<Loaded?>(null, item.key) {
        value = null
        val first = withContext(Dispatchers.Default) { item.bytes()?.let { decodeBig(it) } }
        value = first
        first?.let(got)
        val path = item.path
        val host = item.hostId
        if (first != null && path != null && host != null && takes(host, "read_file")) { // (the file's own, if it's bigger and the same)
            val file = hostFile(host, path)
            val big = withContext(Dispatchers.Default) { file?.let { decodeBig(it) } }
            if (big != null && big.w > first.w && Math.abs(big.w.toDouble() / big.h - first.w.toDouble() / first.h) < 0.02 &&
                withContext(Dispatchers.Default) { samePicture(first.bitmap, big.bitmap) }) {
                value = big
                got(big)
            }
        }
    }
    return loaded
}

/** The large view over the chat's whole screen (lightbox.Lightbox). */
@Composable
fun ViewerOverlay() {
    val st = Viewer.state.value ?: return
    ViewerStage(st)
}

@Composable
private fun ViewerStage(st: ViewerState) {
    val ctx = LocalContext.current
    val uri = LocalUriHandler.current
    val density = LocalDensity.current
    val scope = rememberCoroutineScope()
    val n = st.items.size
    // the pictures side by side, a swipe from one to the next: the one shown is the one the pager last came to rest on
    val pager = rememberPagerState(initialPage = st.index) { n }
    var held by remember { mutableStateOf(false) } // (a finger's on it, or the pages are going where it left them)
    LaunchedEffect(pager) { snapshotFlow { if (held) -1 else pager.settledPage }.collect { if (it >= 0) st.index = it } }
    val item = st.items[st.index]
    val loads = remember { mutableStateMapOf<String, Loaded>() } // (the pages drawn, by their pictures' keys)
    val loaded = loads[item.key]
    val progress = remember { Animatable(0f) }
    var zoom by remember(item.key) { mutableFloatStateOf(1f) }
    var pan by remember(item.key) { mutableStateOf(Offset.Zero) }
    var origin by remember { mutableStateOf(Offset.Zero) }
    var closing by remember { mutableStateOf(false) }
    val focus = remember { FocusRequester() }
    val keys = remember { arrayOf<(Key) -> Boolean>({ false }) }
    // (what a finger's move and its lifting do, as this composition has the picture: the gesture outlives it)
    val moves = remember { arrayOf<(Int, Float, Offset, Offset, Int, Boolean) -> Unit>({ _, _, _, _, _, _ -> }) }
    val lifts = remember { arrayOf<(Int, Float, Boolean) -> Unit>({ _, _, _ -> }) }
    LaunchedEffect(Unit) {
        focus.requestFocus()
        progress.animateTo(1f, tween(Viewer.flyMs, easing = FastOutSlowInEasing))
    }
    fun close() {
        if (closing) return
        closing = true
        scope.launch {
            progress.animateTo(0f, tween(Viewer.flyMs, easing = FastOutSlowInEasing))
            Viewer.state.value = null
        }
    }
    BackHandler { close() }
    val p = progress.value
    val bar = Gap.bar + with(density) { WindowInsets.statusBars.getTop(density).toDp() }
    BoxWithConstraints(Modifier.fillMaxSize().onGloballyPositioned { origin = it.positionInRoot() }
        .background(Color.Black.copy(alpha = Tokens.Lightbox.shade.toFloat() * p))
        .focusRequester(focus).focusable().onPreviewKeyEvent { e ->
            e.type == KeyEventType.KeyDown && keys[0](e.key)
        }) {
        val stageW = with(density) { maxWidth.toPx() }
        val barPx = with(density) { bar.toPx() }
        val stageH = with(density) { maxHeight.toPx() } - barPx
        val img = loaded
        val availW = stageW - 2 * with(density) { Tokens.Lightbox.side.toPx() }
        val availH = stageH - with(density) { (Tokens.Lightbox.top + Tokens.Lightbox.bottom).toPx() }
        fun fitOf(im: Loaded?): Float = if (im == null) 1f else {
            val shrink = minOf(availW / im.w, availH / im.h)
            if (maxOf(im.w, im.h) >= 400) shrink else minOf(density.density, shrink)
        }
        val fit = fitOf(img)
        val maxZoom = maxOf(1f, Tokens.Lightbox.mostPixels.toFloat() / (fit / density.density))
        val center = Offset(stageW / 2, barPx + with(density) { Tokens.Lightbox.top.toPx() } + availH / 2)
        fun clamp(z: Float, o: Offset): Offset {
            val w = (img?.w ?: 0) * fit * z
            val h = (img?.h ?: 0) * fit * z
            val mx = if (w > stageW) (w - stageW) / 2 else 0f
            val my = if (h > stageH) (h - stageH) / 2 else 0f
            return Offset(o.x.coerceIn(-mx, mx), o.y.coerceIn(-my, my))
        }
        fun zoomTo(z: Float, around: Offset) {
            val z2 = z.coerceIn(1f, maxZoom)
            val c0 = center + pan
            val newCenter = around - (around - c0) * (z2 / zoom)
            val start = zoom
            val startPan = pan
            val endPan = if (z2 <= 1f) Offset.Zero else clamp(z2, newCenter - center)
            scope.launch {
                Animatable(0f).animateTo(1f, tween(Tokens.Lightbox.zoomMs)) {
                    zoom = start + (z2 - start) * value
                    pan = startPan + (endPan - startPan) * value
                }
            }
        }
        fun turn(d: Int) { // (wrapping round from the last to the first, and back)
            if (n < 2) return
            val to = st.index + d
            scope.launch { if (to in 0 until n) pager.animateScrollToPage(to) else pager.scrollToPage((to + n) % n) }
        }
        keys[0] = l@{ k ->
            val step = Offset(stageW, stageH) / 5f
            when (k) {
                Key.Escape -> close()
                Key.DirectionLeft -> if (zoom <= 1f) turn(-1) else pan = clamp(zoom, pan + Offset(step.x, 0f))
                Key.DirectionRight -> if (zoom <= 1f) turn(1) else pan = clamp(zoom, pan - Offset(step.x, 0f))
                Key.DirectionUp -> if (zoom > 1f) pan = clamp(zoom, pan + Offset(0f, step.y))
                Key.DirectionDown -> if (zoom > 1f) pan = clamp(zoom, pan - Offset(0f, step.y))
                Key.Plus, Key.Equals, Key.NumPadAdd -> zoomTo(zoom * Tokens.Lightbox.zoomKey.toFloat(), center + pan)
                Key.Minus, Key.NumPadSubtract -> zoomTo(zoom / Tokens.Lightbox.zoomKey.toFloat(), center + pan)
                Key.Zero, Key.NumPad0 -> zoomTo(1f, center)
                else -> return@l false
            }
            true
        }
        fun placed(im: Loaded?, f: Float, z: Float, o: Offset): Rect? = if (im == null) null else
            Rect(center + o - Offset(im.w * f * z / 2, im.h * f * z / 2), Size(im.w * f * z, im.h * f * z))
        val dest = placed(img, fit, zoom, pan)
        val spot = (if (closing) ImageSpots.rects[item.key] else st.from ?: ImageSpots.rects[item.key])?.translate(-origin)
        val shown = if (dest == null) null else if (spot != null && p < 1f) Rect(
            Offset(spot.left + (dest.left - spot.left) * p, spot.top + (dest.top - spot.top) * p),
            Size(spot.width + (dest.width - spot.width) * p, spot.height + (dest.height - spot.height) * p)) else dest
        val fling = with(density) { 400.dp.toPx() } // (a finger leaving faster than this a second goes on to the next)
        fun paged(from: Int) = (pager.currentPage - from) + pager.currentPageOffsetFraction // (in pages, + towards the next)
        moves[0] = { from, zc, dp, centroid, fingers, settling ->
            val resting = Math.abs(paged(from)) < 0.001f
            if (zc != 1f && fingers > 1 && (resting || settling)) { // (a second finger's landing turns paging into zooming)
                val z2 = (zoom * zc).coerceIn(1f, maxZoom)
                val c0 = center + pan
                val moved = centroid - (centroid - c0) * (z2 / zoom) + dp
                zoom = z2
                pan = if (z2 <= 1f) Offset.Zero else clamp(z2, moved - center)
            } else if (!resting && settling) {
                // (the pages are going back to this picture)
            } else {
                var dx = dp.x
                val off = paged(from) * stageW
                if (off != 0f) { // (a picture part way in: the drag moves the pages first, back to this one at most)
                    val toPages = if (off > 0f) maxOf(-dx, -off) else minOf(-dx, -off)
                    val took = pager.dispatchRawDelta(toPages)
                    dx = if (toPages == -off) dx + took else 0f // (what's left once back pans the picture)
                }
                val next = clamp(zoom, Offset(pan.x + dx, pan.y + dp.y))
                val rest = dx - (next.x - pan.x)
                pan = if (zoom <= 1f) Offset.Zero else next
                if (rest != 0f) pager.dispatchRawDelta(-rest)
            }
        }
        lifts[0] = { from, vx, settling ->
            val at = paged(from)
            if (Math.abs(at) <= 0.001f) held = false else {
                val by = when {
                    settling -> 0
                    vx < -fling && at > 0f -> 1
                    vx > fling && at < 0f -> -1
                    Math.abs(at) > 0.5f -> if (at > 0f) 1 else -1
                    else -> 0
                }
                scope.launch {
                    pager.animateScrollToPage((from + by).coerceIn(0, n - 1))
                    held = false
                }
            }
        }
        Box(Modifier.fillMaxSize().pointerInput(item.key, img?.w, fit) {
            detectTapGestures(onTap = { at -> if (dest == null || !dest.contains(at)) close() },
                onDoubleTap = { at -> zoomTo(if (zoom > 1.01f) 1f else 2f, at) })
        }.pointerInput(pager) {
            // a drag pans the picture while it's zoomed in, as far as its edge, and pages past that; a pinch zooms
            awaitEachGesture {
                awaitFirstDown(requireUnconsumed = false)
                held = true
                if (pager.isScrollInProgress) scope.launch(start = CoroutineStart.UNDISPATCHED) { pager.stopScroll(MutatePriority.UserInput) }
                st.index = pager.currentPage // (the page the pager is on, the one a swipe's lifting settles from, even if the last swipe's spring is still running)
                val from = st.index
                var settling = false // (a second finger landed with the pages away from this picture: they go back, and the gesture zooms)
                val velocity = VelocityTracker()
                var slop = Offset.Zero
                var moving = false
                try {
                    while (true) {
                        val e = awaitPointerEvent()
                        val down = e.changes.filter { it.pressed }
                        if (down.isEmpty()) break
                        if (down.size > 1 && !settling && Math.abs((pager.currentPage - from) + pager.currentPageOffsetFraction) > 0.001f) {
                            settling = true
                            scope.launch { pager.animateScrollToPage(from) }
                        }
                        down.first().let { velocity.addPosition(it.uptimeMillis, it.position) }
                        val zc = e.calculateZoom()
                        val dp = e.calculatePan()
                        if (!moving) {
                            slop += dp
                            if (slop.getDistance() > viewConfiguration.touchSlop || (zc != 1f && down.size > 1)) moving = true else continue
                        }
                        moves[0](from, zc, dp, e.calculateCentroid(useCurrent = false), down.size, settling)
                        e.changes.forEach { if (it.positionChange() != Offset.Zero) it.consume() }
                    }
                } finally {
                    lifts[0](from, velocity.calculateVelocity().x, settling)
                }
            }
        }) {
            HorizontalPager(pager, Modifier.fillMaxSize(), beyondViewportPageCount = 1, userScrollEnabled = false) { i ->
                val pic = st.items[i]
                val im = loadedOf(pic) { loads[pic.key] = it }
                DisposableEffect(pic.key) { onDispose { loads.remove(pic.key) } }
                val here = i == st.index
                val at = if (here) shown else placed(im, fitOf(im), 1f, Offset.Zero)
                if (im != null && at != null) Canvas(Modifier.fillMaxSize()) {
                    clipRect(0f, barPx, size.width, size.height) {
                        drawImage(im.bitmap.asImageBitmap(), srcOffset = IntOffset.Zero,
                            srcSize = IntSize(im.bitmap.width, im.bitmap.height),
                            dstOffset = IntOffset(at.left.toInt(), at.top.toInt()),
                            dstSize = IntSize(at.width.toInt().coerceAtLeast(1), at.height.toInt().coerceAtLeast(1)),
                            filterQuality = FilterQuality.High)
                    }
                }
            }
        }
        // the bar: its title, what's known of the picture, and what can be done with it
        val place = ChatPlace(item.hostId.orEmpty(), null, item.hostId?.let { Mesh.conn(it)?.info?.home }, emptyList())
        val title = item.path?.let { pth -> item.hostId?.let { PathCheck.find(place, pth)?.path?.substringAfterLast('/') } }
            ?: item.name ?: item.url ?: Wording.Images.untitled
        val bytes = img?.bytes?.size ?: 0
        val info = listOfNotNull(
            img?.let { "${it.w}×${it.h} · " + if (bytes / 1024.0 >= 1024) Wording.Images.sizeMb(mb = String.format(java.util.Locale.ROOT, "%.1f", bytes / 1024.0 / 1024))
                else Wording.Images.sizeKb(kb = String.format(java.util.Locale.ROOT, "%.0f", bytes / 1024.0)) },
            if (n > 1) "${st.index + 1} / $n" else null,
            if (zoom > 1.01f) "${Math.round(zoom * 100)}%" else null).joinToString("  ·  ")
        val hostFile = hostHit(item) != null
        val stacked = maxWidth < 600.dp // (a narrow phone: what is known of it goes under its title)
        Column(Modifier.fillMaxWidth().alpha(p).padding(top = with(density) { WindowInsets.statusBars.getTop(density).toDp() })) {
            Row(Modifier.fillMaxWidth().height(Gap.bar).padding(start = 16.dp, end = 12.dp), horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically) {

                Column(Modifier.weight(1f)) {
                    Text(title, style = Type.title, color = Color.White, maxLines = 1, overflow = TextOverflow.MiddleEllipsis)
                    if (stacked && info.isNotEmpty()) Text(info, style = Type.caption, color = Color.White.copy(alpha = 0.7f), maxLines = 1)
                }
                if (!stacked) Text(info, style = Type.body, color = Color.White.copy(alpha = 0.7f), maxLines = 1)
                ViewerButton(Wording.Common.copy, Wording.Images.copyImage) { scope.launch { ImageFiles.copy(ctx, img?.bytes ?: item.bytes()) } }
                if (hostFile) ViewerButton(Wording.Images.showInFolder, Wording.Images.openItsFolder) {
                    FileLinks.open(Wanted(item.hostId!!, item.path!!, null, false))
                    close()
                }
                ViewerButton(Wording.Common.open, Wording.Images.openWithTheDefaultApp) { scope.launch { ImageFiles.open(ctx, img?.bytes ?: item.bytes()) } }
                if (item.url != null) ViewerButton(Wording.Images.openInBrowser, Wording.Images.openOriginalInBrowser) {
                    try { uri.openUri(item.url) } catch (_: Exception) {}
                }
                Box(Modifier.size(34.dp).clip(CircleShape).background(Color.White.copy(alpha = 0.10f)).clickable { close() }
                    .semantics { contentDescription = Wording.Images.close }, contentAlignment = Alignment.Center) {
                    Icon(Adwaita.WindowClose, null, tint = Color.White, modifier = Modifier.size(16.dp.icon))
                }
            }
        }
    }
}

@Composable
private fun ViewerButton(label: String, description: String, onClick: () -> Unit) {
    Box(Modifier.clip(RoundedCornerShape(8.dp)).background(Color.White.copy(alpha = 0.10f)).clickable(onClick = onClick)
        .semantics { contentDescription = description }.padding(horizontal = 10.dp, vertical = 6.dp)) {
        Text(label, style = Type.body, color = Color.White, maxLines = 1)
    }
}
