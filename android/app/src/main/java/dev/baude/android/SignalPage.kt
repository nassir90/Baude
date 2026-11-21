package dev.baude.android

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.border
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Icon
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import org.json.JSONObject

private const val TOOL_DOCS_PAGE = "https://code.claude.com/docs/en/tools-reference"

/** Signal's names for the chat, as Baude's Signal page lists them (SignalPage): those its replies named that the folder's
 * glossary has, by kind, each how it stands (linked, several matches, looking up, not found, stale) and what it is; a
 * name opens its card. Read from its host (signal_names) each time the page is shown. The reader's, language servers'
 * and tools' switches, and the search-again buttons, are drawn as the desktop has them and dimmed. */
@Composable
fun SignalPage(hostId: String, sid: String, here: Boolean) {
    val c = LocalBaude.current
    val s = Mesh.session(hostId, sid)
    if (s?.cwd.isNullOrBlank()) return
    SignalChats.version.intValue
    var filter by remember(hostId, sid) { mutableStateOf("") }
    var card by remember(hostId, sid) { mutableStateOf<String?>(null) }
    LaunchedEffect(here) { if (here) SignalNames.read(hostId, sid) }
    val want = SignalLinks.wantCard.value
    LaunchedEffect(want) { // (a link's or a popover's open: that name's card)
        if (want != null) { card = want; SignalLinks.wantCard.value = null }
    }
    val g = SignalNames.reply(hostId, sid)
    val known = SignalNames.known(hostId, sid)
    val off = SignalChats.off(hostId, sid)
    val open = card?.let { known[it] }
    if (open != null) { // (the card rebuilt from the latest reading as it comes)
        NameCard(hostId, open, g?.root) { card = null }
        return
    }
    val strong = SignalNames.strong()
    val texts = SignalNames.texts()
    val shown = remember(g, filter, strong) { g?.refs.orEmpty().filter { r ->
        val surface = r.surface.orEmpty()
        val keep = !r.found.optBoolean("plain") && !refCloudProvider(surface) &&
            !((r.status == NOT_FOUND || r.status == PENDING) && surface !in strong)
        val f = filter.trim().lowercase()
        keep && (f.isEmpty() || surface.lowercase().contains(f) || r.canonical.orEmpty().lowercase().contains(f))
    } }
    val firsts = remember(texts, shown) { shown.associate { r -> r.surface.orEmpty() to texts.indexOf(r.surface.orEmpty()).let { if (it < 0) Int.MAX_VALUE else it } } }
    Scrolled {
        Row(Modifier.padding(start = 8.dp, end = 6.dp, top = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            SearchField(filter, { filter = it }, Wording.Signal.filter, Modifier.weight(1f))
            IconButton(Adwaita.ViewRefresh, Wording.Signal.searchAgain, enabled = false) {}
        }
        OptionsRow()
        if (off) {
            Empty(Wording.Signal.off)
            return@Scrolled
        }
        val counts = listOf<Pair<String, (Int) -> String>>(RESOLVED to { Wording.Signal.linked(n = it) },
            AMBIGUOUS to { Wording.Signal.several(n = it) }, PENDING to { Wording.Signal.lookingUp(n = it) },
            NOT_FOUND to { Wording.Signal.notFound(n = it) }, STALE to { Wording.Signal.stale(n = it) })
            .filter { (st, _) -> shown.any { it.status == st } }.joinToString(" · ") { (st, say) -> say(shown.count { it.status == st }) }
        Text(counts + (g?.glossary?.ifBlank { null }?.let { "\n" + Wording.Signal.glossary(path = pretty(it.removeSuffix("/signal.db"), hostId)) } ?: ""),
            style = Type.caption, color = c.dim, modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp))
        Text(Wording.Signal.notRunning(n = g?.refs.orEmpty().size), style = Type.caption.copy(fontSize = Type.body.fontSize * 0.9f),
            color = c.fg, modifier = Modifier.padding(horizontal = 12.dp, vertical = 2.dp))
        if (shown.isEmpty()) Empty(Wording.Signal.empty)
        val by = shown.groupBy { refGroup(it) }
        for (group in GROUP_ORDER) {
            val rows = by[group] ?: continue
            val first = { r: Msg.SignalRef -> firsts[r.surface.orEmpty()] ?: Int.MAX_VALUE }
            Text(if (group == "pending") Wording.Signal.lookingUpGroup else KINDS[group] ?: group, style = Type.folder, color = c.dim,
                modifier = Modifier.padding(start = 14.dp, top = 8.dp))
            BoxedList(rows.sortedWith(compareBy({ first(it) }, { it.surface.orEmpty().lowercase() })), "") { r ->
                NameRow(r) { card = r.surface }
            }
        }
    }
}

/** A path as the host's home gives it: ~ for the home. */
private fun pretty(path: String, hostId: String): String {
    val home = Mesh.conn(hostId)?.info?.home?.trimEnd('/') ?: return path
    return if (path == home || path.startsWith("$home/")) "~" + path.removePrefix(home) else path
}

/** What Signal may use, as toggle buttons (the reader's model, LSP, Tools) and the switch for the chat, dimmed. */
@Composable
private fun OptionsRow() {
    Row(Modifier.padding(horizontal = 12.dp, vertical = 8.dp), horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalAlignment = Alignment.CenterVertically) {
        OptionToggle("Sonnet", Wording.Signal.readerTip, true)
        OptionToggle(Wording.Signal.lsp, Wording.Signal.lspTip, false)
        OptionToggle(Wording.Lookup.tools, Wording.Signal.toolsTip, false)
        Spacer(Modifier.weight(1f))
        val c = LocalBaude.current
        Switch(false, null, enabled = false, modifier = Modifier.semantics { contentDescription = Wording.Signal.thisChat },
            colors = SwitchDefaults.colors(checkedTrackColor = c.accentBg, checkedThumbColor = c.accentFg))
    }
}

/** A toggle button as .ref-option has it: pressed, on the accent's wash. */
@Composable
private fun OptionToggle(label: String, tip: String, pressed: Boolean) {
    val c = LocalBaude.current
    val shape = RoundedCornerShape(6.dp)
    Text(label, style = Type.body, color = if (pressed) c.accent else c.fg, maxLines = 1,
        modifier = Modifier.alpha(0.5f).semantics { contentDescription = tip }.heightIn(min = 26.dp).clip(shape)
            .then(if (pressed) Modifier.background(c.accentBg.copy(alpha = 0.22f)) else Modifier)
            .border(1.dp, if (pressed) c.accent.copy(alpha = 0.45f) else c.fg.copy(alpha = 0.18f), shape)
            .clickable(enabled = false) {}.padding(horizontal = 12.dp, vertical = 2.dp))
}

/** A name's row (SignalPage.row): its status icon, the name in monospace, what was found under it. */
@Composable
private fun NameRow(r: Msg.SignalRef, open: () -> Unit) {
    val c = LocalBaude.current
    val (icon, tint) = when (r.status) {
        RESOLVED -> Adwaita.ObjectSelect to c.success
        AMBIGUOUS -> Adwaita.ViewMore to c.fg
        NOT_FOUND -> Adwaita.DialogQuestion to c.dim
        STALE -> Adwaita.DialogWarning to c.warning
        else -> Adwaita.ContentLoading to c.dim
    }
    Row(Modifier.fillMaxWidth().clickable(onClick = open).padding(horizontal = 8.dp, vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Icon(icon, r.status, tint = tint, modifier = Modifier.size(16.dp.icon))
        Column(Modifier.weight(1f)) {
            Text(r.surface.orEmpty(), style = Type.mono, color = c.fg, maxLines = 1, overflow = TextOverflow.MiddleEllipsis)
            Text(r.summary.orEmpty(), style = Type.caption, color = c.dim, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
    }
}

// ---------------------------------------------------------------------------------------------------- the card --

/** A name's card (SignalPage.show_ref): what it is, how it was found, its facts, its locations (each opens), Refresh. */
@Composable
private fun NameCard(hostId: String, r: Msg.SignalRef, root: String?, back: () -> Unit) {
    val c = LocalBaude.current
    val d = r.found
    val surface = r.surface.orEmpty()
    BackHandler(onBack = back)
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.padding(horizontal = 8.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            IconButton(Adwaita.GoPrevious, Wording.Signal.allNames, onClick = back)
            Text(surface, style = Type.title, color = c.fg, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).padding(horizontal = 12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)) {
            SelectionContainer { Text(kindAndName(r, c.fg.copy(alpha = c.fg.alpha * 0.65f)), style = Type.body, color = c.fg) }
            val facts = r.status == RESOLVED && (r.kind in setOf("commit", "branch", "pr", "tool", "subagent", "skill") || d.str("context") != null)
            if (!facts) Text(refStatusText(r), style = Type.body, color = if (r.status == STALE) c.warning else c.dim)
            for (line in facts(r, hostId)) SelectionContainer { Text(line, style = Type.body, color = c.fg) }
            if (r.kind == "tool" && r.status == RESOLVED) ToolExtras(r)
            val places = r.places
            if (places.isNotEmpty()) {
                Text(Wording.Signal.locations, style = Type.folder, color = c.dim)
                Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    places.forEach { LocationRow(it, hostId, root) }
                }
                places.first().let { first ->
                    first.str("state")?.let { state ->
                        Text(Wording.SignalCard.readFrom(state = state) +
                            (first.str("commit_sha")?.let { Wording.SignalCard.readFromAt(sha = it.take(9)) } ?: "") +
                            (first.str("branch")?.let { Wording.SignalCard.readFromOn(branch = it) } ?: ""),
                            style = Type.caption, color = c.fg.copy(alpha = c.fg.alpha * 0.6f))
                    }
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Box(Modifier.semantics { contentDescription = Wording.Signal.refreshTip }) {
                    Button(Wording.Signal.refresh, enabled = false) {}
                }
                if ((r.kind == "commit" || r.kind == "branch") && r.status == RESOLVED) Box(Modifier.semantics {
                    contentDescription = Wording.Signal.showInGitTip }) {
                    Button(Wording.Signal.showInGit) { SignalLinks.showInGit(r) }
                }
            }
        }
    }
}

/** One of a name's locations, a flat button: "<role> · <path>:<line>" and its note. */
@Composable
private fun LocationRow(loc: JSONObject, hostId: String, root: String?) {
    val c = LocalBaude.current
    val line = if (loc.isNull("line")) null else loc.optInt("line").takeIf { it > 0 }
    Text((loc.str("role") ?: "") + " · " + (loc.str("path") ?: "") + (line?.let { ":$it" } ?: "") + (loc.str("note")?.let { "  ($it)" } ?: ""),
        style = Type.body, color = c.fg, maxLines = 1, overflow = TextOverflow.MiddleEllipsis,
        modifier = Modifier.fillMaxWidth().heightIn(min = 24.dp).clickable { SignalLinks.openLocation(loc) }.padding(horizontal = 6.dp, vertical = 2.dp))
}

/** A tool's inputs (a list), its output type, and this chat's calls to it (SignalPage.tool_card). */
@Composable
private fun ToolExtras(r: Msg.SignalRef) {
    val c = LocalBaude.current
    val d = r.found
    val fields = d.optJSONArray("input")
    val dim60 = c.fg.copy(alpha = c.fg.alpha * 0.6f)
    if (fields != null && fields.length() > 0) {
        Text(Wording.SignalCard.inputs, style = Type.folder, color = c.dim)
        BoxedList((0 until fields.length()).mapNotNull { fields.optJSONObject(it) }, "") { f ->
            SelectionContainer {
                Text(buildAnnotatedString {
                    withStyle(SpanStyle(fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold)) { append(f.optString("name")) }
                    f.str("type")?.let { withStyle(SpanStyle(fontFamily = FontFamily.Monospace)) { append("  " + it.take(200)) } }
                    if (f.optBoolean("required")) withStyle(SpanStyle(color = dim60)) { append("  " + Wording.SignalCard.required) }
                    f.str("doc")?.let { withStyle(SpanStyle(color = c.fg.copy(alpha = c.fg.alpha * 0.75f))) { append("\n$it") } }
                }, style = Type.body, color = c.fg, modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp))
            }
        }
    } else if (fields != null) Text(Wording.SignalCard.noInputs, style = Type.body, color = dim60)
    d.str("output_text")?.let { out ->
        var open by remember(out) { mutableStateOf(false) }
        Row(Modifier.fillMaxWidth().clickable { open = !open }.heightIn(min = 32.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Icon(if (open) Adwaita.PanDown else Adwaita.GoNext, null, tint = c.fg, modifier = Modifier.size(16.dp.icon))
            Text(Wording.Meta.output, style = Type.body, color = c.fg)
        }
        if (open) CodeBlock(out, "ts")
    }
    val name = d.str("tool") ?: r.canonical
    val chat = Mesh.open
    val calls = chat?.calls?.byId?.values.orEmpty().filter { it.name == name && it.input.length() > 0 }
    if (calls.isNotEmpty()) {
        Text(buildAnnotatedString {
            withStyle(SpanStyle(fontWeight = FontWeight.Bold)) {
                append(Wording.SignalCard.called(n = calls.size) + if (calls.size > 1) Wording.SignalCard.lastOnes else ":")
            }
        }, style = Type.body, color = c.fg)
        for (call in calls.takeLast(2)) CodeBlock(call.input.toString(2).take(1500), "json")
    }
}

// ---------------------------------------------------------------------------------------------------- the facts --

private class Ink(val dim: Color, val accent: Color, val mono: SpanStyle)

private fun AnnotatedString.Builder.tt(i: Ink, s: String) = withStyle(i.mono) { append(s) }
private fun AnnotatedString.Builder.faint(i: Ink, s: String, alpha: Float = 0.7f) =
    withStyle(SpanStyle(color = i.dim.copy(alpha = alpha))) { append(s) }
private fun AnnotatedString.Builder.link(i: Ink, url: String, text: String) {
    pushLink(LinkAnnotation.Url(url, TextLinkStyles(SpanStyle(color = i.accent, textDecoration = TextDecoration.Underline))))
    append(text)
    pop()
}

/** A message with one value in it that's set apart (in monospace): the message is said with a marker where the value
 * goes, and the value put there. */
private fun AnnotatedString.Builder.marked(i: Ink, say: (String) -> String, value: String) {
    val parts = say("\u0000").split("\u0000")
    append(parts[0]); tt(i, value); append(parts.getOrElse(1) { "" })
}

/** Lines about a commit, branch or pull request (GitHub links, dates, lines changed, PRs), or a tool, subagent type or
 * skill (what it is, where its schema came from): SignalPage.facts. */
@Composable
private fun facts(r: Msg.SignalRef, hostId: String): List<AnnotatedString> {
    val c = LocalBaude.current
    val i = Ink(c.fg, c.accent, SpanStyle(fontFamily = Type.mono.fontFamily ?: FontFamily.Monospace, fontSize = Type.mono.fontSize))
    val d = r.found
    val out = ArrayList<AnnotatedString>()
    fun line(f: AnnotatedString.Builder.() -> Unit) { out.add(buildAnnotatedString(f)) }
    fun has(k: String) = d.has(k) && !d.isNull(k)
    if (r.status != RESOLVED) return out
    if (r.kind == "tool") {
        d.str("description")?.let { line { append(it) } }
        if (has("permission")) line { append(if (d.optBoolean("permission")) Wording.SignalCard.asksYes else Wording.SignalCard.asksNo) }
        val server = d.str("server")
        when {
            d.str("source") == "anthropic" -> {
                val where = Wording.SignalCard.claudeCode(version = d.str("cli") ?: "").trim() +
                    (if (d.str("sdk") != null && has("input")) Wording.SignalCard.sdkTypes(version = d.str("sdk")!!) else "")
                line {
                    faint(i, "$where · ")
                    link(i, TOOL_DOCS_PAGE + (d.str("anchor")?.let { "#$it" } ?: ""), Wording.SignalCard.docs)
                }
            }
            d.str("source") == "mcp" -> line { faint(i, Wording.SignalCard.fromMcp(server = server ?: "")) }
            server != null -> line { faint(i, Wording.SignalCard.desktopMcp(server = server)) }
            else -> line { faint(i, Wording.SignalCard.noSchema) }
        }
        return out
    }
    if (d.str("context") != null) { // (a kubeconfig context: what kubectl would use, never its credentials)
        line {
            append((if (d.optBoolean("cluster_only")) Wording.Lookup.kubeconfigCluster else Wording.Lookup.kubeconfigContext) + " ")
            withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { append(d.str("context")!!) }
            if (d.optBoolean("current")) append(Wording.SignalCard.currentOne)
        }
        for ((key, label) in listOf("cluster" to Wording.SignalCard.cluster, "server" to Wording.SignalCard.server,
            "namespace" to Wording.SignalCard.namespace, "user" to Wording.Report.user)) {
            d.str(key)?.let { v -> line { append("$label: "); tt(i, v) } }
        }
        val path = pretty(d.str("kubeconfig") ?: "", hostId)
        line { faint(i, d.str("line")?.let { Wording.SignalCard.inKubeconfigLine(path = path, line = it) } ?: Wording.SignalCard.inKubeconfig(path = path)) }
        return out
    }
    if (r.kind == "k8s" && d.str("kind") != null) {
        line {
            withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { append(d.str("kind")!!) }
            append(" ")
            tt(i, d.str("name") ?: "")
            d.str("namespace")?.let { ns -> marked(i, { Wording.SignalCard.inNamespace(namespace = it) }, ns) }
        }
        if (has("chart")) line {
            marked(i, { Wording.SignalCard.helmChart(chart = it) }, d.str("chart") ?: ".")
            d.str("release")?.let { marked(i, { m -> Wording.SignalCard.helmRelease(release = m) }, it) }
        }
        d.str("overlay")?.let { line { marked(i, { m -> Wording.SignalCard.kustomize(overlay = m) }, it) } }
        d.str("how")?.let { line { faint(i, it) } }
        return out
    }
    if (r.kind == "subagent" || r.kind == "skill") {
        d.str("description")?.let { line { append(it) } }
        for ((key, label) in listOf("model" to Wording.Common.model, "tools" to Wording.Lookup.tools)) {
            d.str(key)?.let { v -> line { append("$label: "); tt(i, v) } }
        }
        if (d.optBoolean("built_in")) line { faint(i, Wording.SignalCard.builtIn) }
        return out
    }
    val bold = SpanStyle(fontWeight = FontWeight.Bold)
    when (r.kind) {
        "commit" -> {
            line { withStyle(bold) { append(d.optString("subject")) } }
            d.str("range")?.let { range ->
                line {
                    val say = Wording.SignalCard.commitsRange(n = d.optInt("count", 0), from = "\u0000", to = "\u0001")
                    val (before, rest) = say.split("\u0000").let { it[0] to it[1] }
                    append(before); tt(i, range.take(9)); append(rest.substringBefore("\u0001")); tt(i, d.optString("sha").take(9))
                    append(rest.substringAfter("\u0001"))
                }
            }
            d.str("repo")?.let { repo ->
                line { marked(i, { Wording.SignalCard.inRepo(repo = it) }, pretty(repo, hostId)) }
            }
            line { append(d.optString("author") + " · " + refDate(d.str("date"))) }
            line { append(Wording.SignalCard.changed(added = d.optInt("added", 0), removed = d.optInt("removed", 0), n = d.optInt("files", 0))) }
            val url = d.str("url")
            line { if (url != null) link(i, url, Wording.SignalCard.openGithub) else append(Wording.SignalCard.localOnly) }
            if (d.optBoolean("rewritten")) line {
                append(Wording.SignalCard.rewritten)
                d.str("successor")?.let { marked(i, { m -> Wording.SignalCard.replacedBy(sha = m) }, it.take(9)) }
            } else {
                val on = strings(d.optJSONArray("branches")) + strings(d.optJSONArray("remotes"))
                if (on.isNotEmpty()) line {
                    append(if (on.size > 8) Wording.Git.onBranchesMore(branches = on.take(8).joinToString(", "), n = on.size - 8)
                        else Wording.Git.onBranches(branches = on.take(8).joinToString(", ")))
                }
            }
        }
        "branch" -> {
            line { withStyle(bold) { append(d.optString("subject")) } }
            line { marked(i, { Wording.SignalCard.lastCommit(date = refDate(d.str("date")), sha = it) }, d.optString("sha").take(9)) }
            if (d.has("ahead")) line { append(Wording.SignalCard.aheadBehind(ahead = d.optInt("ahead"), behind = d.optInt("behind"), base = d.optString("base"))) }
            d.str("upstream")?.let { line { append(Wording.SignalCard.tracks(upstream = it)) } }
            val url = d.str("url")
            line { if (url != null) link(i, url, Wording.SignalCard.openGithub) else append(Wording.SignalCard.notPushed) }
        }
        "pr" -> {
            line { withStyle(bold) { append(d.str("title") ?: "#" + d.opt("number")) } }
            d.str("state")?.let { state ->
                val dates = listOfNotNull(Wording.SignalCard.opened(date = refDate(d.str("created"))),
                    d.str("merged")?.let { Wording.SignalCard.merged(date = refDate(it)) }
                        ?: d.str("closed")?.let { Wording.SignalCard.closed(date = refDate(it)) })
                line { append(state.replaceFirstChar { it.uppercase() } + ": " + dates.joinToString(", ")) }
            }
            if (has("additions")) line {
                append(Wording.SignalCard.changed(added = d.optInt("additions"), removed = d.optInt("deletions"), n = d.optInt("changed_files", 0)) +
                    (d.str("head")?.let { " · $it → ${d.optString("base")}" } ?: ""))
            }
            d.str("url")?.let { url -> line { link(i, url, Wording.SignalCard.openGithub) } }
        }
    }
    if (r.kind == "commit" || r.kind == "branch") {
        val prs = d.optJSONArray("prs")
        if (prs != null && prs.length() > 0) {
            line { withStyle(bold) { append(Wording.Lookup.pullRequests) } }
            for (k in 0 until prs.length()) prs.optJSONObject(k)?.let { p ->
                val whenText = p.str("merged")?.let { Wording.SignalCard.merged(date = refDate(it)) }
                    ?: (p.optString("state") + ", " + Wording.SignalCard.opened(date = refDate(p.str("created"))))
                line {
                    p.str("url")?.let { link(i, it, "#" + p.opt("number")) } ?: append("#" + p.opt("number"))
                    append(" " + p.optString("title") + " ($whenText)")
                }
            }
        } else if (d.optBoolean("pushed") && d.has("prs")) line { append(Wording.SignalCard.noPrs) }
    }
    return out
}

private fun strings(a: org.json.JSONArray?): List<String> = if (a == null) emptyList() else (0 until a.length()).map { a.optString(it) }
