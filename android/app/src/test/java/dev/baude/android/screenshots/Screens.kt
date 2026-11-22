package dev.baude.android.screenshots

import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.ui.unit.dp
import com.github.takahirom.roborazzi.captureRoboImage
import com.github.takahirom.roborazzi.captureScreenRoboImage
import dev.baude.android.Artifact
import dev.baude.android.BaudeTheme
import dev.baude.android.BotSettings
import dev.baude.android.Chat
import dev.baude.android.ChatShell
import dev.baude.android.Line
import dev.baude.android.SideQuestion
import dev.baude.android.Subagent
import dev.baude.android.Home
import dev.baude.android.HomePrefs
import dev.baude.android.Host
import dev.baude.android.Item
import dev.baude.android.Key
import dev.baude.android.Mesh
import dev.baude.android.MeshScreen
import dev.baude.android.Msg
import dev.baude.android.NewSession
import dev.baude.android.PhoneHost
import dev.baude.android.PhoneScreen
import dev.baude.android.Settings
import dev.baude.android.SettingsScreen
import dev.baude.android.Update
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import org.robolectric.shadows.ShadowChoreographer
import java.time.Duration
import java.security.KeyPairGenerator
import java.security.interfaces.ECPublicKey

/** The app's screens drawn on the JVM (Robolectric, its native graphics) with fixture data, saved as PNGs by
 * Roborazzi: a render rig, not tests (nothing's checked). ./gradlew --offline screenshots (in android/) writes
 * app/build/screenshots/<theme>/<name>.png, light and dark (LightScreens, DarkScreens). The app's own state is set
 * as a phone would have it (Mesh's hosts and sessions, Update's, Settings'), never fetched: nothing's reached over
 * the network. */
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35])
abstract class Screens(private val theme: String) {
    private lateinit var activity: ComponentActivity

    @Before fun up() {
        freshCompose()
        ShadowChoreographer.setPaused(true)
        ShadowChoreographer.setFrameDelay(Duration.ofMillis(16))
        activity = Robolectric.buildActivity(ComponentActivity::class.java).setup().get()
        Mesh.app = activity.applicationContext
        Settings.load(activity)
        Settings.accent.value = null // (each screen from the settings a phone starts with)
        Settings.appearance.value = "system"
        Settings.textScale.value = 1f
        Settings.globalScale.value = 1f
        Settings.notify.value = true
        Settings.meteredUpdates.value = false
        Update.state.value = ""
        Settings.split.value = false
        Settings.panelWidth.value = null
        fixtures()
    }

    @After fun down() {
        PhoneHost.id = ""
        FixtureConnection.down()
        Mesh.hosts.clear()
        Mesh.sessions.clear()
        Mesh.infos.clear()
        Mesh.states.clear()
        Mesh.key.value = null
        Mesh.pictures.clear()
        Pictures.authors(emptyMap())
    }

    @Test fun home() = render("01-home") { HomeScreen() }

    @Test fun homeUpdate() {
        Update.state.value = "ready"
        render("02-home-update") { HomeScreen() }
    }

    @Test fun homeDownloading() {
        Update.state.value = "downloading"
        Update.progress.value = 0.4f
        render("03-home-downloading") { HomeScreen() }
    }

    @Test fun homeWrongKey() {
        Update.state.value = "wrong"
        Update.why.value = "Can't update: the new version is signed with another key"
        render("04-home-wrong-key") { HomeScreen() }
    }

    @Test fun settings() = render("05-settings") { SettingsScreen({}, {}) }

    @Test fun mesh() {
        versionsFixture()
        render("06-mesh") { MeshScreen {} }
    }

    @Test fun meshRename() {
        versionsFixture()
        show { MeshScreen {} }
        tap("Menu")
        tap("Rename…")
        screen("06-mesh-rename")
    }

    @Test fun meshRemove() {
        versionsFixture()
        show { MeshScreen {} }
        tap("Menu")
        tap("Remove…")
        screen("06-mesh-remove")
    }

    @Test fun newSessionHosts() {
        versionsFixture()
        show { NewSession(null, null, done = {}, opened = { _, _ -> }) }
        tap("workshop")
        menu("19-new-session-hosts")
    }

    @Test fun phone() {
        phoneFixture()
        render("40-phone") { PhoneScreen({}, open = {}) }
    }

    @Test fun homePhone() {
        phoneFixture()
        HomePrefs.pick("phone")
        render("41-home-phone") { HomeScreen() }
    }

    @Test fun makeBot() { // (a chat on workshop, its row's Make Bot…: the Bot's name, its chat's title to keep or type over)
        show { BotSettings(Item("h1", Mesh.sessions.getValue("h1")[1], 0)) {} }
        screen("42-make-bot")
    }

    // (people's and Bots' pictures, each set into the page under Avatar's shade)
    @Test fun picturesHome() {
        phoneFixture()
        botPictures()
        HomePrefs.load(activity) // (its saved host read first, so it doesn't take back the one picked)
        HomePrefs.pick("phone")
        render("43-pictures-home") { HomeScreen() }
    }

    @Test fun picturesPhone() {
        phoneFixture()
        botPictures()
        render("44-pictures-phone") { PhoneScreen({}, open = {}) }
    }

    @Test fun picturesChat() {
        val bot = workshopBot()
        fill(Mesh.openChat("h1", bot.id.orEmpty()))
        render("45-pictures-chat") { ChatShell("h1", bot.id.orEmpty(), back = {}, open = { _, _ -> }, feedback = {}, first = "chat") }
    }

    @Test fun picturesBotSettings() {
        val bot = workshopBot()
        show { BotSettings(Item("h1", bot, 0)) {} }
        screen("46-pictures-bot-settings")
    }

    /** The Git page on the parity rig's repository (its recorded answers), its authors' pictures as GitHub gives them:
     * ada's identicon, pale grey to its edge; rowan-lee's portrait. */
    @Test fun picturesGit() {
        val fixtures = java.io.File("../../tests/parity/fixtures")
        val home = org.json.JSONObject(java.io.File(fixtures, "sessions.json").readText()).getJSONObject("host").getString("home")
        val answers = org.json.JSONObject(java.io.File(fixtures, "git-answers.json").readText()).getJSONObject("answers")
        FixtureConnection.up(activity, Mesh.hosts.first(), home) { msg ->
            val reply = org.json.JSONObject().put("op", "git").put("req", msg.opt("req")).put("what", msg.optString("what"))
            if (msg.optString("op") != "git") null else when (msg.optString("what")) {
                "page" -> org.json.JSONObject(answers.getJSONObject("page").toString().replace("@HOME@", home))
                    .put("op", "git").put("req", msg.opt("req"))
                "pulls" -> reply.put("repo", org.json.JSONObject.NULL).put("prs", org.json.JSONObject.NULL).put("gh", false)
                "log" -> reply.put("commits", org.json.JSONArray()).put("skip", 0).put("more", false)
                else -> null
            }
        }
        val now = System.currentTimeMillis() / 1000.0
        Mesh.sessions["h1"] = listOf(Msg.Session(id = "a", title = "Tidy the release notes", cwd = "$home/garden",
            updated = now - 60, said = now - 60, created = now - 660, state = "idle", model = "opus", effort = "high", mode = "default"))
        Pictures.authors(mapOf("ada@example.com" to Pictures.identicon(7, 0xFF8DB8E8.toInt()),
            "41000001+rowan-lee@users.noreply.github.com" to Pictures.portrait(0xFF2E3440.toInt(), 0xFFD9A47E.toInt(),
                0xFF6F8396.toInt(), 0xFF3B2A1E.toInt())))
        dev.baude.android.GitPages.prepare("h1", "a", null, false)
        show { ChatShell("h1", "a", back = {}, open = { _, _ -> }, feedback = {}, first = "git") }
        val st = dev.baude.android.GitPages.of("h1", "a")
        repeat(20) { if (st.commits.isEmpty()) settle() }
        repeat(3) {
            androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
            settle()
        }
        activity.window.decorView.captureRoboImage("build/screenshots/$theme/47-pictures-git.png")
    }

    /** The phone's two Bots' pictures: Juniper's a portrait on a dark ground, Fern's one on white. */
    private fun botPictures() {
        Mesh.pictures["phone/p-a/null"] = Pictures.portrait(0xFF1D1D20.toInt(), 0xFFE0B48A.toInt(), 0xFF3A3A40.toInt(), 0xFF141414.toInt())
        Mesh.pictures["phone/p-b/null"] = Pictures.portrait(0xFFFFFFFF.toInt(), 0xFFC68B59.toInt(), 0xFF8DE6B1.toInt(), 0xFF5A3A22.toInt())
    }

    /** workshop's first chat a Bot, Juniper, with its picture: a portrait on white. */
    private fun workshopBot(): Msg.Session {
        val was = Mesh.sessions.getValue("h1")
        val a = was[0]
        val bot = Msg.Session(id = a.id, title = a.title, cwd = a.cwd, updated = a.updated, said = a.said, created = a.created,
            state = "idle", model = a.model, effort = a.effort, mode = a.mode, bot = Msg.BotListing(name = "Juniper"))
        Mesh.sessions["h1"] = listOf(bot) + was.drop(1)
        Mesh.pictures["h1/${a.id}/null"] = Pictures.portrait(0xFFFFFFFF.toInt(), 0xFFC68B59.toInt(), 0xFF8DE6B1.toInt(), 0xFF5A3A22.toInt())
        return bot
    }

    /** This phone as a host of its own, named pocket: its connection up, two agents (Bots) of its own, and one in its
     * trash. */
    private fun phoneFixture() {
        PhoneHost.name = "pocket"
        val root = java.io.File(activity.filesDir, "phone")
        PhoneHost::class.java.getDeclaredField("root").apply { isAccessible = true }.set(PhoneHost, root)
        java.io.File(root, "trash/t1").apply { mkdirs() }.resolve("meta.json").writeText(org.json.JSONObject()
            .put("id", "t1").put("title", "Plan the allotment").put("model", "qwen3:8b").put("trashed", 1.0)
            .put("bot", org.json.JSONObject().put("name", "Moss")).toString())
        FixturePhone.up(activity, "pocket")
        val now = System.currentTimeMillis() / 1000.0
        fun s(id: String, name: String, title: String, ago: Double, state: String = "idle") =
            Msg.Session(id = id, title = title, cwd = "/data/phone/agents/$id", updated = now - ago, said = now - ago,
                created = now - ago - 600, state = state, model = "qwen3:8b", mode = "default", bot = Msg.BotListing(name = name))
        Mesh.sessions["phone"] = listOf(s("p-a", "Juniper", "Shopping list for Saturday", 120.0, "working"),
            s("p-b", "Fern", "Read the bus timetable", 5_400.0))
    }

    /** The mesh as workshop says it is: it, and two members on older code (one 45 commits behind, one whose count
     * it can't make), with the apps on each, and this phone; one not reached now; and lantern, a host of yours that
     * isn't up. */
    private fun versionsFixture() {
        val newest = "6be704e2dc23f0730cc73e2586db5561b1f4cd77"
        val older = "795a6cfd98c7e2517c183787ae4631ae75d39ff5"
        val oldest = "98a4827dd47ec7d67f3e36bb7b2064b91a652bc3"
        val day = 86_400L
        val made = 1_795_247_995L
        fun head(client: String, commit: String?, at: Long?, behind: Long?) =
            Msg.Head(client = client, commit = commit, made = at, behind = behind)
        PhoneHost.id = "p1"
        Mesh.states["h1"] = Mesh.conn("h1")!!
        Mesh.infos["h1"] = Msg.Info(id = "h1", name = "workshop", host = "workshop.local", commit = newest, made = made,
            behind = 0, heads = listOf(head("baude", newest, made, 0)),
            mesh = Msg.MeshView(newest = newest, members = listOf(
                Msg.Member(id = "m1", name = "studio", host = "studio.local", commit = older, made = made - day, behind = 45,
                    heads = listOf(head("record", older, made - day, 45), head("emission", newest, made, 0))),
                Msg.Member(id = "m2", name = "attic", host = "attic.local", commit = oldest, made = made - 3 * day,
                    behind = null, heads = listOf(head("baude", null, null, null))),
                Msg.Member(id = "p1", name = "Phone", host = "Phone", dial = false, commit = older, made = made - day, behind = 45),
            ), gone = listOf(Msg.Gone(id = "m3", name = "cellar"))))
        val lantern = Host(id = "h2", name = "lantern", user = "dev", address = "lantern.example")
        Mesh.hosts.add(lantern)
        FixtureConnection.up(activity, lantern).state = "down"
    }

    @Test fun settingsPicked() {
        Settings.accent.value = "green"
        Settings.meteredUpdates.value = true
        Settings.textScale.value = 1.2f
        render("06-settings-green-larger") { SettingsScreen({}, {}) }
    }

    @Test fun homeLarger() {
        Settings.globalScale.value = 1.3f
        render("07-home-global-scale") { HomeScreen() }
    }

    @Test fun settingsDark() {
        Settings.appearance.value = if (theme == "light") "dark" else "light" // (against the phone's)
        Settings.accent.value = "orange"
        render("08-settings-appearance-pinned") { SettingsScreen({}, {}) }
    }

    /** Chats nested under the ones that forked or started them, two deep, with siblings after them (so a parent's
     * line runs past a child's subtree to its last child). */
    @Test fun homeNested() {
        val now = System.currentTimeMillis() / 1000.0
        fun s(id: String, title: String, cwd: String, ago: Double, fork: String? = null, by: String? = null,
              state: String = "idle") = Msg.Session(id = id, title = title, cwd = cwd, updated = now - ago, said = now - ago,
            created = now - ago - 600, state = state, forkFrom = fork, spawnedBy = by)
        Mesh.sessions["h1"] = listOf(
            s("a", "Tidy the release notes", "/home/dev/garden", 60.0, state = "working"),
            s("a1", "Check the changelog links", "/home/dev/garden", 120.0, by = "a"),
            s("a1x", "Read the old tags", "/home/dev/garden", 130.0, by = "a1"),
            s("a1y", "List the broken links", "/home/dev/garden", 140.0, by = "a1"),
            s("a2", "Shorter release notes", "/home/dev/garden", 300.0, fork = "a"),
            s("b", "Why the build is slow", "/home/dev/garden", 3_600.0),
            s("c", "Draft the README", "/home/dev/lantern", 7_200.0),
            s("c1", "README, plainer", "/home/dev/lantern", 7_300.0, fork = "c"),
            s("d", "Fix the flaky upload", "/home/dev/lantern", 86_400.0, state = "waiting"),
        )
        render("09-home-nested") { HomeScreen() }
    }

    /** workshop can't be reached: its chats listed as they were last seen, dimmed and marked. */
    @Test fun homeHostDown() {
        Mesh.conn("h1")!!.state = "down"
        render("09b-home-host-down") { HomeScreen() }
    }

    @Test fun homeSessionMenu() { // (a chat's long-press menu, on a host that's up)
        show { HomeScreen() }
        tap("Why the build is slow", long = true)
        layOut()
        screen("09c-home-session-menu")
    }

    // (a chat open on workshop, which is up: each of its pages, as its bottom bar switches to them)
    @Test fun chat() = chatAt("10-chat", "chat")
    @Test fun chatMeta() = chatAt("11-chat-meta", "meta")
    @Test fun chatSubagents() = chatAt("12-chat-subagents", "agents")
    @Test fun chatProcesses() = chatAt("13-chat-processes", "processes")
    @Test fun chatSideQuestions() = chatAt("14-chat-side-questions", "btw")
    @Test fun chatArtifacts() = chatAt("15-chat-artifacts", "artifacts")
    @Test fun chatFiles() = chatAt("16-chat-files", "files")
    @Test fun chatGit() = chatAt("17-chat-git", "git")
    @Test fun chatSignal() = chatAt("18-chat-signal", "signal")

    // (the phone turned on its side: the chat, and Meta, their pickers in the bar; then side by side, the bar's
    // side panel toggle pressed: Meta, or the Subagents page, beside the chat)
    @Test @Config(qualifiers = "+land") fun landChat() = chatAt("30-land-chat", "chat")
    @Test @Config(qualifiers = "+land") fun landMeta() = chatAt("31-land-meta", "meta")
    @Test @Config(qualifiers = "+land") fun landSplit() = chatAt("32-land-split-meta", "chat", split = true)
    @Test @Config(qualifiers = "+land") fun landSplitAgents() = chatAt("33-land-split-subagents", "agents", split = true)
    @Test @Config(qualifiers = "+land") fun landSplitInset() { // (3-button navigation down the right edge, a camera hole at the left)
        insets = androidx.core.graphics.Insets.of(72, 0, 144, 0)
        chatAt("35-land-split-side-insets", "chat", split = true)
    }
    @Test @Config(qualifiers = "+land") fun landSplitWider() { // (the rule dragged left: the pages wider)
        Settings.panelWidth.value = 420f
        chatAt("34-land-split-dragged", "processes", split = true)
    }

    // (the chat's model button pressed: Claude's models, workshop's own Ollama models, and studio's, another member of
    // the mesh: its Ollama models, one of them workshop's too, its MLX model and its Apple model; more than fit)
    @Test fun chatModelPicker() {
        meshModels()
        val chat = Mesh.openChat("h1", "a")
        fill(chat)
        show { ChatShell("h1", "a", back = {}, open = { _, _ -> }, feedback = {}) }
        tap("Opus 5.5")
        laidOut()
        withMenus("20-chat-model-picker")
        scrollUnder("Qwen") // (the list's end: the mesh's others)
        laidOut()
        withMenus("21-chat-model-picker-end")
    }

    // (Settings → Meta's Intelligence, and the New session dialog's model, pressed: the menu under the button or, with
    // less room there than the list takes, over it; then Meta's Intelligence card as the page's last row, the list above it)
    @Test fun metaModelPicker() {
        meshModels()
        dev.baude.android.Meta.host.value = "h1"
        show { SettingsScreen({}, {}) }
        tap("Haiku 4.5")
        laidOut()
        withMenus("22-settings-meta-intelligence")
    }

    @Test fun newSessionModelPicker() {
        meshModels()
        show { NewSession("h1", null, done = {}, opened = { _, _ -> }) }
        tap("Its default model")
        laidOut()
        withMenus("23-new-session-model-picker")
    }

    @Test fun metaCardModelPicker() {
        meshModels()
        dev.baude.android.Meta.host.value = "h1"
        val pane = dev.baude.android.MenuPane()
        show {
            androidx.compose.runtime.CompositionLocalProvider(dev.baude.android.LocalMenuPane provides pane) {
                androidx.compose.foundation.layout.Box(androidx.compose.ui.Modifier.fillMaxSize()) {
                    androidx.compose.foundation.layout.Column(androidx.compose.ui.Modifier.align(androidx.compose.ui.Alignment.BottomCenter)
                        .padding(bottom = 100.dp)) {
                        dev.baude.android.BoxedList(listOf(Unit), "") { dev.baude.android.IntelligenceRow() }
                    }
                    dev.baude.android.MenuPaneLayer(pane)
                }
            }
        }
        tap("Haiku 4.5")
        laidOut()
        withMenus("24-meta-card-model-picker")
    }

    /** What scrolls around text (a list with it in) scrolled to its end. */
    private fun scrollUnder(text: String) {
        fun roots(v: android.view.View): List<androidx.compose.ui.node.RootForTest> = when (v) {
            is androidx.compose.ui.node.RootForTest -> listOf(v)
            is android.view.ViewGroup -> (0 until v.childCount).flatMap { roots(v.getChildAt(it)) }
            else -> emptyList()
        }
        fun all(n: androidx.compose.ui.semantics.SemanticsNode): List<androidx.compose.ui.semantics.SemanticsNode> =
            listOf(n) + n.children.flatMap { all(it) }
        val list = windows().flatMap(::roots).flatMap { all(it.semanticsOwner.unmergedRootSemanticsNode) }.last { n ->
            n.config.contains(androidx.compose.ui.semantics.SemanticsActions.ScrollBy) && all(n).any {
                it.config.getOrElse(androidx.compose.ui.semantics.SemanticsProperties.Text) { emptyList() }.any { t -> t.text == text }
            }
        }
        list.config[androidx.compose.ui.semantics.SemanticsActions.ScrollBy].action!!.invoke(0f, 100_000f)
        settle()
    }

    /** The windows up now (the screen's, and a menu's over it), as WindowManager has them, the screen's first. */
    private fun windows(): List<android.view.View> {
        val wm = Class.forName("android.view.WindowManagerGlobal").getMethod("getInstance").invoke(null)
        @Suppress("UNCHECKED_CAST")
        val views = wm.javaClass.getDeclaredField("mViews").apply { isAccessible = true }.get(wm) as List<android.view.View>
        return views.drop(views.indexOf(activity.window.decorView))
    }

    /** Each window laid out and composed again till what its layout says (where a pane or a menu goes) has been read:
     * the rig lays a window out only as it's drawn. */
    private fun laidOut() = repeat(3) {
        for (v in windows()) if (v.width > 0 && v.height > 0)
            v.draw(android.graphics.Canvas(android.graphics.Bitmap.createBitmap(v.width, v.height, android.graphics.Bitmap.Config.ARGB_8888)))
        androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
        settle()
    }

    /** The screen with the menus that are up drawn over it, each where its window is. */
    private fun withMenus(name: String) {
        val root = activity.window.decorView
        val bitmap = android.graphics.Bitmap.createBitmap(root.width, root.height, android.graphics.Bitmap.Config.ARGB_8888)
        val canvas = android.graphics.Canvas(bitmap)
        for (v in windows()) {
            val lp = v.layoutParams as? android.view.WindowManager.LayoutParams
            canvas.save()
            if (v !== root && lp != null) canvas.translate(lp.x.toFloat(), lp.y.toFloat())
            v.draw(canvas)
            canvas.restore()
        }
        java.io.File("build/screenshots/$theme").mkdirs()
        java.io.File("build/screenshots/$theme/$name.png").outputStream().use {
            bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it)
        }
    }

    /** workshop's own Ollama (eight models), and studio in its mesh: an Ollama (llama3.2:3b, gpt-oss:20b, and
     * qwen3:8b, as workshop has it), MLX (a Mistral) and Apple's model. */
    private fun meshModels() {
        val conn = Mesh.conn("h1")!!
        fun m(value: String, id: String? = null, name: String = id ?: value, window: Long? = null) =
            Msg.ProviderModel(value = value, id = id, displayName = name, window = window)
        val own = listOf("qwen3:8b" to 40960L, "qwen3:30b" to 40960L, "gemma3:4b" to 32768L, "gemma3:12b" to 32768L,
            "llama3.1:8b" to 131072L, "deepseek-r1:8b" to 131072L, "mistral:7b" to 32768L, "phi4:14b" to 16384L)
            .map { (id, w) -> m("ollama/$id", id, window = w) }
        val init = conn.info!!.init!!
        val listed = org.json.JSONArray(init.getJSONArray("models").toString())
        for (x in own) listed.put(org.json.JSONObject().put("value", x.value).put("displayName", x.displayName))
        val studio = Msg.Member(id = "5c0ffee0-0000-4000-8000-000000000002", name = "studio", providers = listOf(
            Msg.Provider(id = "ollama", kind = "ollama", name = "Ollama", http = true, models = listOf(
                m("llama3.2:3b", "llama3.2:3b", window = 131072), m("gpt-oss:20b", "gpt-oss:20b", window = 131072),
                m("qwen3:8b", "qwen3:8b", window = 40960))),
            Msg.Provider(id = "mlx", kind = "mlx", name = "MLX", http = false, models = listOf(
                m("mlx-community/Mistral-7B-Instruct-v0.3-4bit", name = "Mistral-7B-Instruct-v0.3-4bit"))),
            Msg.Provider(id = "apple", kind = "apple", name = "Apple", http = false, models = listOf(
                m("apple", name = "Apple Foundation Model")))))
        conn.info = Msg.Info(id = "h1", name = "workshop", home = "/home/dev", init = org.json.JSONObject(init.toString())
            .put("models", listed), models = (0 until listed.length()).map { listed.getJSONObject(it) },
            providers = listOf(Msg.Provider(id = "ollama", kind = "ollama", name = "Ollama", http = true, models = own)),
            mesh = Msg.MeshView(members = listOf(Msg.Member(id = "h1", name = "workshop"), studio)))
    }

    /** The fixture chat ("Tidy the release notes") open at page, its host up: what was said in it, its subagents,
     * side questions and artifacts. */
    private fun chatAt(name: String, page: String, split: Boolean = false) {
        val chat = Mesh.openChat("h1", "a")
        fill(chat)
        if (!split) return render(name) { ChatShell("h1", "a", back = {}, open = { _, _ -> }, feedback = {}, first = page) }
        show { ChatShell("h1", "a", back = {}, open = { _, _ -> }, feedback = {}, first = page) }
        tap(dev.baude.android.Wording.SidePanel.toggleTip)
        activity.window.decorView.captureRoboImage("build/screenshots/$theme/$name.png")
    }

    private fun fill(chat: Chat) {
        chat.lines.addAll(listOf(
            Line("you", "Tidy the release notes for 0.9: group them by area and drop the internal ones."),
            Line("tool", "Read CHANGELOG.md"),
            Line("tool", "Bash git log --oneline v0.8..HEAD"),
            Line("bot", "Here's a tidier draft, grouped by area:\n\n**Sync**\n- Uploads resume after a dropped " +
                "connection\n- Faster first sync on large folders\n\n**Settings**\n- A larger default size on phones\n\n" +
                "I left out the three internal refactors. Want me to write it to `CHANGELOG.md`?"),
            Line("you", "Yes, and link each item to its PR."),
            Line("tool", "Edit CHANGELOG.md"),
        ))
        chat.agents.addAll(listOf(
            Subagent("t1", "Find each item's PR", "general-purpose", "Match each changelog line to its pull request")
                .apply { activity.value = "Searching merged pull requests" },
            Subagent("t2", "Check the links", "general-purpose", "Open every link in the notes").apply { status.value = "completed" },
            Subagent("t3", "Spell-check the notes", "general-purpose", "Spell-check CHANGELOG.md").apply { status.value = "failed" },
        ))
        chat.btw.addAll(listOf(
            SideQuestion("Is 0.9 a minor or a patch release?").apply {
                status.value = "done"
                thread.add(Line("bot", "Minor: it adds resumable uploads, a new feature."))
            },
            SideQuestion("Which PRs touched sync?").apply { status.value = "running" },
        ))
        chat.artifacts.addAll(listOf(
            Artifact("https://claude.ai/artifact/release-notes", "Release notes 0.9", icon = "doc",
                description = "The notes as they'll be published"),
            Artifact("https://claude.ai/artifact/sync-times", "First sync times", icon = "chart",
                description = "Before and after, by folder size"),
        ))
    }

    @Composable
    private fun HomeScreen() = Home(open = { _, _ -> }, feedback = {})

    private fun render(name: String, screen: @Composable () -> Unit) {
        show(screen)
        activity.window.decorView.captureRoboImage("build/screenshots/$theme/$name.png")
    }

    /** What's on the screen, its dialogs too. */
    private fun screen(name: String) = captureScreenRoboImage("build/screenshots/$theme/$name.png")

    /** Every window laid out again: a menu opened with nothing else on screen changing is placed (its anchor's
     * position known) only once its window's laid out, and drawn only then. */
    private fun layOut() {
        val wm = Class.forName("android.view.WindowManagerGlobal").getMethod("getInstance").invoke(null)
        @Suppress("UNCHECKED_CAST")
        val views = wm.javaClass.getDeclaredField("mViews").apply { isAccessible = true }.get(wm) as List<android.view.View>
        fun roots(v: android.view.View): List<androidx.compose.ui.node.RootForTest> = when (v) {
            is androidx.compose.ui.node.RootForTest -> listOf(v)
            is android.view.ViewGroup -> (0 until v.childCount).flatMap { roots(v.getChildAt(it)) }
            else -> emptyList()
        }
        views.flatMap(::roots).forEach { it.measureAndLayoutForTest() }
        settle()
    }

    /** The menu that's up (a window of its own), alone. */
    private fun menu(name: String) {
        val wm = Class.forName("android.view.WindowManagerGlobal").getMethod("getInstance").invoke(null)
        @Suppress("UNCHECKED_CAST")
        val views = wm.javaClass.getDeclaredField("mViews").apply { isAccessible = true }.get(wm) as List<android.view.View>
        val v = views.last()
        val bitmap = android.graphics.Bitmap.createBitmap(v.width, v.height, android.graphics.Bitmap.Config.ARGB_8888)
        v.draw(android.graphics.Canvas(bitmap))
        java.io.File("build/screenshots/$theme").mkdirs()
        java.io.File("build/screenshots/$theme/$name.png").outputStream().use {
            bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it)
        }
    }

    /** The nth thing on screen (its dialogs and menus too) whose words or description are label tapped, as Compose's
     * semantics have it (its click action; long: its long-press). */
    private fun tap(label: String, nth: Int = 0, long: Boolean = false) {
        val act = if (long) androidx.compose.ui.semantics.SemanticsActions.OnLongClick else androidx.compose.ui.semantics.SemanticsActions.OnClick
        val wm = Class.forName("android.view.WindowManagerGlobal").getMethod("getInstance").invoke(null)
        @Suppress("UNCHECKED_CAST")
        val views = wm.javaClass.getDeclaredField("mViews").apply { isAccessible = true }.get(wm) as List<android.view.View>
        fun roots(v: android.view.View): List<androidx.compose.ui.node.RootForTest> = when (v) {
            is androidx.compose.ui.node.RootForTest -> listOf(v)
            is android.view.ViewGroup -> (0 until v.childCount).flatMap { roots(v.getChildAt(it)) }
            else -> emptyList()
        }
        fun all(n: androidx.compose.ui.semantics.SemanticsNode): List<androidx.compose.ui.semantics.SemanticsNode> =
            listOf(n) + n.children.flatMap { all(it) }
        fun hits() = views.drop(views.indexOf(activity.window.decorView)).flatMap(::roots) // (this test's windows)
            .flatMap { all(it.semanticsOwner.rootSemanticsNode) }.filter { n ->
                val c = n.config
                c.contains(act) &&
                    (c.getOrElse(androidx.compose.ui.semantics.SemanticsProperties.Text) { emptyList() }.any { it.text == label } ||
                        c.getOrElse(androidx.compose.ui.semantics.SemanticsProperties.ContentDescription) { emptyList() }.contains(label))
            }
        repeat(20) { if (hits().size <= nth) settle() } // (a menu that's opening: once it's up)
        hits()[nth].config[act].action!!.invoke()
        androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications() // (what it changed: composed again)
        settle()
    }

    private var insets: androidx.core.graphics.Insets? = null

    private fun show(screen: @Composable () -> Unit) {
        activity.setContent { BaudeTheme { screen() } }
        settle()
        insets?.let { i -> // (fake system bars at the sides)
            val w = androidx.core.view.WindowInsetsCompat.Builder()
                .setInsets(androidx.core.view.WindowInsetsCompat.Type.systemBars(), i).build()
            fun give(v: android.view.View) {
                androidx.core.view.ViewCompat.dispatchApplyWindowInsets(v, w)
                if (v is android.view.ViewGroup) for (k in 0 until v.childCount) give(v.getChildAt(k))
            }
            give(activity.window.decorView)
        }
        settle()
    }

    /** Two hosts of fixture names, and their sessions, as a phone that's connected to them lists them. */
    private fun fixtures() {
        val pair = KeyPairGenerator.getInstance("EC").apply { initialize(256) }.generateKeyPair()
        Mesh.key.value = Key(pair.private, pair.public as ECPublicKey)
        val workshop = Host(id = "h1", name = "workshop", user = "dev", address = "workshop.example")
        Mesh.hosts.add(workshop)
        FixtureConnection.up(activity, workshop) // (up, never reached)
        val now = System.currentTimeMillis() / 1000.0
        fun s(id: String, title: String, cwd: String, ago: Double, state: String = "idle") =
            Msg.Session(id = id, title = title, cwd = cwd, updated = now - ago, said = now - ago, created = now - ago - 600,
                state = state, model = "opus", effort = "high", mode = "default")
        Mesh.sessions["h1"] = listOf(
            s("a", "Tidy the release notes", "/home/dev/garden", 60.0, "working"),
            s("b", "Why the build is slow", "/home/dev/garden", 3_600.0),
            s("c", "Draft the README", "/home/dev/lantern", 7_200.0),
            s("d", "Fix the flaky upload", "/home/dev/lantern", 86_400.0, "waiting"),
            s("e", "Sketch the settings page", "/home/dev/lantern", 172_800.0),
        )
    }
}

@RunWith(RobolectricTestRunner::class)
@Config(qualifiers = "w411dp-h891dp-notnight-xxhdpi")
class LightScreens : Screens("light")

@RunWith(RobolectricTestRunner::class)
@Config(qualifiers = "w411dp-h891dp-night-xxhdpi")
class DarkScreens : Screens("dark")

