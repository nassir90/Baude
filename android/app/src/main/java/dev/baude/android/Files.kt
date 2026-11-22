package dev.baude.android

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.LruCache
import androidx.activity.compose.BackHandler
import androidx.compose.animation.Crossfade
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.DpOffset
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.Base64

/** Whether Files shows its folders as a gallery, kept for the next start (settings' files_gallery). */
object FilesPrefs {
    val gallery = mutableStateOf(false)
    private var loaded = false

    fun load(ctx: Context) {
        if (loaded) return
        loaded = true
        gallery.value = ctx.getSharedPreferences("files", Context.MODE_PRIVATE).getBoolean("gallery", false)
    }

    fun save(ctx: Context) {
        ctx.getSharedPreferences("files", Context.MODE_PRIVATE).edit().putBoolean("gallery", gallery.value).apply()
    }
}

/** What a chat's Files page keeps while other pages show: its root, each folder's entries as read, the folders
 * opened, what's picked, the folder its gallery shows (none: the tree), the file opened. */
class FilesState {
    val root = mutableStateOf<String?>(null)
    val entries = mutableStateMapOf<String, List<Msg.DirEntry>>()
    val open = mutableStateMapOf<String, Boolean>()
    val selected = mutableStateOf<String?>(null)
    val gallery = mutableStateOf<String?>(null)
    val file = mutableStateOf<String?>(null)
    val line = mutableStateOf<Int?>(null)
    var started = false
    val asking = HashSet<String>()
    val treeScroll = LazyListState()
    val galleryScroll = HashMap<String, LazyListState>()
}

private val fileStates = java.util.WeakHashMap<Chat, FilesState>()

fun filesState(chat: Chat): FilesState = fileStates.getOrPut(chat) { FilesState() }

private class TreeNode(val path: String, val name: String, val dir: Boolean, val ignored: Boolean, val depth: Int)

private fun parent(path: String) = path.trimEnd('/').substringBeforeLast('/').ifEmpty { "/" }

private fun nameOf(path: String) = path.trimEnd('/').substringAfterLast('/').ifEmpty { "/" }

private fun inside(root: String, path: String) = path == root || path.startsWith(root.trimEnd('/') + "/")

/** A folder's entries read from the chat's host (list_dir), kept; `.git` isn't shown. */
private fun list(chat: Chat, st: FilesState, path: String) {
    Mesh.ask(chat.hostId, Msg.ListDir(path = path).json()) { r ->
        st.asking.remove(path)
        val d = Msg.Dir.from(r)
        if (r.optString("op") == Op.DIR && d.error.isNullOrBlank()) st.entries[path] = d.entries.orEmpty().filter { it.name != ".git" }
    }
}

private fun ensure(chat: Chat, st: FilesState, path: String) {
    if (path in st.entries || !st.asking.add(path)) return
    list(chat, st, path)
}

/** The chat's folder as Baude's Files page has it: a tree of folders to open (the long-press's menu: its gallery, its
 * path), or a gallery of one folder at a time; a file read (its text; a picture shown). Read from its host (list_dir,
 * read_file). */
@Composable
fun FilesPage(chat: Chat) {
    val ctx = LocalContext.current
    FilesPrefs.load(ctx)
    val cwd = Mesh.session(chat.hostId, chat.sid)?.cwd?.ifBlank { null } ?: return
    val st = filesState(chat)
    val root = st.root.value ?: cwd
    if (!st.started) {
        st.started = true
        if (FilesPrefs.gallery.value) st.gallery.value = root
    }
    LaunchedEffect(FileLinks.wanted.value) { // (a link in the chat to a file or folder of this host's: shown here)
        val w = FileLinks.wanted.value ?: return@LaunchedEffect
        if (w.hostId != chat.hostId) return@LaunchedEffect
        val at = if (w.dir) w.path else parent(w.path)
        var top = root
        if (!inside(root, at)) {
            top = at
            st.root.value = at
            st.open.clear()
        }
        st.gallery.value = null
        var p = at
        val chain = ArrayList<String>()
        while (inside(top, p)) {
            chain.add(p)
            if (p == top) break
            p = parent(p)
        }
        for (f in chain) st.open[f] = true
        if (w.dir) {
            st.file.value = null
            st.selected.value = w.path
        } else {
            st.selected.value = w.path
            st.file.value = w.path
            st.line.value = w.line
        }
        FileLinks.wanted.value = null
    }
    val shown = st.gallery.value
    val wanted = HashSet<String>().apply { add(root); shown?.let { add(it) }; st.open.filter { it.value }.keys.forEach { add(it) } }
    LaunchedEffect(wanted) { wanted.forEach { ensure(chat, st, it) } }
    st.file.value?.let { f -> return FileView(chat.hostId, f, st.line.value) { st.file.value = null; st.line.value = null } }
    Column(Modifier.fillMaxSize()) {
        FilesHeader(chat, st, root, shown)
        Crossfade(shown, Modifier.weight(1f).fillMaxWidth(), label = "files") { folder ->
            if (folder == null) FileTree(chat, st, root) else Gallery(chat, st, folder)
        }
    }
}

@Composable
private fun FilesHeader(chat: Chat, st: FilesState, root: String, shown: String?) {
    val c = LocalBaude.current
    val ctx = LocalContext.current
    val at = shown ?: root
    Row(Modifier.fillMaxWidth().padding(start = 10.dp, end = 8.dp, top = 8.dp, bottom = 4.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        if (shown != null) IconButton(Adwaita.GoUp, Wording.CodeView.upFolder, enabled = shown.trimEnd('/') != root.trimEnd('/')) {
            st.gallery.value = parent(shown)
        }
        Column(Modifier.weight(1f)) {
            Text(nameOf(at), style = Type.title, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(shortPath(chat.hostId, parent(at)), style = Type.caption, color = c.dim, maxLines = 1,
                overflow = TextOverflow.StartEllipsis)
        }
        if (shown == null) IconButton(Adwaita.ViewGrid, Wording.CodeView.showGallery) {
            val picked = st.selected.value
            st.gallery.value = picked?.let { p -> if (isFolder(st, p)) p else parent(p) } ?: root
            FilesPrefs.gallery.value = true
            FilesPrefs.save(ctx)
        } else IconButton(Adwaita.ViewList, Wording.CodeView.showTree) {
            st.gallery.value = null
            FilesPrefs.gallery.value = false
            FilesPrefs.save(ctx)
        }
        IconButton(Adwaita.ViewRefresh, Wording.CodeView.readFoldersAgain) {
            if (shown != null) list(chat, st, shown)
            else {
                list(chat, st, root)
                st.open.filter { it.value }.keys.forEach { list(chat, st, it) }
            }
        }
    }
}

private fun isFolder(st: FilesState, path: String): Boolean =
    st.entries[parent(path)]?.firstOrNull { it.name == nameOf(path) }?.dir == true

private fun rowsOf(st: FilesState, root: String): List<TreeNode> {
    val out = ArrayList<TreeNode>()
    fun walk(dir: String, depth: Int) {
        for (e in st.entries[dir].orEmpty()) {
            val name = e.name.orEmpty()
            val path = dir.trimEnd('/') + "/" + name
            val folder = e.dir == true
            out.add(TreeNode(path, name, folder, e.ignored == true, depth))
            if (folder && st.open[path] == true) walk(path, depth + 1)
        }
    }
    walk(root, 0)
    return out
}

@Composable
private fun FileTree(chat: Chat, st: FilesState, root: String) {
    val c = LocalBaude.current
    val rows = rowsOf(st, root)
    val through = through(rows.map { it.depth })
    LazyColumn(Modifier.fillMaxSize(), st.treeScroll, contentPadding = PaddingValues(vertical = 6.dp)) {
        items(rows.size, key = { rows[it].path }) { i ->
            val n = rows[i]
            TreeRow(chat, st, n, through[i], c)
        }
    }
}

/** A row's long-press, where it was pressed: its menu opens at the finger. */
@OptIn(ExperimentalFoundationApi::class)
private fun Modifier.pressed(at: (Offset) -> Unit, onClick: () -> Unit, onLong: () -> Unit): Modifier =
    this.pointerInput(Unit) { awaitEachGesture { at(awaitFirstDown(requireUnconsumed = false).position) } }
        .combinedClickable(onClick = onClick, onLongClick = onLong)

@Composable
private fun TreeRow(chat: Chat, st: FilesState, n: TreeNode, through: Set<Int>, c: BaudeColors) {
    val opened = st.open[n.path] == true
    val selected = st.selected.value == n.path
    var menu by remember { mutableStateOf(false) }
    var pressed by remember { mutableStateOf(Offset.Zero) }
    var height by remember { mutableStateOf(0) }
    Box(Modifier.onSizeChanged { height = it.height }) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 6.dp).padding(top = 1.dp).clip(RoundedCornerShape(6.dp))
            .then(if (selected) Modifier.background(c.rowSelected) else Modifier)
            .treeLines(n.depth, through, c.fg.copy(alpha = 0.3f), start = 6.dp, lineX = 10.dp)
            .pressed({ pressed = it }, {
                st.selected.value = n.path
                if (n.dir) {
                    st.open[n.path] = !opened
                    ensure(chat, st, n.path)
                } else {
                    st.file.value = n.path
                    st.line.value = null
                }
            }) { menu = true }
            .heightIn(min = 24.dp)
            .padding(start = 6.dp + 18.dp * n.depth, end = 6.dp, top = 1.dp, bottom = 1.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Box(Modifier.width(20.dp).alpha(if (n.ignored) 0.55f else 1f), contentAlignment = Alignment.Center) {
                Icon(if (!n.dir) Adwaita.TextXGeneric else if (opened) Adwaita.FolderOpen else Adwaita.Folder, null,
                    tint = c.fg, modifier = Modifier.size(16.dp.icon))
            }
            Text(n.name, style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.MiddleEllipsis,
                modifier = Modifier.weight(1f).alpha(if (n.ignored) 0.55f else 1f))
        }
        val d = LocalDensity.current
        Popover(menu, { menu = false }, with(d) { DpOffset(pressed.x.toDp(), (pressed.y - height).toDp()) }) {
            RowMenu(chat, st, n.path, n.dir) { menu = false }
        }
    }
}

/** A row's menu: a folder's gallery, and its path copied. */
@Composable
private fun RowMenu(chat: Chat, st: FilesState, path: String, dir: Boolean, close: () -> Unit) {
    val ctx = LocalContext.current
    if (dir) MenuChoice(Wording.CodeView.showGallery, false) {
        close()
        st.gallery.value = path
        FilesPrefs.gallery.value = true
        FilesPrefs.save(ctx)
    }
    MenuChoice(Wording.CodeView.copyPath, false) {
        close()
        ctx.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("Path", path))
        toast(Wording.CodeView.pathCopied)
    }
}

// ---- the gallery

private class Tile(val node: TreeNode, val shape: Float)

private val pictures = object : LruCache<String, Bitmap>(24 * 1024 * 1024) {
    override fun sizeOf(key: String, value: Bitmap) = value.byteCount
}

private val shapes = HashMap<String, Float>()

private fun isPicture(path: String) = path.substringAfterLast('.').lowercase() in PICTURES

/** A picture's shape (its width over its height, 0.25 to 4) from the head of its file (read_file), 1 if it can't be
 * told. */
private suspend fun shapeOf(hostId: String, path: String): Float = shapes.getOrPut("$hostId:$path") {
    val r = asked(hostId, Msg.ReadFile(path = path, limit = 65536).json())
    val f = Msg.File.from(r)
    if (r.optString("op") != Op.FILE || !f.error.isNullOrBlank()) return@getOrPut 1f
    val bytes = Base64.getDecoder().decode(f.data.orEmpty())
    val o = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeByteArray(bytes, 0, bytes.size, o)
    if (o.outWidth > 0 && o.outHeight > 0) (o.outWidth.toFloat() / o.outHeight).coerceIn(0.25f, 4f) else 1f
}

/** A picture read whole (read_file, 4 MB) and drawn no bigger than a tile needs: its long side at least [px]. */
private suspend fun pictureOf(hostId: String, path: String, px: Int): Bitmap? {
    val key = "$hostId:$path"
    pictures.get(key)?.let { return it }
    val r = asked(hostId, Msg.ReadFile(path = path, limit = 4 shl 20).json())
    val f = Msg.File.from(r)
    if (r.optString("op") != Op.FILE || !f.error.isNullOrBlank()) return null
    return withContext(Dispatchers.Default) {
        val bytes = Base64.getDecoder().decode(f.data.orEmpty())
        val o = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, o)
        var sample = 1
        while (maxOf(o.outWidth, o.outHeight) / (sample * 2) >= px) sample *= 2
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = sample })
    }?.also { pictures.put(key, it) }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Gallery(chat: Chat, st: FilesState, folder: String) {
    val c = LocalBaude.current
    val entries = st.entries[folder]
    val nodes = entries.orEmpty().map {
        TreeNode(folder.trimEnd('/') + "/" + it.name.orEmpty(), it.name.orEmpty(), it.dir == true, it.ignored == true, 0)
    }
    val dirs = nodes.filter { it.dir }
    val files = nodes.filter { !it.dir }
    val pics = files.filter { isPicture(it.path) }
    val known = remember(folder) { mutableStateMapOf<String, Float>() }
    LaunchedEffect(folder, entries) {
        for (p in pics) if (p.path !in known) known[p.path] = shapeOf(chat.hostId, p.path)
    }
    val ready = pics.all { it.path in known }
    val order = pics + files.filter { !isPicture(it.path) }
    val state = st.galleryScroll.getOrPut(folder) { LazyListState() }
    BoxWithConstraints(Modifier.fillMaxSize()) {
        val width = maxOf(120.dp, maxWidth - 16.dp)
        val rows = ArrayList<Pair<List<Tile>, Float>>()
        if (ready) {
            var line = ArrayList<Tile>()
            var sum = 0f
            for (n in order) {
                val shape = known[n.path] ?: 1f
                line.add(Tile(n, shape))
                sum += shape
                if (sum * 120 + 4 * (line.size - 1) >= width.value) {
                    rows.add(line to (width.value - 4 * (line.size - 1)) / sum)
                    line = ArrayList()
                    sum = 0f
                }
            }
            if (line.isNotEmpty()) rows.add(line to 120f)
        }
        LazyColumn(Modifier.fillMaxSize(), state, contentPadding = PaddingValues(horizontal = 8.dp, vertical = 6.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp)) {
            if (entries != null && dirs.isEmpty() && files.isEmpty()) item { Empty(Wording.CodeView.emptyFolder) }
            if (dirs.isNotEmpty()) item {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(4.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    for (d in dirs) Row(Modifier.clip(RoundedCornerShape(6.dp)).background(c.fg.copy(alpha = 0.10f))
                        .tip(d.name) { st.gallery.value = d.path }.padding(horizontal = 10.dp, vertical = 4.dp),
                        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        Icon(Adwaita.Folder, null, tint = c.fg, modifier = Modifier.size(16.dp.icon))
                        Text(if (d.name.length > 18) d.name.take(8) + "…" + d.name.takeLast(9) else d.name, style = Type.body,
                            color = c.fg, maxLines = 1)
                    }
                }
            }
            items(rows.size) { i ->
                val (tiles, height) = rows[i]
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    for (t in tiles) GalleryTile(chat, st, t, t.shape * height, height)
                }
            }
        }
    }
}

@Composable
private fun GalleryTile(chat: Chat, st: FilesState, t: Tile, w: Float, h: Float) {
    val c = LocalBaude.current
    val d = LocalDensity.current
    val n = t.node
    var bitmap by remember(n.path) { mutableStateOf(pictures.get("${chat.hostId}:${n.path}")) }
    if (isPicture(n.path)) LaunchedEffect(n.path) {
        if (bitmap == null) bitmap = pictureOf(chat.hostId, n.path, with(d) { maxOf(w, h).dp.roundToPx() })
    }
    Box(Modifier.size(w.dp, h.dp).clip(RoundedCornerShape(6.dp)).background(c.fg.copy(alpha = 0.06f))
        .tip(n.name) { st.selected.value = n.path; st.file.value = n.path; st.line.value = null },
        contentAlignment = Alignment.Center) {
        val b = bitmap
        if (b != null) Image(b.asImageBitmap(), n.name, Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
        else if (isPicture(n.path)) Icon(Adwaita.ImageXGeneric, null, tint = c.dim, modifier = Modifier.size(32.dp.icon))
        else Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Icon(Adwaita.TextXGeneric, null, tint = c.dim, modifier = Modifier.size(32.dp.icon))
            Text(n.name, style = Type.caption, color = c.fg, maxLines = 1, overflow = TextOverflow.MiddleEllipsis,
                textAlign = TextAlign.Center, modifier = Modifier.widthIn(max = (w - 8).dp))
        }
    }
}

// ---- a file

private val PICTURES = setOf("png", "jpg", "jpeg", "gif", "webp", "bmp")

/** A file of the host's, read (read_file): its text in the code's type, a picture shown, or why it can't be. */
@Composable
private fun FileView(hostId: String, path: String, line: Int? = null, back: () -> Unit) {
    val c = LocalBaude.current
    BackHandler(onBack = back)
    var data by remember(path) { mutableStateOf<ByteArray?>(null) }
    var why by remember(path) { mutableStateOf<String?>(null) }
    var cut by remember(path) { mutableStateOf(false) }
    LaunchedEffect(path) {
        Mesh.ask(hostId, Msg.ReadFile(path = path, limit = 1_000_000).json()) { r ->
            val f = Msg.File.from(r)
            if (r.optString("op") == Op.FILE && f.error.isNullOrBlank()) {
                data = Base64.getDecoder().decode(f.data.orEmpty())
                cut = f.truncated == true || (f.size?.toLong() ?: 0L) > (data?.size ?: 0)
            } else why = said(r, f.error).ifBlank { Wording.FileView.notText }
        }
    }
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(start = 12.dp, end = 6.dp, top = 6.dp, bottom = 2.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(path.substringAfterLast('/'), style = Type.title, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(Wording.FileView.onHost(host = Mesh.label(hostId)) + if (cut) " · its first part" else "", style = Type.caption, color = c.dim)
            }
            IconButton(Adwaita.GoPrevious, Wording.Browser.back, onClick = back)
        }
        Box(Modifier.fillMaxSize().padding(top = 6.dp).inside(c)) {
            val d = data
            when {
                why != null -> Empty(Wording.FileView.unreadable(path = path, host = Mesh.label(hostId), why = why!!))
                d == null -> {}
                path.substringAfterLast('.').lowercase() in PICTURES -> BitmapFactory.decodeByteArray(d, 0, d.size)?.let {
                    Image(it.asImageBitmap(), path, contentScale = ContentScale.Fit, modifier = Modifier.fillMaxSize().padding(8.dp))
                } ?: Empty("Not a picture this phone can read")
                d.take(8192).contains(0.toByte()) -> Empty(Wording.FileView.unreadable(path = path, host = Mesh.label(hostId),
                    why = Wording.FileView.notText))
                else -> {
                    val scroll = rememberScrollState()
                    val scope = androidx.compose.runtime.rememberCoroutineScope()
                    Text(String(d, Charsets.UTF_8), style = Type.mono, color = c.fg, modifier = Modifier.fillMaxSize()
                        .verticalScroll(scroll).horizontalScroll(rememberScrollState()).padding(12.dp),
                        onTextLayout = { layout -> // (a link's line: scrolled to, a line from the top)
                            if (line != null && line > 1 && line <= layout.lineCount && scroll.value == 0) scope.launch {
                                scroll.scrollTo(maxOf(0f, layout.getLineTop(line - 2)).toInt())
                            }
                        })
                }
            }
        }
    }
}

