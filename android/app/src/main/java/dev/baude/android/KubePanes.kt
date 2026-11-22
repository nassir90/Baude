package dev.baude.android

import androidx.compose.foundation.background
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
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
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

/** The pod's logs, docked under (build_logs): its name, what it's doing, the container, Previous, Follow, Wrap, close;
 * the log followed under that. A bar between it and what's above drags it taller or shorter. */
@Composable
fun LogsPane(st: KubeState, l: LogsShown, height: Dp, room: Dp, set: (Dp) -> Unit) {
    val c = LocalBaude.current
    val k = kubeColours()
    val density = LocalDensity.current
    DisposableEffect(l) { onDispose { l.tail?.stop() } }
    Box(Modifier.fillMaxWidth().height(6.dp).pointerInput(room) {
        detectVerticalDragGestures { _, d ->
            set((height - with(density) { d.toDp() }).coerceIn(k.logsMin, maxOf(k.logsMin, room - 80.dp)))
        }
    }, contentAlignment = Alignment.Center) { Box(Modifier.fillMaxWidth().height(1.dp).background(c.rule)) }
    Column(Modifier.fillMaxWidth().height(height)) {
        Row(Modifier.fillMaxWidth().heightIn(min = 34.dp).padding(start = 10.dp, end = 6.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Icon(Adwaita.Logs, null, tint = c.fg, modifier = Modifier.size(16.dp.icon))
            Text(midCut("${l.pod} · ${l.namespace}", 36), style = Type.bold, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f, fill = false))
            Text(l.state, style = Type.caption, color = c.dim, maxLines = 1, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f, fill = false))
            Picker(midCut(shown(l, l.container), 28), Wording.Kube.container, l.containers.map { shown(l, it.first) to it.first },
                l.container) { l.container = it; st.tail(l) }
            Toggle(Wording.Kube.previous, l.previous) { l.previous = !l.previous; st.tail(l) }
            IconButton(Adwaita.GoBottom, Wording.Kube.followTip, background = if (l.follow) c.fg.copy(alpha = 0.10f) else null) {
                l.follow = !l.follow
            }
            IconButton(Adwaita.FormatJustifyFill, Wording.CodeView.wrap, background = if (st.wrap) c.fg.copy(alpha = 0.10f) else null) {
                st.wrap = !st.wrap
                Mesh.app.getSharedPreferences("settings", android.content.Context.MODE_PRIVATE).edit().putBoolean("kube_log_wrap", st.wrap).apply()
            }
            IconButton(Adwaita.WindowClose, Wording.Kube.closeLogs) { st.closeLogs() }
        }
        CodePane(l.text, "pod.log", Modifier.weight(1f), numbered = true, wrap = st.wrap, follow = l.follow,
            onFollow = { if (it != l.follow) l.follow = it })
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
