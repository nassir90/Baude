package dev.baude.android

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.positionInRoot
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import org.json.JSONObject

/** A tap on a Signal link in a reply, and what comes of it (BaudeWindow.open_ref): a file opens, a commit or branch shows
 * on the Git page, a pull request or issue opens in the browser, a tool shows its card, and everything else gets a
 * popover. */
object SignalLinks {
    /** The popover that's open: the name, and whether it's its places ("names") or its lookup ("lookup"). */
    class Pop(val surface: String, val kind: String)

    val open = mutableStateOf<Pop?>(null)
    var lastDown = Offset.Zero            // (the last touch's place, in the window: where a popover points)
    var openPage: (String) -> Unit = {}   // (the chat's side panel's page switcher)
    val wantCard = mutableStateOf<String?>(null) // (a name whose card the Signal page is asked to show)
    var hostId = ""
    var sid = ""

    /** The place a location is read from: the checkout's top, else the chat's folder. */
    private fun rootOf(): String? = SignalNames.reply(hostId, sid)?.root?.ifBlank { null } ?: Mesh.session(hostId, sid)?.cwd

    /** Open one of a name's locations on the Files page, at its line (a location on another branch (rev) is listed only). */
    fun openLocation(loc: JSONObject) {
        if (loc.str("rev") != null) return
        val path = loc.str("path") ?: return
        val full = if (path.startsWith("/")) path else (rootOf() ?: return).trimEnd('/') + "/" + path
        val line = if (loc.isNull("line")) null else loc.optInt("line").takeIf { it > 0 }
        FileLinks.open(Wanted(hostId, full, line, loc.str("role") == "folder"))
    }

    /** The Git page, at the commit (or the branch's tip) the name stands for. */
    fun showInGit(r: Msg.SignalRef) {
        GitLinks.want.value = r.found.str("sha") ?: r.surface
        openPage("git")
    }

    /** The page's card for a name, or the popover's open button's. */
    fun showCard(surface: String) {
        open.value = null
        wantCard.value = surface
        openPage("signal")
    }

    /** A Signal link was tapped: what it does, as the desktop's open_ref decides it, in its order. */
    fun tap(surface: String) {
        val r = SignalNames.known(hostId, sid)[surface]
        if ((r == null || r.status == PENDING) && refHashlike(surface)) { open.value = Pop(surface, "lookup"); return }
        if (r == null) return
        if (r.status == PENDING) { open.value = Pop(surface, "lookup"); return }
        val d = r.found
        if (r.status == RESOLVED && (r.kind == "commit" || r.kind == "branch")) { showInGit(r); return }
        if (r.status == RESOLVED && r.kind == "pr" && d.str("url") != null) {
            try {
                Mesh.app.startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW, android.net.Uri.parse(d.str("url")))
                    .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK))
            } catch (_: Exception) {}
            return
        }
        if (r.status == RESOLVED && (r.kind in setOf("tool", "subagent", "skill") || d.str("context") != null)) { showCard(surface); return }
        val picked = r.status == AMBIGUOUS && d.str("checked") == "haiku" && d.optJSONObject("pick") != null || d.str("pick") != null
        if (r.status == AMBIGUOUS && d.str("checked") == "haiku" && picked && r.places.isNotEmpty()) { openLocation(r.places[0]); return }
        if (r.status == RESOLVED && r.kind in setOf("file", "symbol", "constant", "config", "cli", "k8s") && r.places.size == 1 &&
            !(r.kind == "k8s" && refRunNames(r).isNotEmpty())) { openLocation(r.places[0]); return }
        open.value = Pop(surface, "names")
    }
}

/** Notes where a finger first touches, without taking it (a Signal link's popover points there). */
@Composable
fun Modifier.watchTouches(): Modifier {
    var origin by remember { mutableStateOf(Offset.Zero) }
    return this.onGloballyPositioned { origin = it.positionInRoot() }.pointerInput(Unit) {
        awaitPointerEventScope {
            while (true) {
                val e = awaitPointerEvent(PointerEventPass.Initial)
                e.changes.firstOrNull { it.pressed && !it.previousPressed }?.let { SignalLinks.lastDown = origin + it.position }
            }
        }
    }
}

/** The popovers a Signal link opens, pointing at where it was touched. */
@Composable
fun SignalPopovers(chat: Chat) {
    val pop = SignalLinks.open.value
    var origin by remember { mutableStateOf(Offset.Zero) }
    Box(Modifier.fillMaxSize().onGloballyPositioned { origin = it.positionInRoot() }) {
        val at = SignalLinks.lastDown - origin
        Box(Modifier.offset { IntOffset(at.x.toInt(), at.y.toInt()) }.size(1.dp)) {
            Popover(pop != null, { SignalLinks.open.value = null }) {
                if (pop != null) if (pop.kind == "lookup") LookupPopover(chat, pop.surface) else NamesPopover(chat, pop.surface)
            }
        }
    }
}

/** A name's kind (65%) and the name in monospace, as a popover or a card titles it. */
fun kindAndName(r: Msg.SignalRef, dim: androidx.compose.ui.graphics.Color): AnnotatedString = buildAnnotatedString {
    refKindLabel(r)?.let {
        withStyle(SpanStyle(color = dim)) { append(it) }
        append("  ")
    }
    withStyle(SpanStyle(fontFamily = Type.mono.fontFamily ?: FontFamily.Monospace, fontSize = Type.mono.fontSize)) {
        append(r.canonical ?: r.surface.orEmpty())
    }
}

/** What a link points at (RefPopover): its places, why it wasn't found, and icons to search again and to open its card. */
@Composable
private fun NamesPopover(chat: Chat, surface: String) {
    val c = LocalBaude.current
    val r = SignalNames.known(chat.hostId, chat.sid)[surface] ?: return
    val places = r.places
    val width = if (places.isEmpty()) 320.dp else LocalConfiguration.current.screenWidthDp.dp - 48.dp
    Column(Modifier.width(width).padding(horizontal = Popups.inset), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(verticalAlignment = Alignment.Top) {
            Text(kindAndName(r, c.fg.copy(alpha = c.fg.alpha * 0.65f)), style = Type.body, color = c.fg, modifier = Modifier.weight(1f).padding(top = 6.dp))
            IconButton(Adwaita.ViewRefresh, if (r.status == NOT_FOUND || r.status == STALE) Wording.Common.tryAgain
                else Wording.Signal.searchAgainOne, enabled = false) {}
            IconButton(Adwaita.GoNext, Wording.Common.open) { SignalLinks.showCard(surface) }
        }
        Text(refStatusText(r), style = Type.body, color = if (r.status == STALE) c.warning else c.dim)
        if (places.isNotEmpty()) Column(Modifier.heightIn(max = 28.dp * 12).verticalScroll(rememberScrollState())) {
            places.forEach { loc ->
                val path = (loc.str("path") ?: "").trimEnd('/')
                val role = loc.str("role").orEmpty()
                val name = (path.substringAfterLast('/').ifEmpty { path }) + (if (role == "folder") "/" else "") +
                    (if (!loc.isNull("line") && loc.optInt("line") > 0) ":${loc.optInt("line")}" else "")
                val where = path.substringBeforeLast('/', "") + (loc.str("note")?.let { "  ($it)" } ?: "")
                Row(Modifier.fillMaxWidth().heightIn(min = 28.dp).clickable { SignalLinks.open.value = null; SignalLinks.openLocation(loc) },
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Row(Modifier.weight(0.4f), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        RefIcons.Draw(refIcon(r.kind, r.found, listOf(role)) ?: "code", c.fg, 16.dp.icon)
                        Text(name, style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                    Text(role.replaceFirstChar { it.uppercase() }, style = Type.body, color = c.dim, maxLines = 1,
                        overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(0.2f))
                    Text(where, style = Type.body, color = c.dim, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(0.4f))
                }
            }
        }
    }
}

/** A name not looked up yet, or one whose lookup's asked for again (BaudeWindow.lookup_pending): "Looking it up…" while it
 * is, what it came to once the glossary says. */
@Composable
private fun LookupPopover(chat: Chat, surface: String) {
    val c = LocalBaude.current
    val r = SignalNames.known(chat.hostId, chat.sid)[surface]
    var asking by remember(surface) { mutableStateOf(false) }
    val version = SignalNames.version.intValue
    var seen by remember(surface) { mutableStateOf(version) }
    if (version != seen) { seen = version; asking = false }
    Column(Modifier.width(320.dp).padding(horizontal = Popups.inset), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(if (r != null && r.status != PENDING) kindAndName(r, c.fg.copy(alpha = c.fg.alpha * 0.65f)) else buildAnnotatedString {
            withStyle(SpanStyle(fontFamily = Type.mono.fontFamily ?: FontFamily.Monospace, fontSize = Type.mono.fontSize)) { append(surface) }
        }, style = Type.body, color = c.fg)
        when {
            r == null || r.status == PENDING || asking -> Row(verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Spinner()
                Text(Wording.Lookup.lookingItUp, style = Type.body, color = c.dim)
            }
            r.status == NOT_FOUND || r.status == STALE -> {
                Text(refStatusText(r), style = Type.body, color = c.dim)
                Button(Wording.Common.tryAgain, suggested = false) { asking = true; SignalNames.read(chat.hostId, chat.sid) }
            }
            else -> {
                Text(Wording.Signal.found(status = refStatusText(r)), style = Type.body, color = c.dim)
                Button(Wording.Common.open, suggested = false) { SignalLinks.open.value = null; SignalLinks.tap(surface) }
            }
        }
    }
}

/** The commit a link asks the Git page to show; the page takes it and clears it. */
object GitLinks {
    val want = androidx.compose.runtime.mutableStateOf<String?>(null)
}
