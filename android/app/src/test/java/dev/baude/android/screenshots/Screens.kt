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
        dev.baude.android.HomePrefs.host.value = ""
        dev.baude.android.Meta.load(org.json.JSONObject())
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

    // (Settings → Subscriptions on workshop, the host picked, which has claude: Claude's Work and Home, folders signed
    // in to with claude auth login, Work the default; Codex's Personal; then its usage, an account's ⋮, Add account
    // (the sign-in, its page's link and the code box) and Remove…)
    @Test fun subscriptions() {
        accountsFixture()
        show { SettingsScreen({}, {}, page = 1) }
        laidOut()
        screen("05a-subscriptions")
    }

    @Test fun subscriptionsUsage() {
        accountsFixture()
        show { SettingsScreen({}, {}, page = 1) }
        laidOut()
        tap("Subscription usage")
        laidOut()
        screen("05f-subscriptions-usage")
    }

    @Test fun subscriptionsMenu() {
        accountsFixture()
        show { SettingsScreen({}, {}, page = 1) }
        laidOut()
        tap("Change this account")
        laidOut()
        withMenus("05b-subscriptions-menu")
    }

    @Test fun subscriptionsSignIn() {
        accountsFixture()
        val host = dev.baude.android.HostAccounts(Mesh.hosts.first().id)
        val login = dev.baude.android.ClaudeLogin(host, null).apply {
            name.value = "Work"
            url.value = "https://claude.ai/oauth/authorize?code=true&client_id=fixture&state=fixture"
            busy.value = true
        }
        show {
            SettingsScreen({}, {}, page = 1)
            dev.baude.android.ClaudeSignIn(host, null, {}, login)
        }
        laidOut()
        screen("05d-subscriptions-sign-in")
    }

    @Test fun subscriptionsRemove() {
        accountsFixture()
        show { SettingsScreen({}, {}, page = 1) }
        laidOut()
        tap("Change this account")
        tap("Remove…")
        screen("05e-subscriptions-remove")
    }

    // (the phone as the host picked: no accounts listed; then, on a host without claude, Add account's token form and a
    // token account's Replace Token…)
    @Test fun subscriptionsPhone() {
        phoneAccountsFixture()
        show { SettingsScreen({}, {}, page = 1) }
        laidOut()
        screen("05g-subscriptions-phone")
    }

    @Test fun subscriptionsPhoneAdd() {
        tokenAccountsFixture()
        show { SettingsScreen({}, {}, page = 1) }
        laidOut()
        tap("Add account")
        screen("05h-subscriptions-phone-add")
    }

    @Test fun subscriptionsToken() {
        tokenAccountsFixture()
        show { SettingsScreen({}, {}, page = 1) }
        laidOut()
        tap("Change this account")
        tap("Replace Token…")
        screen("05c-subscriptions-token")
    }

    private fun accountsFixture() {
        FixtureConnection.up(activity, Mesh.hosts.first()) { m ->
            if (m.optString("op") != "accounts") null
            else org.json.JSONObject().put("op", "accounts").put("req", m.opt("req")).put("what", m.optString("what"))
                .put("accounts", org.json.JSONArray(listOf(HOME_ACCOUNT, WORK,
                    org.json.JSONObject().put("id", "p1").put("provider", "codex").put("name", "Personal").put("kind", "profile"))))
                .put("defaults", org.json.JSONObject().put("claude", "w1").put("codex", "")).put("claude_cli", true)
                .apply { if (m.optString("what") == "usage") put("usage", org.json.JSONArray(listOf(
                    org.json.JSONObject().put("name", "Five hour").put("percent", 25),
                    org.json.JSONObject().put("name", "Seven day").put("percent", 40)))) }
        }
    }

    private fun tokenAccountsFixture() {
        FixtureConnection.up(activity, Mesh.hosts.first()) { m ->
            if (m.optString("op") != "accounts") null
            else org.json.JSONObject().put("op", "accounts").put("req", m.opt("req")).put("what", m.optString("what"))
                .put("accounts", org.json.JSONArray(listOf(
                    org.json.JSONObject().put("id", "t1").put("provider", "claude").put("name", "Personal").put("kind", "token"))))
                .put("defaults", org.json.JSONObject().put("claude", "t1").put("codex", "")).put("claude_cli", false)
        }
    }

    private fun phoneAccountsFixture() {
        FixturePhone.up(activity, "pocket") { m ->
            if (m.optString("op") != "accounts") null
            else org.json.JSONObject().put("op", "accounts").put("req", m.opt("req")).put("what", m.optString("what"))
                .put("accounts", org.json.JSONArray(listOf(
                    org.json.JSONObject().put("id", "t1").put("provider", "claude").put("name", "Personal").put("kind", "token"))))
                .put("defaults", org.json.JSONObject().put("claude", "t1").put("codex", "")).put("claude_cli", false)
        }
        dev.baude.android.HomePrefs.host.value = "phone"
    }

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

    @Test fun spawnAgentHosts() {
        versionsFixture()
        spawnForm()
        tap("workshop")
        laidOut()
        withMenus("19-spawn-agent-hosts")
    }

    @Test fun newSessionOpens() = newChat("92-new-session") { tap("New session") }

    @Test fun folderPlusOpens() = newChat("93-folder-plus") { tap("New session in ~/garden, on workshop") }

    /** Open folder…: the step from the host's home, its folders listed; a path typed, the folders it begins; a folder
     * tapped, browsed into; Open: the chat in it. */
    @Test fun openFolderOpens() = newChat("95-open-folder-opened") {
        tap("Open folder…")
        screen("94-open-folder")
        typeIn("/home/dev/ga")
        screen("94b-open-folder-typed")
        tap("garden")
        screen("94c-open-folder-browsed")
        tap("Open")
    }

    /** A new chat's first message sent: it's the host's from then on (no longer a draft), and listed at home. */
    @Test fun newChatFirstMessage() {
        newChat("96-new-chat-written") { tap("New session in ~/garden, on workshop") }
        val chat = Mesh.openChat("h1", "n")
        chat.draft.value = "Tidy the changelog for 2.4"
        settle()
        tap("Send")
        val s = Mesh.sessions["h1"].orEmpty()
        Mesh.sessions["h1"] = s.map { if (it.id == "n") Msg.Session.from(it.json().put("draft", false)
            .put("title", "Tidy the changelog for 2.4").put("said", System.currentTimeMillis() / 1000.0)) else it }
        settle()
        activity.window.decorView.captureRoboImage("build/screenshots/$theme/97-new-chat-sent.png")
        chat.draft.value = "/new" // (a new chat on workshop, in this one's folder)
        settle()
        tap("Send")
        laidOut()
        activity.window.decorView.captureRoboImage("build/screenshots/$theme/99-slash-new.png")
        Mesh.close()
        render("98-new-chat-listed") { HomeScreen() }
    }

    /** Home, its new chat asked for (how: a tap), the host starting it as a draft ("n", in the folder asked for), and
     * the chat that's opened at once. */
    private fun newChat(name: String, how: () -> Unit) {
        val now = System.currentTimeMillis() / 1000.0
        val folders = mapOf("/home/dev" to listOf("garden", "notes", ".config"), "/home/dev/garden" to listOf("docs", "src", "tests"))
        var made = 0
        FixtureConnection.up(activity, Mesh.hosts[0]) { m ->
            if (m.optString("op") == "list_dir") return@up folders[m.optString("path").trimEnd('/')]?.let { names ->
                org.json.JSONObject().put("op", "dir").put("path", m.optString("path")).put("entries",
                    org.json.JSONArray(names.map { org.json.JSONObject().put("name", it).put("dir", true) } +
                        org.json.JSONObject().put("name", "README.md").put("dir", false)))
            } ?: org.json.JSONObject().put("op", "dir").put("path", m.optString("path")).put("error", "No such folder")
            if (m.optString("op") != "start") return@up null
            val id = if (made++ == 0) "n" else "n$made"
            println("start ${m.optString("cwd")}: $id") // (where it was asked to start: the run's output says)
            val s = Msg.Session(id = id, cwd = m.optString("cwd"), created = now, updated = now, state = "idle",
                mode = "auto", draft = true, group = m.optString("group"))
            Mesh.sessions["h1"] = Mesh.sessions["h1"].orEmpty() + s
            org.json.JSONObject().put("op", "started").put("session", org.json.JSONObject().put("id", id)
                .put("cwd", m.optString("cwd")).put("draft", true))
        }
        val opened = androidx.compose.runtime.mutableStateOf<Pair<String, String>?>(null)
        show {
            val at = opened.value
            if (at == null) Home(open = { h, s -> opened.value = h to s }, feedback = {})
            else androidx.compose.runtime.key(at) {
                ChatShell(at.first, at.second, back = {}, open = { h, s -> opened.value = h to s }, feedback = {}) }
        }
        how()
        settle()
        laidOut()
        activity.window.decorView.captureRoboImage("build/screenshots/$theme/$name.png")
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

    // (This phone's Name and Key Material, Add Host, and a host's key to trust)
    @Test fun addHost() {
        show { dev.baude.android.AddHost(done = {}) }
        screen("45-add-host")
    }

    @Test fun phoneNewKey() {
        phoneFixture()
        show { PhoneScreen({}, open = {}) }
        tap(dev.baude.android.Wording.PhoneKeys.newKey)
        screen("46-phone-new-key")
    }

    @Test fun phoneRename() {
        phoneFixture()
        show { PhoneScreen({}, open = {}) }
        tap(dev.baude.android.Wording.Common.name)
        screen("47-phone-rename")
    }

    @Test fun hostKey() {
        val c = Mesh.conn("h1")!!
        c.state = "key"
        c.shown = dev.baude.android.ShownKey("ssh-ed25519", HOST_KEY)
        show { dev.baude.android.HostKeyAsk() }
        screen("48-host-key")
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

    /** The Git page on the parity rig's repository (its recorded answers), rowan-lee's picture a portrait as GitHub gives it, ada's none (initials). */
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
        Pictures.authors(mapOf(
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

    /** The phone's two Bots: Juniper's picture a portrait on a dark ground, Fern with none (initials). */
    private fun botPictures() {
        Mesh.pictures["phone/p-a/null"] = Pictures.portrait(0xFF1D1D20.toInt(), 0xFFE0B48A.toInt(), 0xFF3A3A40.toInt(), 0xFF141414.toInt())
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

    /** This phone as a host of its own, named pocket: its connection up, two Bots of its own and a chat with no Bot name, and one in its
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
            s("p-b", "Fern", "Read the bus timetable", 5_400.0),
            Msg.Session(id = "p-c", title = "Sort the seed packets", cwd = "/data/phone/agents/p-c", updated = now - 9_000.0,
                said = now - 9_000.0, created = now - 9_600.0, state = "idle", model = "qwen3:8b", mode = "default"))
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
    /** The chat with the phone's text at a size whose line is taller than the box's buttons. */
    @Test fun chatLargerText() {
        Settings.textScale.value = 2.0f
        chatAt("10b-chat-larger-text", "chat")
    }

    /** The same with two lines written in the box. */
    @Test fun chatLargerTextTyped() {
        Settings.textScale.value = 2.0f
        val chat = Mesh.openChat("h1", "a")
        fill(chat)
        chat.draft.value = "Group them by area\nand link each item to its PR"
        render("10c-chat-larger-text-typed") { ChatShell("h1", "a", back = {}, open = { _, _ -> }, feedback = {}, first = "chat") }
    }
    @Test fun chatMeta() = chatAt("11-chat-meta", "meta")
    @Test fun chatSubagents() = chatAt("12-chat-subagents", "agents")
    // (Meta's forms, cards in its conversation: one brought up, filled by Meta, submitted; another scrolled past,
    // pinned at the top, its long-press menu, and unpinned)
    @Test fun metaForms() {
        val m = dev.baude.android.Meta
        val tools = dev.baude.android.MetaTools
        m.host.value = "h1"
        m.chat.hostId = "h1"
        fun asked(text: String) = m.chat.lines.add(Line("you", text, java.util.UUID.randomUUID().toString()))
        fun called(name: String, args: org.json.JSONObject): String {
            val key = "c${m.calls.size + 1}"
            val call = dev.baude.android.MetaCall(key, key, "mcp__meta__$name", args)
            m.calls[key] = call
            m.chat.lines.add(Line("metacall", key))
            var said = ""
            tools.call(name, args, call) { t, e -> said = t; call.state = if (e) "error" else "done"; call.result = t }
            androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications() // (what it changed: composed again)
            settle()
            return said
        }
        HomePrefs.made.clear()
        asked("Make a group for the seedling chats")
        called("open_form", org.json.JSONObject().put("form", "new_group"))
        val id = tools.forms.keys.first()
        val chat = Mesh.openChat("h1", "a")
        fill(chat)
        show { ChatShell("h1", "a", back = {}, open = { _, _ -> }, feedback = {}, first = "meta") }
        screen("100-meta-form-open")
        called("fill_form", org.json.JSONObject().put("id", id).put("values", org.json.JSONObject().put("name", "Seedlings")))
        m.chat.lines.add(Line("bot", "The form is up with the name. Press Create to make it."))
        androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
        settle()
        screen("101-meta-form-filled")
        tap(dev.baude.android.Wording.Common.create)
        screen("102-meta-form-submitted")
        asked("Start a chat on workshop to plan the watering")
        called("open_form", org.json.JSONObject().put("form", "spawn_agent").put("values",
            org.json.JSONObject().put("prompt", "Plan the watering for the garden")))
        for (i in 1..10) m.chat.lines.add(Line("bot", "Pick where it runs and press Start. The folder is the one last used " +
            "on workshop, and the group is left empty, so it's listed with no group. ($i)"))
        androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
        laidOut() // (followed to its end, as a chat read to its end is)
        screen("103-meta-form-pinned")
        longPress(dev.baude.android.Wording.Meta.unpin)
        laidOut()
        withMenus("104-meta-form-pin-menu")
        tap(dev.baude.android.Wording.Meta.unpin)
        screen("105-meta-form-unpinned")
    }

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

    // (a turn on workshop whose sign-in ran out, as claude says it: its card in place of the error, then signing in
    // again from it: the sign-in page's link, then signed in, with Send again)
    @Test fun chatLoginExpired() = loginAt("19a-chat-login-expired")
    @Test fun chatLoginPage() = loginAt("19b-chat-login-page", then = { stage.value = "page"; url.value = "https://claude.ai/oauth/authorize?code=true" })
    @Test fun chatLoginSigned() = loginAt("19c-chat-login-signed", then = { stage.value = "signed"; said.value = "ada@example.com (Example Org · team)" })

    // (signed in while another turn runs: its claude there started again once that turn's over, not under it)
    @Test fun chatLoginSignedWorking() = loginAt("19d-chat-login-signed-working", working = true) {
        stage.value = "signed"; said.value = "ada@example.com (Example Org · team)"
    }

    // (a chat under workshop's Claude account Work whose turn failed to sign in: Sign in, which signs in to Work's own
    // folder there, not the CLI's own sign-in)
    @Test fun chatLoginAccount() = loginAt("19e-chat-login-account", account = true)

    private fun loginAt(name: String, working: Boolean = false, account: Boolean = false,
                        then: dev.baude.android.Login.() -> Unit = {}) {
        if (account) Mesh.sessions["h1"] = Mesh.sessions["h1"].orEmpty().map {
            if (it.id == "b") Msg.Session.from(it.json().put("account", WORK)) else it
        }
        val chat = Mesh.openChat("h1", "b")
        if (working) chat.thinking.value = true
        val asked = "Why is the release build twice as slow as last week's?"
        chat.lines.addAll(listOf(Line("you", asked), Line("error", EXPIRED)))
        chat.chrome.turnSent = asked to emptyList() // (sent from this phone)
        val said = org.json.JSONObject().put("type", "result").put("subtype", "success").put("is_error", true)
            .put("result", EXPIRED)
        dev.baude.android.chrome(chat)("h1", org.json.JSONObject().put("op", "claude").put("session", "b").put("msg", said))
        chat.lines.mapNotNull { dev.baude.android.Logins.all[it.text].takeIf { _ -> it.kind == "login" } }.forEach { it.then() }
        render(name) { ChatShell("h1", "b", back = {}, open = { _, _ -> }, feedback = {}, first = "chat") }
    }

    // (Claude's own events in a turn, as its host passes them on: a commit and a push it saw, a commit in another
    // folder, its background agents stopped, a notification, a warning, and events a chat says nothing of; each said
    // as claude_events has it, no raw names; then notes the chat makes itself)
    @Test fun chatClaudeEvents() {
        val chat = Mesh.openChat("h1", "b")
        chat.lines.add(Line("you", "Fix the login timeout, commit it and push the branch."))
        fun event(subtype: String, vararg fields: Pair<String, String>) {
            val said = org.json.JSONObject().put("type", "system").put("subtype", subtype)
            for ((k, v) in fields) said.put(k, v)
            dev.baude.android.chrome(chat)("h1", org.json.JSONObject().put("op", "claude").put("session", "b").put("msg", said))
        }
        event("vcs_state_changed", "kind" to "commit", "branch" to "fix/login-timeout")
        event("vcs_state_changed", "kind" to "push", "branch" to "fix/login-timeout")
        event("vcs_state_changed", "kind" to "commit", "branch" to "docs/readme", "cwd" to "/work/docs-tree")
        event("agents_killed")
        event("notification", "text" to "Fast mode is off until 5pm")
        event("permission_retry", "content" to "Retrying Bash with the permission you gave", "level" to "warning")
        event("instruction_size_warning", "content" to "", "level" to "warning")
        event("hook_progress", "content" to "Running the pre-commit hook", "level" to "info")
        event("made_up_event")
        chat.lines.add(Line("bot", "Committed the fix and pushed the branch."))
        chat.lines.add(Line("note", dev.baude.android.Wording.ChatHeader.modelNote(name = "Opus 4.7")))
        chat.lines.add(Line("note", dev.baude.android.Wording.ToolCalls.deniedTool(tool = "Bash")))
        chat.lines.add(Line("note", dev.baude.android.Wording.ChatNotes.interrupted))
        render("19f-chat-claude-events") { ChatShell("h1", "b", back = {}, open = { _, _ -> }, feedback = {}, first = "chat") }
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

    @Test fun spawnAgentModelPicker() {
        meshModels()
        spawnForm()
        tap("Its default model")
        laidOut()
        withMenus("23-spawn-agent-model-picker")
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

    /** The launcher's icon (mipmap ic_launcher, made of schema/icons/apps), as a launcher draws it: in its colours, then
     * themed (Android 13's: its monochrome layer on the system palette's colours, as Launcher3 takes them), each at
     * 192 px on the theme's background. */
    @Test fun launcherIcon() {
        val icon = activity.getDrawable(dev.baude.android.R.mipmap.ic_launcher) as android.graphics.drawable.AdaptiveIconDrawable
        val dark = theme == "dark"
        val bg = activity.getColor(if (dark) android.R.color.system_neutral1_800 else android.R.color.system_accent1_100)
        val fg = activity.getColor(if (dark) android.R.color.system_accent1_100 else android.R.color.system_neutral2_700)
        val themed = android.graphics.drawable.AdaptiveIconDrawable(android.graphics.drawable.ColorDrawable(bg),
            icon.monochrome!!.mutate().apply { setTint(fg) })
        val (px, gap) = 192 to 32
        val bitmap = android.graphics.Bitmap.createBitmap(2 * px + 3 * gap, px + 2 * gap, android.graphics.Bitmap.Config.ARGB_8888)
        val canvas = android.graphics.Canvas(bitmap)
        canvas.drawColor(if (dark) 0xFF242424.toInt() else 0xFFFAFAFA.toInt())
        for ((i, d) in listOf(icon, themed).withIndex()) {
            d.setBounds(gap + i * (px + gap), gap, gap + i * (px + gap) + px, gap + px)
            d.draw(canvas)
        }
        java.io.File("build/screenshots/$theme").mkdirs()
        java.io.File("build/screenshots/$theme/42-launcher-icon.png").outputStream().use {
            bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it)
        }
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

    /** A message of yours tapped: its revert button beside its last line. Then a queued one tapped: its ✕, ↑ and
     * revert beside its last line. */
    @Test fun chatMessageTapped() = messageTapped("")

    /** The same with the phone's text at its larger size. */
    @Test fun chatMessageTappedLarger() {
        Settings.textScale.value = 1.3f
        messageTapped("-larger")
    }

    private fun messageTapped(suffix: String) {
        val chat = Mesh.openChat("h1", "a")
        chat.lines.addAll(listOf(
            Line("you", "Tidy the release notes for 0.9.", uuid = "u1"),
            Line("bot", "Here's a tidier draft, grouped by area. Want me to write it to `CHANGELOG.md`?"),
            Line("you", "Yes, and link each item to its PR. Keep the sync items first, then settings, and leave the " +
                "internal refactors out.", uuid = "u2"),
            Line("tool", "Edit CHANGELOG.md"),
        ))
        chat.loaded.value = true
        chat.queued.add(dev.baude.android.Queued("Then tag the release as v0.9.0 and push the tag.", emptyList()))
        show { ChatShell("h1", "a", back = {}, open = { _, _ -> }, feedback = {}) }
        press("Keep the sync items first")
        repeat(20) { frame() } // (the buttons' fade finished)
        activity.window.decorView.captureRoboImage("build/screenshots/$theme/40-chat-message-tapped$suffix.png")
        press("Then tag the release")
        repeat(20) { frame() }
        activity.window.decorView.captureRoboImage("build/screenshots/$theme/41-chat-queued-tapped$suffix.png")
    }

    /** A chat at a turn with two messages queued under it, another chat opened, then the first again: its queue as it
     * was left. */
    @Test fun chatQueueKept() {
        val sid = androidx.compose.runtime.mutableStateOf("a")
        queueThenLeave(sid)
        switch(sid, "a")
        chatSoFar(Mesh.open!!)
        repeat(20) { frame() }
        activity.window.decorView.captureRoboImage("build/screenshots/$theme/50-chat-queue-kept.png")
    }

    /** The same, its turn then ending while the other chat's open (its host lists it idle): what was queued sent to it
     * then (by the input its host takes), and in its conversation as it's opened again. */
    @Test fun chatQueueSentElsewhere() {
        val sent = ArrayList<org.json.JSONObject>()
        FixtureConnection.up(activity, Mesh.hosts.first()) { m ->
            if (m.optString("op") == "input" && m.optJSONObject("msg")?.optString("type") == "user")
                org.json.JSONObject().put("op", "accepted").also { sent.add(m) } else null }
        val sid = androidx.compose.runtime.mutableStateOf("a")
        queueThenLeave(sid)
        val listed = org.json.JSONObject().put("op", "sessions").put("sessions", org.json.JSONArray(Mesh.sessions["h1"]!!.map {
            it.json().apply { if (it.id == "a") put("state", "idle") } }))
        Mesh::class.java.getDeclaredMethod("heard", dev.baude.android.Connection::class.java, org.json.JSONObject::class.java)
            .apply { isAccessible = true }.invoke(Mesh, Mesh.states["h1"], listed)
        repeat(10) {
            settle()
            Thread.sleep(100) // (the message made off the main thread)
        }
        switch(sid, "a")
        val chat = Mesh.open!!
        chatSoFar(chat)
        for (m in sent) { // (as its transcript has it now)
            val blocks = m.getJSONObject("msg").getJSONObject("message").getJSONArray("content")
            val text = (0 until blocks.length()).map { blocks.getJSONObject(it) }.filter { it.optString("type") == "text" }
                .joinToString("\n\n") { it.optString("text") }
            chat.lines.add(Line("you", text, uuid = m.getJSONObject("msg").optString("uuid")))
            chat.lines.add(Line("bot", "On it: done."))
        }
        repeat(20) { frame() }
        activity.window.decorView.captureRoboImage("build/screenshots/$theme/50b-chat-queue-sent-elsewhere.png")
    }

    /** Chat a's conversation as its host has it: a long turn begun. */
    private fun chatSoFar(chat: Chat) {
        chat.lines.addAll(listOf(Line("you", "Tidy the release notes for 0.9.", uuid = "u1"),
            Line("bot", "Going through the notes now.")))
        chat.loaded.value = true
    }

    /** Chat a open (sid's), at a turn, two messages sent meanwhile (queued under it), then chat b opened. */
    private fun queueThenLeave(sid: androidx.compose.runtime.MutableState<String>) {
        Mesh.drafts.clear() // (none another screen left)
        show { androidx.compose.runtime.key(sid.value) { ChatShell("h1", sid.value, back = {}, open = { _, _ -> }, feedback = {}) } }
        val chat = Mesh.open!!
        chatSoFar(chat)
        dev.baude.android.Queue.submit(chat, true, "Then tag the release as v0.9.0.", emptyList())
        dev.baude.android.Queue.submit(chat, true, "And push the tag to origin.", emptyList())
        repeat(10) { frame() }
        switch(sid, "b")
    }

    /** The chat shown (sid's) switched to another, as opening it from the list does. */
    private fun switch(sid: androidx.compose.runtime.MutableState<String>, to: String) {
        sid.value = to
        androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
        settle()
        check(Mesh.open?.sid == to) { "chat $to not open" }
    }

    /** A long chat opened for the first time, its transcript read in parts as a host sends it, a picture you sent
     * near its end read a moment after it's drawn, taking its size then: open at its end. */
    @Test fun chatOpened() {
        val chat = Mesh.openChat("h1", "b")
        show { ChatShell("h1", "b", back = {}, open = { _, _ -> }, feedback = {}) }
        for (part in longChat().chunked(12)) {
            chat.lines.addAll(part)
            androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
            repeat(3) { frame() }
        }
        chat.loaded.value = true
        androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
        repeat(60) { frame() }
        Thread.sleep(1500) // (the picture read meanwhile, off the main thread)
        repeat(30) { frame() }
        activity.window.decorView.captureRoboImage("build/screenshots/$theme/42-chat-opened.png")
    }

    /** The long chat scrolled up by a drag, left for the home screen, and opened again: where it was left. */
    @Test fun chatReopened() {
        val chat = Mesh.openChat("h1", "b")
        chat.lines.addAll(longChat())
        chat.loaded.value = true
        show { ChatShell("h1", "b", back = {}, open = { _, _ -> }, feedback = {}) }
        repeat(30) { frame() }
        drag(from = 0.3f, to = 0.75f)
        drag(from = 0.3f, to = 0.75f)
        activity.window.decorView.captureRoboImage("build/screenshots/$theme/43-chat-left.png")
        show { HomeScreen() }
        Mesh.close()
        val again = Mesh.openChat("h1", "b")
        again.lines.addAll(longChat())
        again.loaded.value = true
        show { ChatShell("h1", "b", back = {}, open = { _, _ -> }, feedback = {}) }
        repeat(30) { frame() }
        activity.window.decorView.captureRoboImage("build/screenshots/$theme/43-chat-reopened.png")
    }

    /** At the end, lines come in and the drag that interrupts their glide ends at the end again: a reply streaming
     * after that is still followed. */
    @Test fun chatFollowAfterInterrupt() {
        Mesh.close()
        dev.baude.android.ChatPlaces.keep("h1/b", androidx.compose.foundation.lazy.LazyListState(), true) // (none kept: opened at its end)
        val chat = Mesh.openChat("h1", "b")
        chat.lines.addAll(longChat())
        chat.loaded.value = true
        show { ChatShell("h1", "b", back = {}, open = { _, _ -> }, feedback = {}) }
        repeat(90) { frame() }
        chat.lines.addAll((1..3).map { Line("bot", "A late line $it: the profile shows the same plugins again, in the same order.") })
        repeat(3) { frame() }
        drag(from = 0.5f, to = 0.55f)
        repeat(6) { drag(from = 0.7f, to = 0.15f) }
        chat.streaming.value = (1..12).joinToString("\n\n") { "Streamed paragraph $it of the reply, which keeps on growing below." }
        repeat(60) { frame() }
        activity.window.decorView.captureRoboImage("build/screenshots/$theme/46-chat-follow-after-interrupt.png")
    }

    /** A code block scrolled by a finger, then what you send: the end comes into view. */
    @Test fun chatCodeScrolledThenSend() {
        Mesh.close()
        dev.baude.android.ChatPlaces.keep("h1/b", androidx.compose.foundation.lazy.LazyListState(), true) // (none kept: opened at its end)
        val chat = Mesh.openChat("h1", "b")
        chat.lines.addAll(longChat().takeLast(4))
        chat.lines.add(Line("bot", "```\n" + (1..80).joinToString("\n") { "line $it of the build log" } + "\n```"))
        chat.loaded.value = true
        show { ChatShell("h1", "b", back = {}, open = { _, _ -> }, feedback = {}) }
        repeat(60) { frame() }
        drag(from = 0.6f, to = 0.4f)
        chat.lines.addAll((1..4).map { Line("bot", "A reply line $it, after the log.") })
        repeat(60) { frame() }
        activity.window.decorView.captureRoboImage("build/screenshots/$theme/47-chat-code-scrolled-then-more.png")
    }

    /** The long chat left scrolled up, opened again while its host is down (no lines): its end, then the lines
     * arrive and it's put back where it was left. */
    @Test fun chatReopenedHostDown() {
        Mesh.close()
        dev.baude.android.ChatPlaces.keep("h1/b", androidx.compose.foundation.lazy.LazyListState(), true) // (none kept: opened at its end)
        val chat = Mesh.openChat("h1", "b")
        chat.lines.addAll(longChat())
        chat.loaded.value = true
        show { ChatShell("h1", "b", back = {}, open = { _, _ -> }, feedback = {}) }
        repeat(30) { frame() }
        drag(from = 0.3f, to = 0.75f)
        drag(from = 0.3f, to = 0.75f)
        activity.window.decorView.captureRoboImage("build/screenshots/$theme/48-chat-left-again.png")
        show { HomeScreen() }
        Mesh.close()
        val again = Mesh.openChat("h1", "b")
        again.loaded.value = true
        show { ChatShell("h1", "b", back = {}, open = { _, _ -> }, feedback = {}) }
        repeat(150) { frame() }
        activity.window.decorView.captureRoboImage("build/screenshots/$theme/48b-chat-reopened-host-down.png")
        again.lines.addAll(longChat())
        repeat(30) { frame() }
        activity.window.decorView.captureRoboImage("build/screenshots/$theme/49-chat-reopened-host-back.png")
    }

    /** The keyboard up, then partway down past the bottom bar (as it is mid-way through going down): the keyboard
     * drawn as a grey block where it is, which the rig doesn't draw. */
    @Test fun chatKeyboard() {
        val chat = Mesh.openChat("h1", "a")
        fill(chat)
        show { ChatShell("h1", "a", back = {}, open = { _, _ -> }, feedback = {}) }
        for ((name, dp) in listOf("44-chat-keyboard-up" to 300, "45-chat-keyboard-going-down" to 40)) {
            val px = (dp * activity.resources.displayMetrics.density).toInt()
            val w = androidx.core.view.WindowInsetsCompat.Builder()
                .setInsets(androidx.core.view.WindowInsetsCompat.Type.ime(), androidx.core.graphics.Insets.of(0, 0, 0, px))
                .setVisible(androidx.core.view.WindowInsetsCompat.Type.ime(), true).build()
            fun give(v: android.view.View) {
                androidx.core.view.ViewCompat.dispatchApplyWindowInsets(v, w)
                if (v is android.view.ViewGroup) for (k in 0 until v.childCount) give(v.getChildAt(k))
            }
            give(activity.window.decorView)
            repeat(20) { frame() }
            val v = activity.window.decorView
            val bitmap = android.graphics.Bitmap.createBitmap(v.width, v.height, android.graphics.Bitmap.Config.ARGB_8888)
            val canvas = android.graphics.Canvas(bitmap)
            v.draw(canvas)
            canvas.drawRect(0f, (v.height - px).toFloat(), v.width.toFloat(), v.height.toFloat(),
                android.graphics.Paint().apply { color = android.graphics.Color.rgb(120, 120, 128) })
            java.io.File("build/screenshots/$theme").mkdirs()
            java.io.File("build/screenshots/$theme/$name.png").outputStream().use {
                bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it)
            }
        }
    }

    private fun longChat(): List<Line> = (1..18).flatMap { i -> listOf(
        Line("you", "Step $i: check the build on the release branch and say what's slow.", uuid = "s$i"),
        Line("tool", "Bash ./gradlew assemble --profile"),
        Line("bot", "Step $i done. Configuration took most of the time: the plugins resolve their dependencies on " +
            "every run. Caching the configuration would save about a third of it."),
    ) } + Line("you", "Here's the profile from my machine.", uuid = "pic",
        files = listOf(dev.baude.android.Sent("image", "profile.png", "image/png", picture()))) +
        Line("bot", "Thanks: the same three plugins stand out there too.")

    /** A frame as a phone draws one: its time passed, then what changed laid out and drawn (the rig lays out only
     * when it's drawn). */
    private fun frame() {
        org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idleFor(java.time.Duration.ofMillis(16))
        val v = activity.window.decorView
        v.draw(android.graphics.Canvas(android.graphics.Bitmap.createBitmap(v.width, v.height, android.graphics.Bitmap.Config.ARGB_8888)))
    }

    /** A small picture, as a message's attachment has one (base64 PNG). */
    private fun picture(): String {
        val b = android.graphics.Bitmap.createBitmap(3000, 3000, android.graphics.Bitmap.Config.ARGB_8888)
        android.graphics.Canvas(b).apply { // (a phone camera's size: read a moment after its message is drawn)
            drawColor(android.graphics.Color.rgb(80, 120, 200))
            drawCircle(1500f, 1500f, 940f, android.graphics.Paint().apply { color = android.graphics.Color.rgb(240, 200, 80) })
        }
        val out = java.io.ByteArrayOutputStream()
        b.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, out)
        return java.util.Base64.getEncoder().encodeToString(out.toByteArray())
    }

    /** Where the words text are on screen (the first that has them), as Compose's semantics have them. */
    private fun where(text: String): android.graphics.RectF {
        fun roots(v: android.view.View): List<androidx.compose.ui.node.RootForTest> = when (v) {
            is androidx.compose.ui.node.RootForTest -> listOf(v)
            is android.view.ViewGroup -> (0 until v.childCount).flatMap { roots(v.getChildAt(it)) }
            else -> emptyList()
        }
        fun all(n: androidx.compose.ui.semantics.SemanticsNode): List<androidx.compose.ui.semantics.SemanticsNode> =
            listOf(n) + n.children.flatMap { all(it) }
        val node = roots(activity.window.decorView).flatMap { all(it.semanticsOwner.unmergedRootSemanticsNode) }.first { n ->
            n.config.getOrElse(androidx.compose.ui.semantics.SemanticsProperties.Text) { emptyList() }.any { text in it.text }
        }
        val b = node.boundsInWindow
        return android.graphics.RectF(b.left, b.top, b.right, b.bottom)
    }

    /** A finger put down on the words text and lifted (held: for a long press). */
    private fun press(text: String, held: Boolean = false) {
        val r = where(text)
        val root = activity.window.decorView
        val t = android.os.SystemClock.uptimeMillis()
        root.dispatchTouchEvent(android.view.MotionEvent.obtain(t, t, android.view.MotionEvent.ACTION_DOWN, r.centerX(), r.centerY(), 0))
        org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idleFor(java.time.Duration.ofMillis(if (held) 700 else 60))
        val u = android.os.SystemClock.uptimeMillis()
        root.dispatchTouchEvent(android.view.MotionEvent.obtain(t, u, android.view.MotionEvent.ACTION_UP, r.centerX(), r.centerY(), 0))
        settle()
    }

    /** A finger dragged down the middle of the screen, from and to fractions of its height, then let go still. */
    private fun drag(from: Float, to: Float) {
        val root = activity.window.decorView
        val x = root.width / 2f
        val t = android.os.SystemClock.uptimeMillis()
        val looper = org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper())
        root.dispatchTouchEvent(android.view.MotionEvent.obtain(t, t, android.view.MotionEvent.ACTION_DOWN, x, root.height * from, 0))
        for (k in 1..20) {
            looper.idleFor(java.time.Duration.ofMillis(16))
            val y = root.height * (from + (to - from) * k / 20f)
            root.dispatchTouchEvent(android.view.MotionEvent.obtain(t, android.os.SystemClock.uptimeMillis(),
                android.view.MotionEvent.ACTION_MOVE, x, y, 0))
        }
        looper.idleFor(java.time.Duration.ofMillis(300))
        root.dispatchTouchEvent(android.view.MotionEvent.obtain(t, android.os.SystemClock.uptimeMillis(),
            android.view.MotionEvent.ACTION_UP, x, root.height * to, 0))
        settle()
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

    /** What on screen has a long-press labelled label (its OnLongClick's), long-pressed. */
    private fun longPress(label: String) {
        fun roots(v: android.view.View): List<androidx.compose.ui.node.RootForTest> = when (v) {
            is androidx.compose.ui.node.RootForTest -> listOf(v)
            is android.view.ViewGroup -> (0 until v.childCount).flatMap { roots(v.getChildAt(it)) }
            else -> emptyList()
        }
        fun all(n: androidx.compose.ui.semantics.SemanticsNode): List<androidx.compose.ui.semantics.SemanticsNode> =
            listOf(n) + n.children.flatMap { all(it) }
        val act = androidx.compose.ui.semantics.SemanticsActions.OnLongClick
        windows().flatMap(::roots).flatMap { all(it.semanticsOwner.rootSemanticsNode) }
            .first { it.config.contains(act) && it.config[act].label == label }.config[act].action!!.invoke()
        androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
        settle()
    }

    /** typed put in the box on screen (its dialog's, if one's up), as typing it would. */
    private fun typeIn(typed: String) {
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
        val box = views.drop(views.indexOf(activity.window.decorView)).flatMap(::roots)
            .flatMap { all(it.semanticsOwner.rootSemanticsNode) }
            .last { it.config.contains(androidx.compose.ui.semantics.SemanticsActions.SetText) }
        box.config[androidx.compose.ui.semantics.SemanticsActions.SetText].action!!.invoke(androidx.compose.ui.text.AnnotatedString(typed))
        androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
        settle()
    }

    private var insets: androidx.core.graphics.Insets? = null

    /** Meta's form for starting a chat, empty, as its card in Meta's conversation draws it. */
    private fun spawnForm() {
        dev.baude.android.MetaTools.forms["f1"] = org.json.JSONObject().put("id", "f1").put("form", "spawn_agent")
            .put("values", org.json.JSONObject()).put("state", "open")
        show { androidx.compose.foundation.layout.Column(androidx.compose.ui.Modifier.padding(12.dp)) { dev.baude.android.MetaFormCard("f1") } }
    }

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

/** A host's ed25519 key, as ssh sends it: string "ssh-ed25519", string its 32 bytes. */
private val HOST_KEY = byteArrayOf(0, 0, 0, 11) + "ssh-ed25519".toByteArray() + byteArrayOf(0, 0, 0, 32) +
    ByteArray(32) { (it * 37 + 11).toByte() }

/** Claude accounts on workshop (Settings → Subscriptions). */
private val WORK = org.json.JSONObject().put("id", "w1").put("provider", "claude").put("name", "Work").put("kind", "folder")
private val HOME_ACCOUNT = org.json.JSONObject().put("id", "h2").put("provider", "claude").put("name", "Home").put("kind", "folder")

/** What claude says when its sign-in has run out and can't be renewed. */
private const val EXPIRED = "Failed to authenticate: this login reached its time limit and cannot be renewed. Run " +
    "`claude auth login` to sign in again, or use `claude setup-token` and set CLAUDE_CODE_OAUTH_TOKEN for unattended jobs"

@RunWith(RobolectricTestRunner::class)
@Config(qualifiers = "w411dp-h891dp-notnight-xxhdpi")
class LightScreens : Screens("light")

@RunWith(RobolectricTestRunner::class)
@Config(qualifiers = "w411dp-h891dp-night-xxhdpi")
class DarkScreens : Screens("dark")

