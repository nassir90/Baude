package dev.baude.android

import android.graphics.BitmapFactory
import android.util.LruCache
import androidx.activity.compose.BackHandler
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** A fix's images, decoded, by path: they never change. */
private val shots = LruCache<String, androidx.compose.ui.graphics.ImageBitmap>(24)

private fun statusLabel(status: String): String = when (status) {
    "open" -> Wording.Common.open
    "working" -> Wording.Fixes.statusWorking
    "ready" -> Wording.Fixes.statusReady
    "merged" -> Wording.References.prMerged
    "rejected" -> Wording.Fixes.statusRejected
    "closed" -> Wording.Fixes.statusClosed
    "saved" -> Wording.Fixes.statusSaved
    "filed" -> Wording.Fixes.statusFiled
    else -> Wording.Fixes.statusResolved
}

private fun roundLabel(state: String): String = when (state) {
    "working" -> Wording.Fixes.roundWorking
    "ready" -> Wording.Fixes.statusReady
    "failed" -> Wording.Fixes.roundFailed
    "stopped" -> Wording.Fixes.roundStopped
    "nochange" -> Wording.Fixes.roundNochange
    else -> Wording.Fixes.roundDiscarded
}

/** A status as a pill (.fix-status): its words tinted as the status is. */
@Composable
private fun StatusPill(status: String) {
    val c = LocalBaude.current
    val (fg, bg) = when (status) {
        "working" -> c.accent to c.accentBg.copy(alpha = 0.14f)
        "ready" -> Tokens.Accent.yellow to Tokens.Accent.yellow.copy(alpha = 0.16f)
        "merged", "resolved" -> c.success to c.success.copy(alpha = 0.14f)
        "rejected" -> c.error to c.error.copy(alpha = 0.14f)
        else -> c.fg.copy(alpha = 0.7f) to c.fg.copy(alpha = 0.08f)
    }
    Text(statusLabel(status), style = Type.body.copy(fontSize = Type.body.fontSize * Tokens.Fixes.statusSize.toFloat(),
        lineHeight = Type.body.lineHeight * Tokens.Fixes.statusSize.toFloat()), color = fg, maxLines = 1,
        modifier = Modifier.clip(RoundedCornerShape(Tokens.Radius.status)).background(bg)
            .padding(horizontal = Tokens.Fixes.statusPad))
}

/** What someone wrote, as markdown with its images as links: nothing is fetched from what an issue says (fixview.said). */
@Composable
private fun Said(text: String) = MarkdownView(Regex("!(\\[[^\\]]*\\]\\()").replace(text, "\$1"))

/** ☰ → Ongoing Feedback (fixview.FixesPage): the feedback and where each stands, a row opening its page over the list
 * (what the desktop's two panes show side by side); back from a page goes to the list. feedback: the + (Send
 * Feedback…). show: one to open as soon as it's read, by its key. */
@Composable
fun FixesScreen(back: () -> Unit, feedback: () -> Unit, show: String? = null) {
    val c = LocalBaude.current
    Mesh.tick.value
    var selected by rememberSaveable { mutableStateOf<String?>(null) }
    var showAll by rememberSaveable { mutableStateOf(false) }
    var reply by rememberSaveable { mutableStateOf("") }
    LaunchedEffect(Unit) {
        Fixes.last()
        Fixes.refresh()
        Fixes.poll()
    }
    val all = remember(Fixes.items, Fixes.readAt, Fixes.loading, Fixes.busy) { Fixes.all() }
    LaunchedEffect(show, all) {
        if (show != null && selected == null && all.any { Fixes.key(it) == show }) selected = show
    }
    BackHandler(selected != null) { selected = null }
    val item = selected?.let { s -> all.firstOrNull { Fixes.key(it) == s } }
    val waiting = all.count { it.optString("status") == "ready" }
    val title = if (waiting > 0) Wording.Fixes.tabWaiting(n = waiting) else Wording.Feedback.title
    Column(Modifier.fillMaxSize().background(if (item != null) c.inside else c.frame)) {
        Bar(title, start = { IconButton(Adwaita.GoPrevious, Wording.Browser.back) { if (selected != null) selected = null else back() } })
        if (item != null) Detail(item, reply, { reply = it })
        else Listing(all, showAll, { showAll = it }, feedback) { selected = it; reply = "" }
    }
}

@Composable
private fun androidx.compose.foundation.layout.ColumnScope.Listing(all: List<JSONObject>, showAll: Boolean, setAll: (Boolean) -> Unit, feedback: () -> Unit, open: (String) -> Unit) {
    val c = LocalBaude.current
    Row(Modifier.fillMaxWidth().padding(start = 10.dp, end = 8.dp, top = 8.dp, bottom = 8.dp), verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(Modifier.clip(RoundedCornerShape(8.dp)).background(c.fg.copy(alpha = 0.08f))) {
            for ((label, all) in listOf(Wording.Fixes.ongoing to false, Wording.Fixes.all to true))
                Text(label, style = Type.bold, color = c.fg,
                    modifier = Modifier.background(if (showAll == all) c.fg.copy(alpha = 0.15f) else Color.Transparent)
                        .clickable { setAll(all) }.padding(horizontal = 14.dp, vertical = 8.dp))
        }
        Box(Modifier.weight(1f))
        IconButton(Adwaita.ListAdd, Wording.Fixes.sendFeedback, onClick = feedback)
        val turn = rememberInfiniteTransition(label = "read").animateFloat(0f, 360f,
            infiniteRepeatable(tween(900, easing = LinearEasing), RepeatMode.Restart), label = "turn")
        Box(Modifier.rotate(if (Fixes.loading) turn.value else 0f)) {
            IconButton(Adwaita.ViewRefresh, Wording.Common.readAgain, onClick = Fixes::refresh)
        }
    }
    val listed = all.filter { showAll || it.optString("status") in Fixes.ONGOING }
    val nothing = all.isEmpty() && !Fixes.firstRead && (Fixes.repo == null || Fixes.error != null)
    Box(Modifier.weight(1f)) {
        if (nothing) {
            if (Fixes.repo == null) StatusPage(Adwaita.MailSend, Wording.Fixes.notConnected, Wording.Fixes.notConnectedBody)
            else StatusPage(Adwaita.MailSend, Wording.Fixes.unreadable, Fixes.error.orEmpty())
        } else LazyColumn(Modifier.fillMaxSize()) {
            if (listed.isEmpty()) item { Empty(if (Fixes.firstRead && Fixes.lastRead == 0L) Wording.Fixes.loading else Wording.Fixes.nothingOngoing) }
            items(listed, key = { Fixes.key(it) }) { row ->
                Column(Modifier.fillMaxWidth().heightIn(min = DENSE_ROW).clickable { open(Fixes.key(row)) }.padding(horizontal = 12.dp, vertical = 4.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(row.optString("title"), style = Type.body, color = c.fg, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                        StatusPill(row.optString("status"))
                        val where = Fixes.location(row).let { if (it.isEmpty()) "" else "$it · " }
                        Text(where + ago(row.optDouble("updated").toLong()), style = Type.caption, color = c.dim, maxLines = 1,
                            overflow = TextOverflow.Ellipsis)
                    }
                }
            }
        }
    }
    val foot = when {
        Fixes.firstRead -> if (Fixes.lastRead > 0 && Fixes.repo != null) Wording.Fixes.readFrom(where = Fixes.readFrom()) + ago(Fixes.lastRead / 1000) else ""
        Fixes.repo == null && Fixes.error == null -> Wording.Fixes.onlySavedHere
        Fixes.error != null -> Wording.Fixes.readFailed(why = Fixes.error.orEmpty().lines().first())
        else -> Wording.Fixes.readFromAt(where = Fixes.readFrom(), time = SimpleDateFormat("HH:mm", Locale.ENGLISH).format(Date(Fixes.readAt)))
    }
    if (foot.isNotEmpty() && !nothing) Text(foot, style = Type.caption, color = c.dim, maxLines = 1, overflow = TextOverflow.Ellipsis,
        modifier = Modifier.fillMaxWidth().navigationBarsPadding().padding(start = 12.dp, end = 12.dp, top = 6.dp, bottom = 8.dp))
}

/** One of them: what it is, what's been said on it and each round of its fix, and under that what can be done. */
@Composable
private fun Detail(item: JSONObject, reply: String, setReply: (String) -> Unit) {
    val c = LocalBaude.current
    val uri = LocalUriHandler.current
    var confirm by rememberSaveable { mutableStateOf<String?>(null) }
    val status = item.optString("status")
    val pr = item.optJSONObject("pr")
    Column(Modifier.fillMaxSize().imePadding()) {
        Box(Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState()), contentAlignment = Alignment.TopCenter) {
            Column(Modifier.widthIn(max = Tokens.Fixes.detailMax).fillMaxWidth()
                .padding(start = 18.dp, end = 18.dp, top = 14.dp, bottom = 14.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                SelectionContainer {
                    Text(item.optString("title"), style = Type.title.copy(fontSize = Type.title.fontSize * 1.4f), color = c.fg)
                }
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
                    StatusPill(status)
                    val said = listOf(Fixes.location(item), item.str("author")
                        ?.let { Wording.Common.fromWho(author = it) }.orEmpty(), ago(item.optDouble("created").toLong()))
                    Text(said.filter { it.isNotEmpty() }.joinToString(" · "), style = Type.caption, color = c.dim, maxLines = 1,
                        overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                }
                val url = item.optString("url")
                if (url.isNotEmpty() || pr != null) Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    if (url.isNotEmpty()) Link(Wording.Lookup.issue) { try { uri.openUri(url) } catch (_: Exception) {} }
                    pr?.let { p ->
                        Link(Wording.Fixes.pr(number = p.optInt("number"))) { try { uri.openUri(p.optString("url")) } catch (_: Exception) {} }
                    }
                }
                item.optString("body").takeIf { it.isNotEmpty() }?.let { Said(it) }
                val thread = item.optJSONArray("thread")
                for (i in 0 until (thread?.length() ?: 0)) {
                    val e = thread!!.getJSONObject(i)
                    val mark = e.optJSONObject("mark")
                    if (mark?.optString("state") != "working" && (mark != null || e.optString("text").isNotEmpty())) Entry(e, mark)
                }
            }
        }
        ActionBar(item, reply, setReply) { confirm = it }
    }
    if (confirm == "merge" && pr != null) AlertDialog(onDismissRequest = { confirm = null },
        title = { Text(Wording.Fixes.mergeHeading(issue = Fixes.location(item))) },
        text = { Text(Wording.Fixes.mergeBody(number = pr.optInt("number"))) },
        confirmButton = { Button(Wording.Fixes.merge, suggested = true) { confirm = null; Fixes.run("merge", item) } },
        dismissButton = { Button(Wording.Common.cancel) { confirm = null } }, containerColor = c.dialog)
    if (confirm == "discard" && pr != null) AlertDialog(onDismissRequest = { confirm = null },
        title = { Text(Wording.Fixes.discardHeading(issue = Fixes.location(item))) },
        text = { Text(Wording.Fixes.discardBody(number = pr.optInt("number"))) },
        confirmButton = { Button(Wording.Common.discard, destructive = true) { confirm = null; Fixes.run("discard", item) } },
        dismissButton = { Button(Wording.Common.cancel) { confirm = null } }, containerColor = c.dialog)
}

/** A flat text button (Issue, PR #N). */
@Composable
private fun Link(label: String, onClick: () -> Unit) {
    val c = LocalBaude.current
    Text(label, style = Type.bold, color = c.accent, maxLines = 1,
        modifier = Modifier.clip(RoundedCornerShape(8.dp)).clickable(onClick = onClick).padding(horizontal = 8.dp, vertical = 6.dp))
}

/** A round of the fix, or something someone said (fixview.entry). */
@Composable
private fun Entry(e: JSONObject, mark: JSONObject?) {
    val c = LocalBaude.current
    val failed = mark?.optString("state") == "failed"
    val frame = if (mark != null) Modifier.border(1.dp, if (failed) c.error else c.rule, RoundedCornerShape(Gap.cardRadius))
        .padding(horizontal = Tokens.Fixes.roundPadH, vertical = Tokens.Fixes.roundPadV) else Modifier
    Column(Modifier.fillMaxWidth().then(frame), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(if (mark != null) roundLabel(mark.optString("state")) else e.optString("author"), style = Type.bold, color = c.fg,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
            val machine = mark?.str("machine")?.let { " · $it" }.orEmpty()
            Text(ago(e.optDouble("at").toLong()) + machine, style = Type.caption, color = c.dim, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        e.optString("text").takeIf { it.isNotEmpty() }?.let { Said(it) }
        val list = mark?.optJSONArray("shots")
        for (i in 0 until (list?.length() ?: 0)) Shot(list!!.getJSONObject(i))
    }
}

/** One thing shown, before and after, side by side (fixview.shot). */
@Composable
private fun Shot(shot: JSONObject) {
    val c = LocalBaude.current
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        shot.optString("label").takeIf { it.isNotEmpty() }?.let { Text(it, style = Type.body, color = c.fg) }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            for ((side, name, none) in listOf(Triple("before", Wording.Fixes.before, Wording.Fixes.notThereBefore),
                Triple("after", Wording.Fixes.after, Wording.Fixes.none))) {
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(name, style = Type.caption, color = c.dim)
                    val path = shot.str(side)
                    if (path == null) Text(none, style = Type.caption, color = c.dim) else ShotImage(path)
                }
            }
        }
    }
}

/** An image of a fix: a spinner in its place till it's read, then it fitted to the column. */
@Composable
private fun ShotImage(path: String) {
    val c = LocalBaude.current
    var image by remember(path) { mutableStateOf(shots.get(path)) }
    var failed by remember(path) { mutableStateOf(false) }
    LaunchedEffect(path) {
        if (image != null) return@LaunchedEffect
        val h = Fixes.host() ?: run { failed = true; return@LaunchedEffect }
        Mesh.ask(h, Msg.Fixes(what = "shot", path = path).json()) { r ->
            val bytes = Msg.FixesReply.from(r).data?.let { try { java.util.Base64.getDecoder().decode(it) } catch (_: Exception) { null } }
            val bmp = bytes?.let { BitmapFactory.decodeByteArray(it, 0, it.size) }?.asImageBitmap()
            if (bmp == null) failed = true else {
                shots.put(path, bmp)
                image = bmp
            }
        }
    }
    val shown = image
    Box(Modifier.fillMaxWidth().heightIn(min = Tokens.Fixes.imageSlot), contentAlignment = Alignment.Center) {
        if (shown != null) Image(shown, null, Modifier.fillMaxWidth().aspectRatio(shown.width.toFloat() / shown.height).border(1.dp, c.rule),
            contentScale = ContentScale.Fit)
        else if (failed) Text(Wording.Fixes.imageUnreadable, style = Type.caption, color = c.dim)
        else Spinner()
    }
}

/** What can be done with it, by where it stands (fixview.sync_bar, hint_for). */
@Composable
private fun ActionBar(item: JSONObject, reply: String, setReply: (String) -> Unit, ask: (String) -> Unit) {
    val c = LocalBaude.current
    val status = item.optString("status")
    val failure = item.opt("error")?.takeIf { it != JSONObject.NULL }?.toString().orEmpty()
    val working = item.optJSONObject("working")
    val pr = item.optJSONObject("pr")
    val can = Fixes.canWrite
    val writes = can && !Fixes.busy
    val replies = status == "ready" && can && Fixes.ours(item)
    val hint = when {
        status == "filed" -> Wording.Fixes.filedUnreadable
        status == "working" -> if (working != null) {
            val machine = working.str("machine").orEmpty()
            if (machine.isNotEmpty()) Wording.Fixes.runningOn(issue = Fixes.location(item), machine = machine)
            else Wording.Fixes.runningElsewhere(issue = Fixes.location(item))
        } else Wording.Fixes.draft(number = pr?.optInt("number") ?: 0)
        !can -> if (status == "open" || status == "ready") Wording.Fixes.writeAccess(repo = Fixes.repo.orEmpty()) else ""
        failure.isNotEmpty() && (status == "open" || status == "ready") -> Wording.Fixes.fixFailed(why = failure)
        else -> ""
    }
    val stop = can && status == "working" && item.optBoolean("here")
    val reopen = can && status == "rejected"
    val discard = can && status == "ready"
    val merge = can && status == "ready"
    val fix = can && status == "open"
    if (hint.isEmpty() && !Fixes.busy && !replies && !(stop || reopen || discard || merge || fix)) return
    HorizontalDivider(color = c.rule)
    Column(Modifier.fillMaxWidth().navigationBarsPadding().padding(start = 14.dp, end = 14.dp, top = 8.dp, bottom = 10.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (hint.isNotEmpty()) Text(hint, style = Type.caption, color = c.dim)
        fun send() {
            if (writes && reply.isNotBlank()) Fixes.run("reply", item, reply.trim()) { setReply("") }
        }
        if (replies) Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.weight(1f).clip(RoundedCornerShape(8.dp)).background(c.fg.copy(alpha = 0.08f))
                .padding(horizontal = 10.dp, vertical = 9.dp)) {
                if (reply.isEmpty()) Text(Wording.Fixes.askForChanges, style = Type.body, color = c.dim)
                BasicTextField(reply, setReply, enabled = writes, singleLine = true, textStyle = Type.body.copy(color = c.fg),
                    cursorBrush = SolidColor(c.accentBg), keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                    keyboardActions = KeyboardActions(onSend = { send() }), modifier = Modifier.fillMaxWidth())
            }
            Button(Wording.Common.send, enabled = writes && reply.isNotBlank()) { send() }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End), verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.fillMaxWidth()) {
            if (Fixes.busy) Spinner()
            if (stop) Button(Wording.Common.stop, enabled = writes) { Fixes.run("stop", item) }
            if (reopen) Button(Wording.Fixes.reopen, enabled = writes) { Fixes.run("reopen", item) }
            if (discard) Button(Wording.Common.discard, destructive = true, enabled = writes) { ask("discard") }
            if (merge) Button(Wording.Fixes.merge, suggested = true, enabled = writes) { ask("merge") }
            if (fix) Button(Wording.Fixes.fixHere, suggested = true, enabled = writes) { Fixes.run("fix", item) }
        }
    }
}
