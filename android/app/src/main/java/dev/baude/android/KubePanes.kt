package dev.baude.android

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.gestures.detectVerticalDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

private const val LOG_LINES = 20000 // (kubedata.KUBE_LOG_LINES: a log keeps this many lines, the oldest going)

/** A pod's logs shown: whose, its containers (init ones marked), the one picked, the text so far and what it's doing. */
class LogsShown(val context: String, val namespace: String, val pod: String, val containers: List<Pair<String, Boolean>>,
                container: String) {
    var container by mutableStateOf(container)
    var previous by mutableStateOf(false)
    var follow by mutableStateOf(true)
    var text by mutableStateOf("")
    var state by mutableStateOf("")
    var lines = 1
    var tail: KubeFollow? = null

    /** More lines; past a tenth over what's kept, the oldest go. */
    fun append(more: String) {
        val add = more.replace("\r\n", "\n")
        lines += add.count { it == '\n' }
        var t = text + add
        if (lines > LOG_LINES * 1.1) {
            var drop = lines - LOG_LINES
            var at = -1
            while (drop > 0) { at = t.indexOf('\n', at + 1); drop-- }
            t = t.substring(at + 1)
            lines = LOG_LINES
        }
        text = t
    }
}

/** A shell in a pod shown: whose, its running containers, the one picked, its terminal, and what it's doing (empty
 * while it runs, or how kubectl ended). */
class ShellShown(val context: String, val namespace: String, val pod: String, val containers: List<String>,
                 container: String) {
    var container by mutableStateOf(container)
    var term by mutableStateOf<HostTerm?>(null)
    var state by mutableStateOf("")
}

/** An object's YAML in the table's place: its name and a × to hide it, then the YAML with the names in it as links. */
@Composable
fun YamlLevel(st: KubeState, y: YamlShown) {
    val c = LocalBaude.current
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.padding(start = 10.dp, end = 6.dp, top = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(midCut(y.title, 48), style = Type.title, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f))
            IconButton(Adwaita.WindowClose, Wording.Kube.hide) { st.hideYaml() }
        }
        key(y.uid) {
            CodePane(y.text, y.file, Modifier.weight(1f), numbered = true, links = y.refs, open = { st.openRef(it) }, dim = y.loading)
        }
    }
}

/** The dock under the table (kubedock.KubeDock): a strip of tabs on the frame, its grey, as Freelens' dock has them (each
 * a pod's logs or a shell in one: its icon, "<pod> · <namespace>", ×; the picked one in the inside's colour, joined to
 * what's under it), and under it, inside, the picked one: its controls (the desktop has them at the strip's end, where
 * a phone has no room), then its log or terminal. Dragging the strip up or down makes it taller or shorter. */
@Composable
fun Dock(st: KubeState, height: Dp, room: Dp, set: (Dp) -> Unit) {
    val c = LocalBaude.current
    val k = kubeColours()
    val density = LocalDensity.current
    Column(Modifier.fillMaxWidth().height(height)) {
        val strip = rememberLazyListState()
        LaunchedEffect(st.picked) { st.docks.indexOf(st.picked).takeIf { it >= 0 }?.let { strip.animateScrollToItem(it) } }
        LazyRow(Modifier.fillMaxWidth().pointerInput(room) {
            detectVerticalDragGestures { _, d ->
                set((height - with(density) { d.toDp() }).coerceIn(k.logsMin, maxOf(k.logsMin, room - 80.dp)))
            }
        }, state = strip, contentPadding = PaddingValues(horizontal = Gap.inside), verticalAlignment = Alignment.Bottom,
            horizontalArrangement = Arrangement.spacedBy(2.dp)) {
            items(st.docks.toList(), key = { System.identityHashCode(it) }) { t -> DockTab(st, t) }
        }
        Column(Modifier.weight(1f).fillMaxWidth().inside(c)) {
            when (val p = st.picked) {
                is LogsShown -> {
                    LogsControls(st, p)
                    key(p) {
                        CodePane(p.text, "pod.log", Modifier.weight(1f).fillMaxWidth(), numbered = true, wrap = st.wrap,
                            follow = p.follow, onFollow = { if (it != p.follow) p.follow = it })
                    }
                }
                is ShellShown -> {
                    ShellControls(st, p)
                    p.term?.let { t -> key(t) { TerminalPane(t, Modifier.weight(1f).fillMaxWidth()) } }
                }
            }
        }
    }
}

/** A tab on the dock's strip: its icon, "<pod> · <namespace>", ×. Tapped, it's picked; ×, it's closed. */
@Composable
private fun DockTab(st: KubeState, t: Any) {
    val c = LocalBaude.current
    val (icon, pod, ns) = when (t) {
        is LogsShown -> Triple(Adwaita.Logs, t.pod, t.namespace)
        is ShellShown -> Triple(Adwaita.UtilitiesTerminal, t.pod, t.namespace)
        else -> return
    }
    val on = st.picked === t
    Row(Modifier.clip(RoundedCornerShape(topStart = Gap.codeRadius, topEnd = Gap.codeRadius))
        .background(if (on) c.inside else Color.Transparent).clickable { st.picked = t }
        .padding(start = 8.dp, end = 2.dp, top = 2.dp, bottom = 2.dp), verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        Icon(icon, null, tint = c.fg, modifier = Modifier.size(16.dp.icon))
        Text(midCut("$pod · $ns", 36), style = Type.bold, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis)
        IconButton(Adwaita.WindowClose, Wording.Common.close) { st.close(t) }
    }
}

/** A pod's logs' controls (build_logs'): what it's doing, the container, Previous, Follow, Wrap. */
@Composable
private fun LogsControls(st: KubeState, l: LogsShown) {
    val c = LocalBaude.current
    Row(Modifier.fillMaxWidth().heightIn(min = 34.dp).padding(start = 10.dp, end = 6.dp), verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(l.state, style = Type.caption, color = c.dim, maxLines = 1, overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f))
        Picker(midCut(shown(l, l.container), 28), Wording.Kube.container, l.containers.map { shown(l, it.first) to it.first },
            l.container) { l.container = it; st.tail(l) }
        Toggle(Wording.Kube.previous, l.previous) { l.previous = !l.previous; st.tail(l) }
        IconButton(Adwaita.GoBottom, Wording.Kube.followTip, background = if (l.follow) c.fg.copy(alpha = 0.10f) else null) {
            l.follow = !l.follow
        }
        IconButton(Adwaita.Wrap, Wording.CodeView.wrap, background = if (st.wrap) c.fg.copy(alpha = 0.10f) else null) {
            st.wrap = !st.wrap
            Mesh.app.getSharedPreferences("settings", android.content.Context.MODE_PRIVATE).edit().putBoolean("kube_log_wrap", st.wrap).apply()
        }
    }
}

/** A shell's controls (build_shell's): what it's doing (how kubectl ended), the container. */
@Composable
private fun ShellControls(st: KubeState, s: ShellShown) {
    val c = LocalBaude.current
    Row(Modifier.fillMaxWidth().heightIn(min = 34.dp).padding(start = 10.dp, end = 6.dp), verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(s.term?.said ?: s.state, style = Type.caption, color = c.dim, maxLines = 1, overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f))
        Picker(midCut(s.container, 28), Wording.Kube.container, s.containers.map { it to it }, s.container) {
            if (it != s.container) { s.container = it; st.startShell(s) }
        }
    }
}

private fun shown(l: LogsShown, name: String) =
    if (l.containers.firstOrNull { it.first == name }?.second == true) Wording.Kube.initContainer(name) else name

/** Ask before deleting a pod (force: at once, not waiting for it to stop), naming it, its namespace and its context, and
 * saying plainly when that's a remote cluster. Nothing runs unless you say Delete. */
@Composable
fun DeletePod(st: KubeState, row: Msg.KubeRow, force: Boolean) {
    val c = LocalBaude.current
    val ctx = st.context.orEmpty()
    val name = row.name.orEmpty()
    val body = listOfNotNull(
        Wording.Kube.inNamespace(row.namespace.orEmpty(), ctx),
        if (st.contexts.firstOrNull { it.name == ctx }?.local == false) Wording.Kube.remoteCluster(ctx) else null,
        if (force) Wording.Kube.forceDeleteBody else Wording.Kube.deleteBody,
        if (!row.ownerKind.isNullOrEmpty()) Wording.Kube.controlledBy(row.ownerKind, row.ownerName.orEmpty()) else Wording.Kube.noController,
    )
    val done = { st.deleting = null }
    AlertDialog(
        onDismissRequest = done,
        title = { Text(if (force) Wording.Kube.forceDeleteHeading(name) else Wording.Kube.deleteHeading(name)) },
        text = { Column(verticalArrangement = Arrangement.spacedBy(12.dp)) { for (p in body) Text(p, style = Type.body, color = c.fg) } },
        confirmButton = {
            Button(if (force) Wording.Kube.forceDeleteResponse else Wording.Common.delete, destructive = true) {
                done()
                st.delete(row, force)
            }
        },
        dismissButton = { Button(Wording.Common.cancel, onClick = done) },
        containerColor = c.dialog,
    )
}
