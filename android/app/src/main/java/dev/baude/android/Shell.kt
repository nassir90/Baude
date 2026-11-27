package dev.baude.android

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.isImeVisible
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.exclude
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.union
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
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
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.positionInWindow
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch

/** One of the bottom bar's pages (Baude's side panel's, the chat's own first). */
data class Page(val key: String, val title: String, val icon: ImageVector)

/** The pages a chat has, in Baude's side panel's order: a Bot's memory, browser and tasks only while it's a Bot;
 * each only where its host takes what it asks of it (takes). */
fun pages(bot: Boolean, hostId: String): List<Page> {
    fun has(op: String) = takes(hostId, op)
    return listOfNotNull(
        Page("chat", Wording.Chats.chat, Adwaita.Claude),
        Page("meta", "Meta", Adwaita.Meta),
        if (bot && has("memory")) Page("memory", Wording.BotMemory.title, Adwaita.DocumentEdit) else null,
        if (bot && has("emission_watch")) Page("browser", Wording.BotBrowser.title, Adwaita.WebBrowser) else null,
        if (bot && has("tasks")) Page("tasks", Wording.Tasks.title, Adwaita.Alarm) else null,
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
 * side panel's switcher (a row that scrolls). On its side, the phone has the shown page's pickers in the bar, as
 * Baude's header bar has them, and the bar's side panel toggle, which puts the chat and its other pages side by side
 * as Baude's side panel is: a rule between them to drag, the switcher under the pages on the right. */
@OptIn(ExperimentalFoundationApi::class, ExperimentalLayoutApi::class)
@Composable
fun ChatShell(hostId: String, sid: String, back: () -> Unit, open: (String, String) -> Unit, feedback: () -> Unit,
              first: String = "chat", screen: (String) -> Unit = {}, tool: (String, String) -> Unit = { _, _ -> }) {
    val c = LocalBaude.current
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val chat = remember(hostId, sid) { Mesh.openChat(hostId, sid) }
    Meta.opener = open
    val at = chat.hostId // (where it's reached now: a member's chat moves to a host of yours that's its machine)
    Mesh.tick.value
    val s = Mesh.session(at, sid)
    val bot = s?.bot
    val contact = s?.let { Contact(at, it) }
    val name = if (bot != null) contact!!.name else s?.title?.ifBlank { null }?.let { if (it.length > 48) it.take(47) + "…" else it }
        ?: Wording.Sidebar.newSession
    val all = pages(bot != null, at)
    val sideAll = all.filter { it.key != "chat" }
    val wide = androidx.compose.ui.platform.LocalConfiguration.current.orientation ==
        android.content.res.Configuration.ORIENTATION_LANDSCAPE // (on its side: the pickers in the bar)
    val split = wide && Settings.split.value // (upright, one page at a time: too narrow for two)
    val beside by androidx.compose.runtime.rememberUpdatedState(split)
    var shown by rememberSaveable(hostId, sid) { mutableStateOf(first) }
    var side by rememberSaveable(hostId, sid) { mutableStateOf(if (first == "chat") "meta" else first) } // (the page
    val pager = rememberPagerState(initialPage = all.indexOfFirst { it.key == shown }.coerceAtLeast(0)) { all.size } // beside it)
    val scope = rememberCoroutineScope()
    val show = { key: String -> // (a page shown: beside the chat while they're side by side, else swiped to)
        if (split && key != "chat" && sideAll.any { it.key == key }) side = key
        else all.indexOfFirst { it.key == key }.takeIf { it >= 0 }?.let { scope.launch { pager.animateScrollToPage(it) } }
        Unit
    }
    val toggle = { // (side by side, the page shown beside the chat; else the chat, swiped to, as Baude's closed panel)
        val on = !Settings.split.value
        if (on && shown != "chat") side = shown
        Settings.split.value = on
        Settings.save(ctx)
        if (!on) all.indexOfFirst { it.key == "chat" }.takeIf { it >= 0 }?.let { scope.launch { pager.scrollToPage(it) } }
        Unit
    }
    Meta.pages = { all }
    FileLinks.showFiles = { show("files") }
    val place = ChatPlace(at, s?.cwd, Mesh.conn(at)?.info?.home, chat.artifacts.toList(), sid)
    Meta.pageOpener = { t -> all.firstOrNull { it.title.equals(t, true) }?.let { show(it.key) } }
    androidx.compose.runtime.DisposableEffect(chat) { // (its side questions' answers heard whichever page is shown)
        val l = sideQuestions(chat)
        GitPages.showPage = { show("git") }
        SignalLinks.hostId = at
        SignalLinks.sid = sid
        val unsignal = SignalNames.listen(chat)
        val status = chrome(chat)
        Mesh.listeners.add(l)
        Mesh.listeners.add(status)
        Stopping.listen()
        onDispose {
            Mesh.listeners.remove(l)
            Mesh.listeners.remove(status)
            unsignal()
            SignalLinks.open.value = null
            Meta.pages = { emptyList() } // (no chat open: no pages for Meta to show)
            Meta.pageOpener = {}
            FileLinks.showFiles = {}
            GitPages.showPage = {}
        }
    }
    SignalLinks.openPage = show
    LaunchedEffect(chat.showAgent.value) { // (View on a call's card: the Subagents page)
        if (chat.showAgent.value == null) return@LaunchedEffect
        if (beside) side = "agents"
        else all.indexOfFirst { it.key == "agents" }.takeIf { it >= 0 }?.let { pager.animateScrollToPage(it) }
        kotlinx.coroutines.delay(100) // (the page has read it by now)
        chat.showAgent.value = null
    }
    LaunchedEffect(all.size, TaskLinks.pending) { // (opened from a pending restart's See Thread on a task's run, or a
        // chat's line from one: its Tasks page)
        if (TaskLinks.pending?.let { it[0] == hostId && it[1] == sid } == true) show("tasks")
    }
    LaunchedEffect(pager.currentPage, all.size) { all.getOrNull(pager.currentPage)?.let { shown = it.key } }
    LaunchedEffect(shown, split) { // (its chat page on screen, or not: what's on it seen, Mesh.tellSeen)
        chat.inView = split || shown == "chat"
        Mesh.tellSeen(chat)
    }
    LaunchedEffect(all.size) { // (a Bot no more: its pages gone, Meta shown if one of them was, as Baude does)
        if (sideAll.none { it.key == side }) side = "meta"
        val i = all.indexOfFirst { it.key == shown }
        if (i < 0) pager.scrollToPage(1) else if (i != pager.currentPage) pager.scrollToPage(i)
    }
    val modelSet = { v: String ->
        chat.chrome.outgrown.value = false // (another model's window: as its usage says, its host asking once it's set)
    }
    val chatControls = @Composable { inBar: Boolean -> // (its model, effort and mode: the chat's own, as Baude's
        if (takes(at, "control"))                     // header has them, not its side panel's pages)
            ChatControls(at, sid, said = { chat.said.value = it }, open = open, modelSet = modelSet, inBar = inBar)
    }
    @Composable
    fun Page(key: String, here: Boolean, beside: Boolean) {
        when (key) {
            "chat" -> Column(Modifier.fillMaxSize().watchTouches()) {
                if (!wide) chatControls(false)
                ChatBanner(chat, open)
                Box(Modifier.weight(1f)) {
                    ChatPage(chat, name, stop = { interrupt(chat) }, foot = { StatusBar(chat) { show(it) } }, openPage = { show(it) }, open = open)
                    SignalPopovers(chat)
                }
            }
            "meta" -> Column(Modifier.fillMaxSize()) { // (Meta's pickers where the chat's are; beside the chat, over it)
                if (!wide || beside) MetaControls()
                Box(Modifier.weight(1f)) { MetaPage(at, here) }
            }
            "memory" -> MemoryPage(at, sid, here)
            "browser" -> BrowserPage(at, sid, bot?.name.orEmpty(), here)
            "tasks" -> TasksPage(at, sid, here)
            "agents" -> SubagentsPage(chat)
            "processes" -> ProcessesPage(chat, here)
            "artifacts" -> ArtifactsPage(chat)
            "btw" -> SideQuestionsPage(chat)
            "files" -> FilesPage(chat)
            "git" -> GitPage(at, sid, here, show)
            "signal" -> SignalPage(at, sid, here)
        }
    }
    // (the keyboard rises over the bottom bar, and the pages' foot, the box you write in, with it once it's past the
    // bar; going down, the box comes down with it till it rests on the bar, which the keyboard uncovers as it goes)
    var barHeight by remember { mutableIntStateOf(0) } // (px: the bottom bar, the phone's own bar under it too)
    val overBar = WindowInsets.ime.exclude(WindowInsets(bottom = barHeight))
    val pane = remember { MenuPane() } // (the model pickers' lists, over the page, down to the bottom bar's top)
    val keyboard = WindowInsets.isImeVisible
    val bar = Modifier.onSizeChanged { barHeight = it.height }
        .onGloballyPositioned { pane.floor = if (keyboard) null else it.positionInWindow().y }
    androidx.compose.runtime.CompositionLocalProvider(LocalMenuPane provides pane) {
    Box(Modifier.fillMaxSize()) {
    Column(Modifier.fillMaxSize().background(c.frame)) {
        Bar(name, start = { IconButton(Adwaita.GoPrevious, Wording.Browser.back, onClick = back) },
            lead = if (bot != null) ({ Avatar(name, contact?.let { Mesh.pictures[it.pictureKey] }) }) else null,
            end = {
                if (wide) when (if (split) "chat" else shown) {
                    "chat" -> chatControls(true)
                    "meta" -> MetaControls(inBar = true)
                }
                if (bot != null) PhoneBrowserButton(at, sid, name) // (Phone Use: a Bot's browser on this phone)
                if (wide) IconButton(Adwaita.SidebarShowRight, Wording.SidePanel.toggleTip,
                    background = if (split) c.fg.copy(alpha = 0.10f) else null, onClick = toggle)
                ChatMenu(at, sid, feedback, screen, writes = chat.chrome.writes.value,
                    tool = { s, h -> if (h == at) screen(s) else tool(s, h) })
            })
        androidx.compose.runtime.CompositionLocalProvider(LocalChatPlace provides place) {
        if (split) SideBySide(Modifier.weight(1f).fillMaxWidth(), close = toggle, chat = { Page("chat", true, false) }) {
            Box(Modifier.weight(1f).windowInsetsPadding(overBar)) { Page(side, true, true) }
            BottomBar(sideAll, sideAll.indexOfFirst { it.key == side }, bar) { side = sideAll[it].key }
        } else {
            HorizontalPager(pager, Modifier.weight(1f).fillMaxWidth().windowInsetsPadding(overBar), key = { all[it].key },
                userScrollEnabled = all.getOrNull(pager.currentPage)?.key != "browser") { i ->
                Page(all[i].key, pager.currentPage == i && !pager.isScrollInProgress, false)
            }
            BottomBar(all, pager.currentPage, bar) { scope.launch { pager.animateScrollToPage(it) } }
        }
        }
    }
    MenuPaneLayer(pane)
    ViewerOverlay()
    }
    }
}

/** The chat and its pages side by side (Baude's main split): the chat on the left, a 1px rule, the pages on the
 * right at the width the rule was dragged to (Settings' panelWidth), else about a third of the phone. A drag of the
 * rule stops at the pages' least and the chat's; dragged on till the pages would be under 24 wide, they close once
 * it's let go there, and open again at the width they had. */
@Composable
fun SideBySide(modifier: Modifier, close: () -> Unit, chat: @Composable () -> Unit, pages: @Composable ColumnScope.() -> Unit) {
    val c = LocalBaude.current
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val density = androidx.compose.ui.platform.LocalDensity.current
    androidx.compose.foundation.layout.BoxWithConstraints(modifier) {
        val total = maxWidth.value
        val most = (total - CHAT_LEAST - 1f).coerceAtLeast(PANEL_LEAST)
        val kept = Settings.panelWidth.value ?: (total * 0.36f)
        var dragged by remember { mutableStateOf<Float?>(null) } // (where a drag has put it: the width it'd leave)
        val wanted = dragged ?: kept
        val shut = dragged != null && wanted <= CLOSE_WITHIN // (dragged on to close: let go there, it's closed)
        val width = if (shut) 0f else wanted.coerceIn(PANEL_LEAST, most)
        val from by androidx.compose.runtime.rememberUpdatedState(kept.coerceIn(PANEL_LEAST, most)) // (for the drag: as
        val widest by androidx.compose.runtime.rememberUpdatedState(most)                            // they are then)
        Row(Modifier.fillMaxSize()) {
            Box(Modifier.weight(1f).fillMaxHeight() // (clear of the phone's own bar at the foot, as the switcher is, and
                .windowInsetsPadding(WindowInsets.navigationBars.union(WindowInsets.ime) // of the keyboard)
                    .only(WindowInsetsSides.Bottom))) { chat() }
            Box(Modifier.width(1.dp).fillMaxHeight().background(c.rule))
            if (width > 0f) Column(Modifier.width(width.dp).fillMaxHeight()) { pages() }
        }
        Box(Modifier.offset(x = (total - width - 1f - GRIP / 2).dp).width(GRIP.dp).fillMaxHeight() // (the rule picked
            .pointerInput(Unit) {                                                                 // first: 8 either side)
                detectHorizontalDragGestures(
                    onDragStart = { dragged = from },
                    onDragEnd = {
                        val d = dragged
                        dragged = null
                        if (d != null && d <= CLOSE_WITHIN) close()
                        else if (d != null) {
                            Settings.panelWidth.value = d.coerceIn(PANEL_LEAST, widest)
                            Settings.save(ctx)
                        }
                    },
                    onDragCancel = { dragged = null },
                ) { change, dx ->
                    change.consume()
                    dragged = (dragged ?: from) - with(density) { dx.toDp().value } // (as far as the finger's gone: drawn
                }                                                                   // between the least and most)
            })
    }
}

private const val PANEL_LEAST = 200f  // (dp: the pages beside the chat at their narrowest)
private const val CHAT_LEAST = 160f   // (the chat at its narrowest beside them)
private const val CLOSE_WITHIN = 24f  // (panels.PaneSlide.CLOSE_WITHIN)
private const val GRIP = 16f          // (the rule's width to a finger)

/** Baude's side panel's switcher as a bottom bar (Adw.ViewSwitcher, narrow: each page's icon over its title, the one
 * shown raised), a row that scrolls when it's wider than the phone. */
@Composable
fun BottomBar(pages: List<Page>, current: Int, modifier: Modifier = Modifier, pick: (Int) -> Unit) {
    val c = LocalBaude.current
    val scroll = rememberScrollState()
    LaunchedEffect(current, scroll.maxValue) { // (the page shown in view, where they don't all fit)
        if (pages.size > 1) scroll.animateScrollTo(scroll.maxValue * current / (pages.size - 1))
    }
    Box(modifier.fillMaxWidth().background(c.frame).navigationBarsPadding(), contentAlignment = Alignment.Center) {
        Row(Modifier.horizontalScroll(scroll).padding(horizontal = 6.dp, vertical = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(2.dp)) {
            pages.forEachIndexed { i, p ->
                val on = i == current
                Column(Modifier.widthIn(min = 64.dp).clip(RoundedCornerShape(8.dp))
                    .background(if (on) c.fg.copy(alpha = 0.10f) else c.frame).clickable { pick(i) }
                    .padding(horizontal = 8.dp, vertical = 5.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                    Icon(p.icon, null, tint = c.fg, modifier = Modifier.size(16.dp.icon))
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
