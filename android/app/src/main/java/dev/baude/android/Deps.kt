package dev.baude.android

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp

/** What a page shows in its place when its host lacks a program it needs (depsview.Missing; schema/deps): its icon,
 * "Kubernetes needs kubectl", the command that installs it there with Copy (or, with none for that host's platform,
 * its install page), and Try again. feature: the page's name. */
@Composable
fun MissingDep(dep: Msg.Dep, feature: String, again: () -> Unit) {
    val c = LocalBaude.current
    val clip = LocalClipboardManager.current
    val uri = LocalUriHandler.current
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp, Alignment.CenterVertically), horizontalAlignment = Alignment.CenterHorizontally) {
        Icon(Adwaita.PackageXGeneric, null, tint = c.dim, modifier = Modifier.size(32.dp.icon))
        val name = feature.removeSuffix("…")
        Text(if (name.equals(dep.name, ignoreCase = true)) Wording.Deps.notInstalled(dep = dep.name.orEmpty())
            else Wording.Deps.needs(feature = name, dep = dep.name.orEmpty()), style = Type.title,
            color = c.fg, textAlign = TextAlign.Center)
        val fix = dep.fix
        val link = dep.link
        if (!fix.isNullOrBlank()) Row(Modifier.clip(RoundedCornerShape(Gap.codeRadius)).background(c.raised)
            .padding(start = 10.dp, top = 2.dp, bottom = 2.dp, end = 2.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            SelectionContainer(Modifier.weight(1f, fill = false)) { Text(fix, style = Type.mono, color = c.fg) }
            IconButton(Adwaita.EditCopy, Wording.Common.copy) {
                clip.setText(AnnotatedString(fix))
                toast(Wording.Kube.copiedTryAgain)
            }
        } else if (!link.isNullOrBlank()) Button(Wording.Deps.installPage) { try { uri.openUri(link) } catch (_: Exception) {} }
        Button(Wording.Common.tryAgain) { again() }
    }
}

/** A dependency's row (depsview.row): its name, then Installed, or the command that installs it with Copy (else its
 * install page). */
@Composable
fun DepRow(dep: Msg.Dep) {
    val c = LocalBaude.current
    val clip = LocalClipboardManager.current
    val uri = LocalUriHandler.current
    ActionRow(dep.name.orEmpty(), null, suffix = {
        val fix = dep.fix
        val link = dep.link
        if (dep.present == true) Text(Wording.Deps.installed, style = Type.body, color = c.dim)
        else if (!fix.isNullOrBlank()) Row(verticalAlignment = Alignment.CenterVertically) {
            Text(fix, style = Type.mono, color = c.fg, maxLines = 2, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.widthIn(max = 160.dp))
            IconButton(Adwaita.EditCopy, Wording.Common.copy) {
                clip.setText(AnnotatedString(fix))
                toast(Wording.Kube.copiedTryAgain)
            }
        } else if (!link.isNullOrBlank()) Button(Wording.Deps.installPage) { try { uri.openUri(link) } catch (_: Exception) {} }
    })
}

/** Settings → Dependencies (depsview.Page), Settings' third page: the host picked beside New session (a computer host:
 * the phone runs none of them), what its features need and whether it's there, as its deps answer says. */
@Composable
fun DepsPage() {
    val c = LocalBaude.current
    val hostId = picked()?.takeIf { it != "phone" } ?: Mesh.hosts.firstOrNull()?.id
    var listed by remember(hostId) { mutableStateOf<List<Msg.Dep>?>(null) }
    var error by remember(hostId) { mutableStateOf<String?>(null) }
    LaunchedEffect(hostId) {
        val h = hostId ?: return@LaunchedEffect
        Mesh.ask(h, Msg.Deps().json()) { r ->
            if (r.optString("op") == Op.ERROR) error = Msg.Error.from(r).said.orEmpty()
            else listed = Msg.DepsReply.from(r).deps.orEmpty()
        }
    }
    Column(Modifier.fillMaxSize().background(c.frame).verticalScroll(rememberScrollState())) {
        BoxedList(listOf(Unit), "") {
            ActionRow(Wording.NewSession.host, null, suffix = {
                Text(hostId?.let { Mesh.label(it) } ?: Wording.MeshVersions.none, style = Type.body, color = c.dim)
            })
        }
        val e = error
        val l = listed
        if (e != null) BoxedList(listOf(e), "") { ActionRow(it, null) }
        else if (l != null) BoxedList(l, "") { DepRow(it) }
    }
}
