package dev.baude.android

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.isImeVisible
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch

/** One of the bottom bar's pages (Baude's side panel's, the chat's own first). */
data class Page(val key: String, val title: String, val icon: ImageVector)

/** The pages a chat has, in Baude's side panel's order: a Bot's memory, browser and routines only while it's a Bot;
 * each only where its host takes what it asks of it (takes). */
fun pages(bot: Boolean, hostId: String): List<Page> {
    fun has(op: String) = takes(hostId, op)
    return listOfNotNull(
        Page("chat", Wording.Chats.chat, Adwaita.Claude),
        Page("meta", "Meta", Adwaita.Meta),
        if (bot && has("memory")) Page("memory", Wording.BotMemory.title, Adwaita.DocumentEdit) else null,
        if (bot && has("emission_watch")) Page("browser", Wording.BotBrowser.title, Adwaita.WebBrowser) else null,
        if (bot && has("routines")) Page("routines", Wording.Routines.title, Adwaita.Alarm) else null,
        Page("agents", Wording.SidePanel.subagents, Adwaita.SystemUsers),
        Page("processes", Wording.SidePanel.processes, Adwaita.UtilitiesTerminal),
        if (has("ephemeral")) Page("btw", Wording.SidePanel.sideQuestions, Adwaita.DialogQuestion) else null,
        Page("artifacts", Wording.SidePanel.artifacts, Adwaita.WebBrowser),
        if (has("list_dir")) Page("files", Wording.Lookup.files, Adwaita.Folder) else null,
        if (has("git")) Page("git", Wording.Git.title, Adwaita.MediaPlaylistShuffle) else null,
        if (has("signal_names")) Page("signal", Wording.SidePanel.signal, Adwaita.InsertLink) else null,
    )
}

/** A chat open: its bar (its Bot's picture and name, or its title; where it is), its pages to swipe between (the
 * chat's own with Baude's header controls over it; first: the one shown first), and the bottom bar that's Baude's
 * side panel's switcher (a row that scrolls). */
@OptIn(ExperimentalFoundationApi::class, ExperimentalLayoutApi::class)
@Composable
fun ChatShell(hostId: String, sid: String, back: () -> Unit, open: (String, String) -> Unit, feedback: () -> Unit,
              first: String = "chat", screen: (String) -> Unit = {}) {
    val c = LocalBaude.current
    val chat = remember(hostId, sid) { Mesh.openChat(hostId, sid) }
    Meta.opener = open
    val at = chat.hostId // (where it's reached now: a member's chat moves to a host of yours that's its machine)
    Mesh.tick.value
    val s = Mesh.session(at, sid)
    val bot = s?.bot
    val contact = s?.let { Contact(at, it) }
    val name = if (bot != null) contact!!.name else s?.title?.ifBlank { null } ?: Wording.Common.untitled
    val all = pages(bot != null, at)
    var shown by rememberSaveable(hostId, sid) { mutableStateOf(first) }
    val pager = rememberPagerState(initialPage = all.indexOfFirst { it.key == shown }.coerceAtLeast(0)) { all.size }
    val scope = rememberCoroutineScope()
    Meta.pages = { all }
    FileLinks.showFiles = { all.indexOfFirst { it.key == "files" }.takeIf { it >= 0 }?.let { scope.launch { pager.animateScrollToPage(it) } } }
    val place = ChatPlace(at, s?.cwd, Mesh.conn(at)?.info?.home, chat.artifacts.toList())
    Meta.pageOpener = { t -> all.indexOfFirst { it.title.equals(t, true) }.takeIf { it >= 0 }?.let { scope.launch { pager.animateScrollToPage(it) } } }
    androidx.compose.runtime.DisposableEffect(chat) { // (its side questions' answers heard whichever page is shown)
        val l = sideQuestions(chat)
        Mesh.listeners.add(l)
        onDispose {
            Mesh.listeners.remove(l)
            Meta.pages = { emptyList() } // (no chat open: no pages for Meta to show)
            Meta.pageOpener = {}
            FileLinks.showFiles = {}
        }
    }
    LaunchedEffect(pager.currentPage, all.size) { all.getOrNull(pager.currentPage)?.let { shown = it.key } }
    LaunchedEffect(all.size) { // (a Bot no more: its pages gone, Meta shown if one of them was, as Baude does)
        val i = all.indexOfFirst { it.key == shown }
        if (i < 0) pager.scrollToPage(1) else if (i != pager.currentPage) pager.scrollToPage(i)
    }
    Column(Modifier.fillMaxSize().background(c.frame).imePadding()) {
        Bar(name, subtitle = Mesh.label(at), start = { IconButton(Adwaita.GoPrevious, Wording.Browser.back, onClick = back) },
            lead = if (bot != null) ({ Avatar(name, contact?.let { Mesh.pictures[it.pictureKey] }) }) else null,
            end = {
                if (bot != null) PhoneBrowserButton(at, sid, name) // (Phone Use: a Bot's browser on this phone)
                ChatMenu(at, sid, feedback, screen)
            })
        val conn = Mesh.conn(at)
        if (conn == null || conn.state != "up") GreyBanner(if (conn?.state == "connecting") Wording.Hosts.looking(host = Mesh.label(at))
            else conn?.why?.let { Wording.Hosts.downBecause(host = Mesh.label(at), why = it) } ?: Wording.Hosts.down(host = Mesh.label(at)))
        androidx.compose.runtime.CompositionLocalProvider(LocalChatPlace provides place) {
        HorizontalPager(pager, Modifier.weight(1f).fillMaxWidth(), key = { all[it].key },
            userScrollEnabled = all.getOrNull(pager.currentPage)?.key != "browser") { i ->
            val here = pager.currentPage == i && !pager.isScrollInProgress
            when (all[i].key) {
                "chat" -> Column(Modifier.fillMaxSize()) { // (its model, effort and mode: the chat's own, as Baude's
                    if (takes(at, "control"))              // header has them, not its side panel's pages)
                        ChatControls(at, sid, said = { chat.said.value = it }, open = open)
                    Box(Modifier.weight(1f)) { ChatPage(chat, name, hint = if (bot != null) "Message $name" else "") }
                }
                "meta" -> MetaPage(at, here)
                "memory" -> MemoryPage(at, sid, here)
                "browser" -> BrowserPage(at, sid, bot?.name.orEmpty(), here)
                "routines" -> RoutinesPage(at, sid, here)
                "agents" -> SubagentsPage(chat)
                "processes" -> ProcessesPage(at, sid)
                "artifacts" -> ArtifactsPage(chat)
                "btw" -> SideQuestionsPage(chat)
                "files" -> FilesPage(at, sid)
                "git" -> GitPage(at, sid)
                "signal" -> SignalPage(at, sid)
            }
        }
        }
        if (!WindowInsets.isImeVisible) BottomBar(all, pager.currentPage) { scope.launch { pager.animateScrollToPage(it) } }
    }
}

/** Baude's side panel's switcher as a bottom bar (Adw.ViewSwitcher, narrow: each page's icon over its title, the one
 * shown raised), a row that scrolls when it's wider than the phone. */
@Composable
fun BottomBar(pages: List<Page>, current: Int, pick: (Int) -> Unit) {
    val c = LocalBaude.current
    Box(Modifier.fillMaxWidth().background(c.frame).navigationBarsPadding(), contentAlignment = Alignment.Center) {
        Row(Modifier.horizontalScroll(rememberScrollState()).padding(horizontal = 6.dp, vertical = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(2.dp)) {
            pages.forEachIndexed { i, p ->
                val on = i == current
                Column(Modifier.widthIn(min = 64.dp).clip(RoundedCornerShape(8.dp))
                    .background(if (on) c.fg.copy(alpha = 0.10f) else c.frame).clickable { pick(i) }
                    .padding(horizontal = 8.dp, vertical = 5.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                    Icon(p.icon, null, tint = c.fg, modifier = Modifier.size(18.dp.icon))
                    Text(p.title, style = Type.caption.copy(fontWeight = if (on) androidx.compose.ui.text.font.FontWeight.Bold
                        else androidx.compose.ui.text.font.FontWeight.Normal), color = c.fg, maxLines = 1,
                        overflow = TextOverflow.Ellipsis)
                }
            }
        }
    }
}

/** An empty page's words (a side panel list's placeholder: dim, 24 above and below). */
@Composable
fun Empty(text: String) {
    val c = LocalBaude.current
    Text(text, style = Type.body, color = c.dim, textAlign = androidx.compose.ui.text.style.TextAlign.Center,
        modifier = Modifier.fillMaxWidth().padding(vertical = 24.dp))
}

/** A status page (Adw.StatusPage): its icon, its title, what it says under that. */
@Composable
fun StatusPage(icon: ImageVector, title: String, description: String? = null) {
    val c = LocalBaude.current
    Column(Modifier.fillMaxSize().padding(32.dp), verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally) {
        Icon(icon, null, tint = c.dim, modifier = Modifier.size(64.dp.icon))
        Text(title, style = Type.title.copy(fontSize = Type.title.fontSize * 1.4f), color = c.fg,
            textAlign = androidx.compose.ui.text.style.TextAlign.Center, modifier = Modifier.padding(top = 18.dp))
        if (description != null) Text(description, style = Type.body, color = c.dim,
            textAlign = androidx.compose.ui.text.style.TextAlign.Center, modifier = Modifier.padding(top = 8.dp))
    }
}
