package dev.baude.android.screenshots

import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.requiredHeight
import androidx.compose.foundation.layout.wrapContentHeight
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
        Settings.theme.value = null
        Settings.themeColours.value = null
        dev.baude.android.Themes.listed.value = emptyList()
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
        dev.baude.android.HomePrefs.groups.clear()
        dev.baude.android.HomePrefs.noGroups.value = false
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

    // (Settings swiped from General toward Subscriptions, the finger still down halfway, then let go: the page it
    // settles on, the bar's raised page following)
    @Test fun settingsSwipe() {
        accountsFixture()
        show { SettingsScreen({}, {}) }
        laidOut()
        val lift = swipe(0.9f, 0.3f)
        screen("05g-settings-mid-swipe")
        lift()
        laidOut()
        screen("05h-settings-swiped")
    }

    // (Settings → Subscriptions on workshop, the host picked, which has claude: Claude's Work and Home, folders signed
    // in to with claude auth login, Work the default, each card's usage read as the page is shown; Codex's Personal;
    // then the Current Subscription row's menu, an account's ⋮, Add account (the sign-in, its page's link and the code box) and Remove…)
    @Test fun subscriptions() {
        accountsFixture()
        show { SettingsScreen({}, {}, page = 1) }
        laidOut()
        screen("05a-subscriptions")
    }

    // (Settings → Subscriptions opened while each card's usage read is still out: what workshop last read of each, greyed)
    @Test fun subscriptionsLastRead() {
        accountsFixture(reading = true)
        show { SettingsScreen({}, {}, page = 1) }
        laidOut()
        screen("05j-subscriptions-last-read")
    }

    @Test fun subscriptionsPill() {
        accountsFixture()
        show { SettingsScreen({}, {}, page = 1) }
        laidOut()
        tap("Current Subscription")
        laidOut()
        withMenus("05f-subscriptions-pill")
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

    // (Settings → Subscriptions on a host without codex: Codex's section scrolled to, its Dependencies row in Add account's place)
    @Test fun subscriptionsNoCodex() {
        noCodexFixture()
        show { SettingsScreen({}, {}, page = 1) }
        laidOut()
        scrollUnder("Personal")
        laidOut()
        screen("05i-subscriptions-no-codex")
    }

    @Test fun subscriptionsToken() {
        tokenAccountsFixture()
        show { SettingsScreen({}, {}, page = 1) }
        laidOut()
        tap("Change this account")
        tap("Replace Token…")
        screen("05c-subscriptions-token")
    }

    // (a host without kubectl or Docker, its own commands for them (a Debian host's): what Kubernetes and Database
    // Schemas show in their place, and Settings → Dependencies, the host's list under what needs each: Ollama running,
    // gh signed out, a kubeconfig with no context)
    @Test fun missingKubectl() {
        missingFixture()
        show { dev.baude.android.KubeScreen(Mesh.hosts.first().id, {}) }
        laidOut()
        screen("40a-missing-kubectl")
    }

    @Test fun missingDocker() {
        missingFixture()
        show { dev.baude.android.DatabaseScreen(Mesh.hosts.first().id, {}) }
        laidOut()
        screen("40b-missing-docker")
    }

    // (☰ → Docker on workshop: colima's running containers (a Compose project, shop, and a redis), a row inspected as
    // YAML, the categories, and Remove… asked first)
    @Test fun docker() {
        dockerFixture()
        show { dev.baude.android.DockerScreen(Mesh.hosts.first().id, {}) }
        laidOut()
        screen("41a-docker")
    }

    @Test fun dockerInspect() {
        dockerFixture()
        show { dev.baude.android.DockerScreen(Mesh.hosts.first().id, {}) }
        laidOut()
        tap("shop-api-1")
        laidOut()
        screen("41b-docker-inspect")
    }

    @Test fun dockerCategories() {
        dockerFixture()
        show { dev.baude.android.DockerScreen(Mesh.hosts.first().id, {}) }
        laidOut()
        tap(dev.baude.android.Wording.Browser.back)
        laidOut()
        screen("41c-docker-categories")
    }

    @Test fun dockerRemove() {
        dockerFixture()
        show { dev.baude.android.DockerScreen(Mesh.hosts.first().id, {}) }
        laidOut()
        tap(dev.baude.android.Wording.Common.removeEllipsis)
        laidOut()
        tap(dev.baude.android.Wording.Common.removeEllipsis, nth = 4)
        laidOut()
        screen("41d-docker-remove")
    }

    @Test fun dependencies() {
        missingFixture()
        show { SettingsScreen({}, {}, page = 2) }
        laidOut()
        screen("40c-dependencies")
        scrollUnder("Claude Code") // (its end: Multiplex, Emission, Signal)
        laidOut()
        screen("40c2-dependencies-end")
    }

    // (Settings → Telemetry on a host: its turns' tokens by day over a year, the models used, its machine and its
    // processes now and line profiling, as it answers telemetry_read; made-up numbers, the same every run)
    @Test fun telemetry() {
        telemetryFixture()
        show { SettingsScreen({}, {}, page = 3) }
        laidOut()
        screen("40d-telemetry")
    }

    // (the same page's foot: its machine and its processes now, line profiling and its top lines)
    @Test fun telemetryFoot() {
        telemetryFixture()
        show {
            androidx.compose.foundation.layout.Box(androidx.compose.ui.Modifier.fillMaxSize()
                .wrapContentHeight(androidx.compose.ui.Alignment.Bottom, unbounded = true).requiredHeight(1500.dp)) {
                dev.baude.android.TelemetryPage()
            }
        }
        laidOut()
        screen("40e-telemetry-foot")
    }

    // (the foot on a host without line_profiler: what's missing and how it's installed, in Line Profiling's place)
    @Test fun telemetryNoProfiler() {
        telemetryFixture(profiler = false)
        show {
            androidx.compose.foundation.layout.Box(androidx.compose.ui.Modifier.fillMaxSize()
                .wrapContentHeight(androidx.compose.ui.Alignment.Bottom, unbounded = true).requiredHeight(1500.dp)) {
                dev.baude.android.TelemetryPage()
            }
        }
        laidOut()
        screen("40f-telemetry-no-profiler")
    }

    // (Settings → Access on a host: each capability's rule, Chosen Members with a switch for each member it knows of)
    @Test fun access() {
        accessFixture()
        show { SettingsScreen({}, {}, page = 4) }
        laidOut()
        screen("40g-access")
    }

    // (a chat's Access…: its own rules for managing it, Read its own, Write the host's)
    @Test fun chatAccess() {
        accessFixture()
        show { dev.baude.android.ChatAccess(Item("h1", Mesh.sessions.getValue("h1")[1], 0)) {} }
        laidOut()
        screen("40h-chat-access")
    }

    // (a capability's members opened: a check each, Everybody over them)
    @Test fun accessOpen() {
        accessFixture()
        show { SettingsScreen({}, {}, page = 4) }
        laidOut()
        tap(dev.baude.android.PhoneAccess.named("model_files"))
        layOut()
        screen("40g-access-open")
    }

    // (a chat's own Read (pixel-9), its Write the host's, the host's Write widened to everybody: Write opened, as
    // it's checked, pixel-9 only)
    @Test fun chatAccessWriteWidened() {
        accessFixture(write = "everybody")
        show { dev.baude.android.ChatAccess(Item("h1", Mesh.sessions.getValue("h1")[1], 0)) {} }
        laidOut()
        tap(dev.baude.android.Wording.Access.sameAsHost)
        layOut()
        screen("40m-chat-access-write-widened")
    }

    // (a chat's Read opened: Same as Host over its members)
    @Test fun chatAccessOpen() {
        accessFixture()
        show { dev.baude.android.ChatAccess(Item("h1", Mesh.sessions.getValue("h1")[1], 0)) {} }
        laidOut()
        tap("pixel-9")
        layOut()
        screen("40h-chat-access-open")
    }

    // (Meta's row for a capability of the host it runs on, as show setting:access_providers draws it under a reply)
    @Test fun metaAccess() {
        accessFixture()
        dev.baude.android.Meta.host.value = Mesh.hosts.first().id
        show { dev.baude.android.MetaBoxed(listOf(Unit)) { dev.baude.android.MetaSet.ALL.getValue("access_providers").row(null, null) } }
        laidOut()
        screen("40i-meta-access")
    }

    // (Meta's row for one side of a split capability, as show setting:access_kube_write draws it: Read and Write both)
    @Test fun metaAccessSplit() {
        accessFixture()
        dev.baude.android.Meta.host.value = Mesh.hosts.first().id
        show { dev.baude.android.MetaBoxed(listOf(Unit)) { dev.baude.android.MetaSet.ALL.getValue("access_kube_write").row(null, null) } }
        laidOut()
        screen("40l-meta-access-split")
    }

    // (Meta's set_setting narrowing one host's rule, of two: that host's row alone under the reply)
    @Test fun metaAccessSet() {
        accessFixture(spawn = "nobody")
        val studio = Host(id = "7d3f2a90-1c4e-4b6a-9e8d-5f0a2b3c4d5e", name = "studio", user = "dev", address = "studio.example")
        Mesh.hosts.add(studio)
        accessFixture(studio, "studio")
        dev.baude.android.Meta.host.value = Mesh.hosts.first().id
        val spec = org.json.JSONObject().put("setting", "access_spawn")
            .put("hosts", org.json.JSONArray(listOf(Mesh.hosts.first().id)))
        show { dev.baude.android.MetaSettingBox(dev.baude.android.MetaSet.ALL.getValue("access_spawn"), emptyList(), spec) }
        laidOut()
        screen("40j-meta-access-set")
    }

    // (Meta's value naming a host whose rule isn't known yet, "studio: unknown": that host's row, not an empty box)
    @Test fun metaAccessUnknown() {
        val studio = Host(id = "7d3f2a90-1c4e-4b6a-9e8d-5f0a2b3c4d5e", name = "studio", user = "dev", address = "studio.example")
        Mesh.hosts.add(studio)
        accessFixture(studio, "studio")
        dev.baude.android.Meta.host.value = Mesh.hosts.first().id
        val s = dev.baude.android.MetaSet.ALL.getValue("access_spawn")
        val spec = org.json.JSONObject().put("setting", "access_spawn")
            .put("hosts", org.json.JSONArray(s.changed?.invoke("studio: unknown").orEmpty()))
        show { dev.baude.android.MetaSettingBox(s, emptyList(), spec) }
        laidOut()
        screen("40k-meta-access-unknown")
    }

    /** A host answering access and access_set as one with members studio and pixel-9 would. */
    private fun accessFixture(host: Host = Mesh.hosts.first(), name: String = "workshop", spawn: String = "everybody",
                              write: String = "nobody") {
        val studio = "5b168c62-bc6e-4150-8d6f-de1c2fade4f3"
        val pixel = "9c0e7a11-4f2d-4b8e-a3c5-2d7e9f1b6a40"
        fun rule(who: String, vararg m: String) = org.json.JSONObject().put("who", who)
            .apply { if (who == "chosen") put("members", org.json.JSONArray(m.toList())) }
        val rules = org.json.JSONObject().put("shell", rule("nobody")).put("browser_read", rule("chosen", studio))
            .put("browser_write", rule("nobody")).put("providers", rule("everybody"))
            .put("model_files", rule("chosen", studio, pixel)).put("spawn", rule(spawn))
            .put("manage_read", rule(if (write == "everybody") "everybody" else "chosen", studio)).put("manage_write", rule(write)).put("kube_read", rule("everybody"))
            .put("kube_write", rule("nobody")).put("databases", rule("everybody")).put("subscriptions_read", rule("everybody"))
            .put("subscriptions_write", rule("everybody")).put("access_read", rule("everybody")).put("access_write", rule("nobody"))
        val state = org.json.JSONObject().put("host", name).put("rules", rules).put("editable", true)
            .put("members", org.json.JSONArray(listOf(org.json.JSONObject().put("id", pixel).put("name", "pixel-9"),
                org.json.JSONObject().put("id", studio).put("name", "studio"))))
        FixtureConnection.up(activity, host) { m ->
            if (m.optString("op") != "access") return@up null
            org.json.JSONObject().put("op", "access").put("req", m.opt("req")).put("state", state)
                .apply { if (m.has("session")) put("session", m.get("session"))
                    .put("chat", org.json.JSONObject().put("manage_read", rule("chosen", pixel))) }
        }
    }

    private fun telemetryFixture(profiler: Boolean = true) {
        FixtureConnection.up(activity, Mesh.hosts.first()) { m ->
            if (m.optString("op") == "deps") return@up org.json.JSONObject().put("op", "deps").put("req", m.opt("req"))
                .put("deps", org.json.JSONArray(listOf(org.json.JSONObject().put("dep", "line_profiler").put("name", "line_profiler")
                    .put("where", "host").put("present", false).put("fix", "sudo apt install python3-line-profiler"))))
            if (m.optString("op") != "telemetry_read") return@up null
            val now = System.currentTimeMillis() / 1000.0
            val rng = java.util.Random(7)
            val state = org.json.JSONObject().put("on", true).put("line_profiler", profiler).put("profiling", org.json.JSONArray(listOf(
                org.json.JSONObject().put("app", "host").put("functions", org.json.JSONArray(listOf("baudelib.telemetry.Telemetry.sample"))))))
            val out = org.json.JSONObject().put("op", "telemetry_read").put("req", m.opt("req")).put("host", Mesh.hosts.first().id)
                .put("state", state)
            when (m.optJSONArray("what")?.optString(0)) {
                "usage" -> {
                    val today = java.time.LocalDate.now()
                    val days = (0 until 370).filter { rng.nextDouble() < if (it > 120) 0.35 else 0.75 }.reversed().map {
                        org.json.JSONObject().put("day", today.minusDays(it.toLong()).toString())
                            .put("tokens", 50_000L + rng.nextInt(4_000_000)).put("turns", 1 + rng.nextInt(9)) }
                    fun model(name: String, effort: String?, turns: Int, tokens: Long) = org.json.JSONObject().put("model", name)
                        .put("effort", effort ?: org.json.JSONObject.NULL).put("turns", turns).put("tokens", tokens)
                    out.put("usage", org.json.JSONObject().put("days", org.json.JSONArray(days))
                        .put("models", org.json.JSONArray(listOf(model("claude-opus-4-7", null, 600, 280_800_000),
                            model("claude-sonnet-4-6", null, 222, 99_100_000), model("gpt-5.4-codex", null, 70, 33_500_000))))
                        .put("combos", org.json.JSONArray(listOf(model("claude-opus-4-7", "high", 451, 210_000_000)))))
                }
                "events" -> out.put("events", org.json.JSONArray(listOf("chat_opened", "chat_front", "turn_sent", "turn_ended",
                    "chat_left").mapIndexed { i, k -> org.json.JSONObject().put("at", now - 900 + i * 60).put("app", "baude")
                        .put("kind", k).put("host", Mesh.hosts.first().id).put("session", "c1a7e4f0-0000-4000-8000-000000000001")
                        .apply { if (k == "turn_ended") put("seconds", 131.4) } }))
                "metrics" -> out.put("metrics", org.json.JSONArray(listOf(org.json.JSONObject().put("at", now)
                    .put("load", org.json.JSONArray(listOf(2.41, 2.10, 1.98))).put("memory_total", 17_179_869_184L)
                    .put("memory_used", 11_811_160_064L)
                    .put("swap_total", 3_221_225_472L).put("swap_used", 1_288_490_188L).put("memory_pressure", "normal")
                    .put("memory_stall", org.json.JSONObject.NULL).put("procs", org.json.JSONArray(listOf(
                        org.json.JSONObject().put("app", "host").put("pid", 101).put("cpu", 1.4).put("rss", 148_897_792L),
                        org.json.JSONObject().put("app", "baude").put("pid", 102).put("cpu", 6.2).put("rss", 642_777_088L),
                        org.json.JSONObject().put("app", "record").put("pid", 103).put("cpu", 0.3).put("rss", 231_735_296L),
                        org.json.JSONObject().put("app", "emission").put("pid", 104).put("cpu", 3.8).put("rss", 412_090_368L),
                        org.json.JSONObject().put("app", "emission").put("pid", 106).put("cpu", 1.1).put("rss", 389_021_696L),
                        org.json.JSONObject().put("app", "multiplex").put("pid", 105).put("cpu", 0.6).put("rss", 98_566_144L)))))))
                "profiles" -> out.put("profiles", org.json.JSONArray(listOf(org.json.JSONObject().put("at", now - 30)
                    .put("from", now - 60).put("app", "host").put("lines", org.json.JSONArray(listOf(
                        org.json.JSONObject().put("file", "baudelib/telemetry.py").put("line", 271).put("function", "sample")
                            .put("code", "stats = proc_stats(sorted(pids))").put("hits", 120).put("seconds", 0.912),
                        org.json.JSONObject().put("file", "baudelib/telemetry.py").put("line", 131).put("function", "_mac_memory")
                            .put("code", "if libc.host_statistics64(host, 4, ctypes.byref(vm), ctypes.byref(count)) == 0:")
                            .put("hits", 120).put("seconds", 0.402)))))))
            }
            out
        }
    }

    private fun dockerFixture() {
        fun j(vararg kv: Pair<String, Any?>) = org.json.JSONObject().apply { for ((k, v) in kv) put(k, v ?: org.json.JSONObject.NULL) }
        fun a(vararg v: Any?) = org.json.JSONArray(v.toList())
        val shell = a("docker", "--context", "colima", "exec", "-it", "x", "sh")
        fun ctr(name: String, image: String, status: String, tone: String, ports: String, project: String, age: String, secs: Int,
                running: Boolean = true) = j("id" to name, "name" to name, "cells" to a(name, image, status, ports, project, age),
            "keys" to a(name, image, status, ports, project, secs), "tones" to a("", "", tone, "", "", ""), "tips" to a("", "", "", "", "", ""),
            "actions" to (if (running) a("stop", "restart", "remove") else a("start", "remove")), "logs" to true,
            "shell" to if (running) shell else a(), "running" to running)
        val rows = a(ctr("shop-api-1", "shop/api:1.4", "Up 2 hours", "ok", "0.0.0.0:8080->80/tcp", "shop", "2h05m", 7500),
            ctr("shop-db-1", "postgres:16", "Up 2 hours (healthy)", "ok", "5432/tcp", "shop", "2h06m", 7560),
            ctr("shop-worker-1", "shop/worker:1.4", "Restarting (1) 4 seconds ago", "warn", "", "shop", "2h05m", 7500),
            ctr("cache", "redis:7", "Up 3 days", "ok", "6379/tcp", "", "3d", 264000))
        fun page(t: String, k: String, w: String) = j("title" to t, "key" to k, "what" to w)
        val sections = a(j("title" to "Containers", "icon" to "baude-kube-workloads-symbolic",
                "pages" to a(page("Running", "running", "running containers"), page("All", "containers", "containers"))),
            j("title" to "Images", "icon" to "package-x-generic-symbolic", "pages" to a(page("Images", "images", "images"))),
            j("title" to "Volumes", "icon" to "baude-kube-storage-symbolic", "pages" to a(page("Volumes", "volumes", "volumes"))),
            j("title" to "Networks", "icon" to "baude-kube-network-symbolic", "pages" to a(page("Networks", "networks", "networks"))),
            j("title" to "Compose Projects", "icon" to "view-grid-symbolic", "pages" to a(page("Compose Projects", "compose", "Compose projects"))))
        val cols = a(j("title" to "Name", "kind" to "name"), j("title" to "Image"), j("title" to "Status", "kind" to "status"),
            j("title" to "Ports"), j("title" to "Project"), j("title" to "Age", "kind" to "age"))
        val yaml = "- Id: 3f9c2a1b7d4e8f60\n  Name: /shop-api-1\n  State:\n    Status: running\n    Running: true\n" +
            "  Config:\n    Image: shop/api:1.4\n    Env:\n      - \"PORT=80\"\n    Labels:\n      com.docker.compose.project: shop\n"
        FixtureConnection.up(activity, Mesh.hosts.first()) { m ->
            if (m.optString("op") != "docker") null
            else org.json.JSONObject().put("op", "docker").put("req", m.opt("req")).put("what", m.optString("what")).apply {
                when (m.optString("what")) {
                    "contexts" -> put("contexts", a(j("name" to "colima", "current" to true), j("name" to "default", "current" to false)))
                        .put("sections", sections)
                    "list" -> put("columns", cols).put("rows", rows)
                    "inspect" -> put("text", yaml).put("file", "inspect.yaml")
                }
            }
        }
        Mesh.app.getSharedPreferences("settings", android.content.Context.MODE_PRIVATE).edit().putString("docker_inspect", "yaml").apply()
    }

    private fun missingFixture() {
        fun dep(key: String, name: String, present: Boolean, fix: String?, group: String? = null,
                ready: String = dev.baude.android.Wording.Deps.installed) = org.json.JSONObject().put("dep", key).put("name", name)
            .put("where", "host").put("present", present).put("fix", fix ?: org.json.JSONObject.NULL).put("group", group ?: org.json.JSONObject.NULL)
            .put("ready", if (present) ready else org.json.JSONObject.NULL)
        val kubectl = dep("kubectl", "kubectl", false, "sudo snap install kubectl --classic")
        val docker = dep("docker", "Docker", false, "sudo apt install docker.io")
        FixtureConnection.up(activity, Mesh.hosts.first()) { m ->
            val r = org.json.JSONObject().put("op", m.optString("op")).put("req", m.opt("req"))
            when (m.optString("op")) {
                "kube" -> r.put("what", m.optString("what")).put("missing", kubectl)
                "databases" -> r.put("what", m.optString("what")).put("missing", docker)
                "deps" -> r.put("deps", org.json.JSONArray(listOf(
                    dep("claude", "Claude Code", true, "curl -fsSL https://claude.ai/install.sh | bash", "deps.providers"),
                    dep("codex", "Codex CLI", false, "npm install -g @openai/codex", "deps.providers"),
                    dep("ollama", "Ollama", true, "curl -fsSL https://ollama.com/install.sh | sh", "deps.providers",
                        dev.baude.android.Wording.Deps.runningModels(n = 2)),
                    dep("git", "Git", true, "sudo apt install git", "deps.git_github"),
                    dep("gh", "GitHub CLI", true, "sudo apt install gh", "deps.git_github"),
                    dep("gh_auth", "GitHub Account", false, "gh auth login", "deps.git_github").put("check", true),
                    dep("kubectl", "kubectl", true, "sudo snap install kubectl --classic", "window.kubernetes"),
                    dep("kubeconfig", "kubeconfig", false, null, "window.kubernetes").put("check", true)
                        .put("link", "https://kubernetes.io/docs/concepts/configuration/organize-cluster-access-kubeconfig/"),
                    docker.put("group", "window.database_schemas"),
                    dep("vte", "VTE", true, "sudo apt install gir1.2-vte-3.91", "deps.multiplex").put("where", "app"),
                    dep("xvfb", "Xvfb", true, "sudo apt install xvfb", "deps.multiplex"),
                    dep("webkit", "WebKitGTK", true, "sudo apt install gir1.2-webkit-6.0", "mesh_versions.emission").put("where", "app"),
                    dep("node", "Node.js", true, "sudo apt install nodejs npm", "side_panel.signal").put("where", "app"))))
                else -> null
            }
        }
    }

    /** workshop's accounts; reading: each usage read left unanswered, the host's last reading of each in every answer. */
    private fun accountsFixture(reading: Boolean = false) {
        FixtureConnection.up(activity, Mesh.hosts.first()) { m ->
            if (m.optString("op") != "accounts" || (reading && m.optString("what") == "usage")) null
            else org.json.JSONObject().put("op", "accounts").put("req", m.opt("req")).put("what", m.optString("what"))
                .put("accounts", org.json.JSONArray(listOf(HOME_ACCOUNT, WORK,
                    org.json.JSONObject().put("id", "p1").put("provider", "codex").put("name", "Personal").put("kind", "profile"))))
                .put("defaults", org.json.JSONObject().put("claude", "w1").put("codex", "")).put("claude_cli", true)
                .apply {
                    fun limits(id: String): org.json.JSONArray {
                        val (five, seven) = mapOf("w1" to (25 to 40), "h2" to (72 to 18))[id] ?: (5 to 12)
                        return org.json.JSONArray(listOf(
                            org.json.JSONObject().put("name", "Five hour").put("key", "five_hour").put("percent", five)
                                .put("resets", System.currentTimeMillis() / 1000 + 3 * 3600 + 720),
                            org.json.JSONObject().put("name", "Seven day").put("key", "seven_day").put("percent", seven)
                                .put("resets", System.currentTimeMillis() / 1000 + 3 * 86400 + 5 * 3600)))
                    }
                    if (m.optString("what") == "usage") put("usage", limits(m.optString("id")))
                    if (reading) put("last_usage", org.json.JSONObject().apply {
                        for (id in listOf("", "w1", "h2")) put(id, org.json.JSONObject().put("usage", limits(id))
                            .put("read", System.currentTimeMillis() / 1000 - 600))
                    })
                }
        }
    }

    private fun noCodexFixture() {
        FixtureConnection.up(activity, Mesh.hosts.first()) { m ->
            if (m.optString("op") != "accounts") null
            else org.json.JSONObject().put("op", "accounts").put("req", m.opt("req")).put("what", m.optString("what"))
                .put("accounts", org.json.JSONArray(listOf(HOME_ACCOUNT, WORK,
                    org.json.JSONObject().put("id", "p1").put("provider", "codex").put("name", "Personal").put("kind", "profile"))))
                .put("defaults", org.json.JSONObject().put("claude", "w1").put("codex", "")).put("claude_cli", true)
                .put("missing", org.json.JSONObject().put("codex", org.json.JSONObject().put("dep", "codex").put("name", "Codex CLI")
                    .put("where", "host").put("present", false).put("fix", "npm install -g @openai/codex").put("group", "deps.providers")
                    .put("link", "https://github.com/openai/codex").put("check", false).put("ready", org.json.JSONObject.NULL)))
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

    /** The Git page on the chat's own branch, its pull request open (gh signed in): Auto-fix's toggle under it, off,
     * then on as its host lists it. */
    @Test fun gitAutofixOff() = gitAutofix("48-git-autofix-off", false)
    @Test fun gitAutofixOn() = gitAutofix("49-git-autofix-on", true)

    private fun gitAutofix(name: String, on: Boolean) {
        val fixtures = java.io.File("../../tests/parity/fixtures")
        val home = org.json.JSONObject(java.io.File(fixtures, "sessions.json").readText()).getJSONObject("host").getString("home")
        val answers = org.json.JSONObject(java.io.File(fixtures, "git-answers.json").readText()).getJSONObject("answers")
        val branch = "fix/upload-retry"
        FixtureConnection.up(activity, Mesh.hosts.first(), home) { msg ->
            val reply = org.json.JSONObject().put("op", "git").put("req", msg.opt("req")).put("what", msg.optString("what"))
            if (msg.optString("op") != "git") null else when (msg.optString("what")) {
                "page" -> org.json.JSONObject(answers.getJSONObject("page").toString().replace("@HOME@", home))
                    .put("op", "git").put("req", msg.opt("req")).put("picked", org.json.JSONArray().put(branch))
                    .put("branch", org.json.JSONObject().put("name", branch).put("upstream", "origin/$branch")
                        .put("ahead", 0).put("behind", 0).put("gone", false))
                "pulls" -> reply.put("repo", "garden-dev/garden").put("prs", org.json.JSONArray()).put("gh", true)
                    .put("signed_in", true).put("pr", org.json.JSONObject().put("number", 42).put("title", "Retry uploads that time out")
                        .put("state", "open").put("url", "https://github.com/garden-dev/garden/pull/42").put("head", org.json.JSONObject.NULL))
                "log" -> reply.put("commits", org.json.JSONArray()).put("skip", 0).put("more", false)
                else -> null
            }
        }
        val now = System.currentTimeMillis() / 1000.0
        Mesh.sessions["h1"] = listOf(Msg.Session(id = "a", title = "Tidy the release notes", cwd = "$home/garden",
            updated = now - 60, said = now - 60, created = now - 660, state = "idle", model = "opus", effort = "high", mode = "default",
            autofix = if (on) Msg.AutofixPull(repo = "garden-dev/garden", number = 42, url = "https://github.com/garden-dev/garden/pull/42",
                title = "Retry uploads that time out") else null))
        dev.baude.android.GitPages.prepare("h1", "a", listOf(branch), false)
        show { ChatShell("h1", "a", back = {}, open = { _, _ -> }, feedback = {}, first = "git") }
        val st = dev.baude.android.GitPages.of("h1", "a")
        repeat(20) { if (st.commits.isEmpty() || st.pr == null) settle() }
        repeat(3) {
            androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
            settle()
        }
        screen(name)
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

    // (themes on workshop, as its host works them out (the desktop's scenes_themes.py's three): Settings → Theme
    // under Sepia, its list open; Home under Midnight; a chat under Forest)
    @Test fun themeSettings() {
        themed("Sepia")
        show { SettingsScreen({}, {}) }
        laidOut()
        tap(dev.baude.android.Wording.SharedSettings.theme)
        laidOut()
        screen("41a-theme-settings")
    }

    @Test fun themeHome() {
        themed("Midnight")
        render("41b-theme-midnight-home") { HomeScreen() }
    }

    @Test fun themeChat() {
        themed("Forest")
        fill(Mesh.openChat("h1", "a"))
        render("41c-theme-forest-chat") { ChatShell("h1", "a", back = {}, open = { _, _ -> }, feedback = {}, first = "chat") }
    }

    // (Accent Colour under Sepia, whose accent is its own: at System, the theme's accent drawn and System's dot in it;
    // at Red, Red drawn over it)
    @Test fun themeAccentSystem() {
        themed("Sepia")
        show { SettingsScreen({}, {}) }
        laidOut()
        tap(dev.baude.android.Wording.SharedSettings.accentColour)
        laidOut()
        screen("41d-theme-accent-system")
    }

    @Test fun themeAccentPicked() {
        themed("Sepia")
        Settings.accent.value = "red"
        show { SettingsScreen({}, {}) }
        laidOut()
        tap(dev.baude.android.Wording.SharedSettings.accentColour)
        laidOut()
        screen("41e-theme-accent-picked")
    }

    private fun themed(name: String) {
        val listed = org.json.JSONArray(THEMES_JSON)
        FixtureConnection.up(activity, Mesh.hosts.first()) { m ->
            if (m.optString("op") != "themes") null
            else org.json.JSONObject().put("op", "themes").put("req", m.opt("req")).put("themes", listed)
        }
        val picked = (0 until listed.length()).map { listed.getJSONObject(it) }.first { it.getString("name") == name }
        Settings.theme.value = name
        Settings.themeColours.value = Msg.Theme.from(picked)
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

    // (Claude Desktop's chats on workshop, as its host lists them (desktop): the Desktop group, apart and first; its
    // ⋮, Desktop first among the groups; one open while Desktop's writing it: said, and no text box)
    private fun desktopChats() {
        val now = System.currentTimeMillis() / 1000.0
        fun d(id: String, title: String, cwd: String, ago: Double, fork: String? = null) = Msg.Session.from(
            Msg.Session(id = id, title = title, cwd = cwd, updated = now - ago, said = now - ago, created = now - ago - 600,
                state = "stopped", forkFrom = fork).json().put("desktop", true).put("taken_up", false))
        Mesh.sessions["h1"] = Mesh.sessions["h1"].orEmpty() + listOf(
            d("k1", "Plan the onboarding flow", "/home/dev/garden", 1_800.0),
            d("k2", "Onboarding, shorter", "/home/dev/garden", 2_400.0, fork = "k1"),
            d("k3", "Review the pricing page", "/home/dev/lantern", 90_000.0),
        )
    }

    @Test fun homeDesktop() {
        desktopChats()
        render("01d-home-desktop") { HomeScreen() }
    }

    @Test fun homeDesktopGroups() {
        desktopChats()
        show { HomeScreen() }
        tap(dev.baude.android.Wording.Sidebar.groupByTip)
        layOut()
        screen("01e-home-desktop-groups")
    }

    @Test fun homeGroupsClicked() {
        desktopChats()
        show { HomeScreen() }
        tap(dev.baude.android.Wording.Sidebar.groupByTip)
        layOut()
        screen("01f-home-groups-all")
        tap(dev.baude.android.Wording.Sidebar.desktopGroup, nth = 1) // (a group's check, every one listed; the first is
        // the list's Desktop heading)
        layOut()
        screen("01f-home-groups-one-clicked")
        tap(dev.baude.android.Wording.Sidebar.groups)
        layOut()
        screen("01f-home-groups-groups-clicked")
        tap(dev.baude.android.Wording.Sidebar.groups) // (again, every one listed)
        layOut()
        screen("01f-home-groups-groups-again")
    }

    @Test fun chatDesktopElsewhere() {
        Mesh.sessions["h1"] = Mesh.sessions["h1"].orEmpty().map {
            if (it.id == "a") Msg.Session.from(it.json().put("desktop", true).put("elsewhere", true).put("state", "stopped")) else it
        }
        fill(Mesh.openChat("h1", "a"))
        render("10e-chat-desktop-elsewhere") { ChatShell("h1", "a", back = {}, open = { _, _ -> }, feedback = {}, first = "chat") }
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
    /** An archived chat open, its ☰ open: no text box, Read-only session checked and greyed. */
    @Test fun chatArchived() {
        Mesh.sessions["h1"] = Mesh.sessions["h1"].orEmpty().map { if (it.id == "a") Msg.Session.from(it.json().put("archived", true)) else it }
        fill(Mesh.openChat("h1", "a"))
        show { ChatShell("h1", "a", back = {}, open = { _, _ -> }, feedback = {}, first = "chat") }
        tap("Menu")
        layOut()
        screen("10d-chat-archived")
    }
    /** A chat its host lets this phone only read, a permission asked in it, its ☰ open: no text box, the ask's buttons
     * greyed, Read-only session checked and greyed. */
    @Test fun chatReadOnlyMember() {
        val chat = Mesh.openChat("h1", "a")
        fill(chat)
        chat.chrome.writes.value = false
        chat.asks.add(dev.baude.android.Ask("perm-ro", "Bash", org.json.JSONObject().put("command", "make release"), null))
        show { ChatShell("h1", "a", back = {}, open = { _, _ -> }, feedback = {}, first = "chat") }
        laidOut()
        screen("10e-chat-read-only-member")
        tap("Menu")
        layOut()
        screen("10f-chat-read-only-member-menu")
    }
    /** MCP tools named: a ToolSearch loading the mesh's tools (its input open), a mesh tool's card, and a Slack
     * connector's tool asking to run, the host's info saying how its tools are named. */
    @Test fun chatMcpNames() {
        Mesh.infos["h1"] = Msg.Info.from(org.json.JSONObject().put("id", "h1").put("name", "workshop").put("tool_names",
            org.json.JSONObject().put("servers", org.json.JSONObject().put("mesh", "Mesh").put("claude_ai_Slack", "Slack"))))
        val chat = Mesh.openChat("h1", "a")
        settle()
        chat.lines.clear()
        fun call(id: String, name: String, input: org.json.JSONObject) = dev.baude.android.took(chat.calls, chat.lines,
            Line("call", org.json.JSONObject().put("id", id).put("name", name).put("input", input).toString()))
        chat.lines.add(Line("you", "Which of my tasks ran today? Post it to the team channel."))
        call("ts", "ToolSearch", org.json.JSONObject().put("query", "select:mcp__mesh__tasks,mcp__mesh__task_run").put("max_results", 2))
        call("mt", "mcp__mesh__tasks", org.json.JSONObject().put("bot", "a1b2c3"))
        val post = org.json.JSONObject().put("channel_id", "C024BE91L").put("message", "Two tasks ran today: nightly tests and the weekly report.")
        call("sl", "mcp__claude_ai_Slack__slack_send_message", post)
        chat.calls.byId.getValue("ts").result("Loaded 2 tools", false, emptyList())
        chat.calls.byId.getValue("mt").result("[]", false, emptyList())
        chat.calls.byId.getValue("ts").inputOpen = true
        chat.calls.opened["ts"] = true
        chat.asks.add(dev.baude.android.Ask("perm-sl", "mcp__claude_ai_Slack__slack_send_message", post, "sl"))
        show { ChatShell("h1", "a", back = {}, open = { _, _ -> }, feedback = {}, first = "chat") }
        laidOut()
        screen("10g-chat-mcp-names")
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

    // (a chat on Claude's model whose host keeps two Claude subscriptions, Personal (the chat's) and Work: the provider
    // picker beside the model lists them by name, pressed)
    @Test fun chatSubscriptions() {
        val chat = Mesh.openChat("h1", "a")
        fill(chat)
        val subs = listOf(Msg.Account(id = "p1", provider = "claude", name = "Personal", kind = "folder"),
            Msg.Account(id = "w1", provider = "claude", name = "Work", kind = "folder"))
        dev.baude.android.HostSubscriptions.listed["h1"] = subs
        Mesh.sessions["h1"] = Mesh.sessions["h1"].orEmpty().map {
            if (it.id == "a") Msg.Session.from(it.json().put("account", subs[0].json())) else it }
        show { ChatShell("h1", "a", back = {}, open = { _, _ -> }, feedback = {}) }
        laidOut()
        screen("20s-chat-subscriptions")
        tap("Personal")
        laidOut()
        withMenus("20t-chat-subscriptions-open")
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

    /** The long chat scrolled up to its first lines with more of its transcript before them, the host slow to send
     * it: what's before them asked for and not come yet, the host failing it (asked again a little later, the spinner
     * still up), then come and put above, what was in view left there. */
    @Test fun chatEarlier() {
        Mesh.close()
        dev.baude.android.ChatPlaces.keep("h1/b", androidx.compose.foundation.lazy.LazyListState(), true) // (none kept: opened at its end)
        val asked = ArrayList<Pair<org.json.JSONObject, (org.json.JSONObject) -> Unit>>()
        val earlier = (1..12).joinToString("") { i ->
            org.json.JSONObject().put("type", "user").put("uuid", "e$i").put("message", org.json.JSONObject().put("role", "user")
                .put("content", "Earlier question $i about the build cache.")).toString() + "\n" +
            org.json.JSONObject().put("type", "assistant").put("uuid", "r$i").put("message", org.json.JSONObject().put("role", "assistant")
                .put("content", org.json.JSONArray().put(org.json.JSONObject().put("type", "text")
                    .put("text", "Earlier answer $i: the cache key changes whenever a plugin's version does.")))).toString() + "\n"
        }.toByteArray()
        val c = FixtureConnection.up(activity, Mesh.hosts.first()) { null }
        val slow = object : dev.baude.android.Connection(activity, dev.baude.android.Place.Direct(Mesh.hosts.first()), identify = { _, _ -> },
            route = { null }, heard = { _, _ -> }, changed = {}) {
            init { state = "up"; info = c.info }
            override fun start() {}
            override fun stop() {}
            override fun send(msg: org.json.JSONObject) = true
            override fun request(msg: org.json.JSONObject, then: (org.json.JSONObject) -> Unit) { asked.add(msg to then) }
        }
        @Suppress("UNCHECKED_CAST")
        (Mesh::class.java.getDeclaredField("conns").apply { isAccessible = true }.get(Mesh) as HashMap<String, dev.baude.android.Connection>)["h1"] = slow
        val chat = Mesh.openChat("h1", "b")
        chat.lines.addAll(longChat())
        chat.start = earlier.size.toLong()
        chat.earlier.value = true
        chat.loaded.value = true
        show { ChatShell("h1", "b", back = {}, open = { _, _ -> }, feedback = {}) }
        repeat(60) { frame() }
        repeat(14) { drag(from = 0.25f, to = 0.85f) }
        repeat(30) { frame() }
        activity.window.decorView.captureRoboImage("build/screenshots/$theme/47b-chat-earlier-reading.png")
        fun earlierAsks() = asked.filter { it.first.optString("op") == "transcript" && it.first.has("limit") }
        val first = earlierAsks().size
        earlierAsks().last().second(org.json.JSONObject().put("op", "error").put("error", "not connected"))
        repeat(30) { frame() }
        activity.window.decorView.captureRoboImage("build/screenshots/$theme/47c-chat-earlier-failed.png")
        org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idleFor(java.time.Duration.ofSeconds(2))
        check(earlierAsks().size == first + 1) { "asked again after a failure: ${earlierAsks().size - first}" }
        val (msg, then) = earlierAsks().last()
        then(org.json.JSONObject().put("op", "transcript").put("offset", msg.optLong("offset"))
            .put("data", java.util.Base64.getEncoder().encodeToString(earlier)).put("next", earlier.size).put("size", earlier.size + 1000))
        repeat(30) { frame() }
        activity.window.decorView.captureRoboImage("build/screenshots/$theme/47d-chat-earlier-read.png")
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

    /** A finger dragged across the middle of the screen, from and to fractions of its width, and held there; what's
     * given back lifts it. */
    private fun swipe(from: Float, to: Float): () -> Unit {
        val root = activity.window.decorView
        val y = root.height / 2f
        val t = android.os.SystemClock.uptimeMillis()
        val looper = org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper())
        root.dispatchTouchEvent(android.view.MotionEvent.obtain(t, t, android.view.MotionEvent.ACTION_DOWN, root.width * from, y, 0))
        for (k in 1..20) {
            looper.idleFor(java.time.Duration.ofMillis(16))
            root.dispatchTouchEvent(android.view.MotionEvent.obtain(t, android.os.SystemClock.uptimeMillis(),
                android.view.MotionEvent.ACTION_MOVE, root.width * (from + (to - from) * k / 20f), y, 0))
        }
        looper.idleFor(java.time.Duration.ofMillis(300))
        laidOut()
        return {
            root.dispatchTouchEvent(android.view.MotionEvent.obtain(t, android.os.SystemClock.uptimeMillis(),
                android.view.MotionEvent.ACTION_UP, root.width * to, y, 0))
            settle()
        }
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

/** The themes workshop's host lists (scenes_themes.py's, as baudelib/themes.py works them out). */
private const val THEMES_JSON = """[{"name":"Forest","css":"","light":{"baude.frame":"#dde8d9","baude.inside":"#f6faf3","baude.raised":"#1f33200f","libadwaita.sidebar_bg_color":"#dde8d9","libadwaita.view_bg_color":"#f6faf3","libadwaita.window_bg_color":"#e7efe3","libadwaita.window_fg_color":"#1f3320","libadwaita.view_fg_color":"#1f3320","libadwaita.accent_bg_color":"#3a8a4a","libadwaita.dialog_bg_color":"#f6faf3"},"dark":{"baude.frame":"#1a2419","baude.inside":"#121a12","baude.raised":"#dbe9d60f","libadwaita.sidebar_bg_color":"#1a2419","libadwaita.view_bg_color":"#121a12","libadwaita.window_bg_color":"#162015","libadwaita.window_fg_color":"#dbe9d6","libadwaita.view_fg_color":"#dbe9d6","libadwaita.accent_bg_color":"#3f8f4f","libadwaita.dialog_bg_color":"#1a2419"}},{"name":"Midnight","css":".user-bubble { border-radius: 4px; }\n","light":{"baude.frame":"#dfe6f3","baude.inside":"#f5f8fd","baude.raised":"#1b25400f","libadwaita.sidebar_bg_color":"#dfe6f3","libadwaita.view_bg_color":"#f5f8fd","libadwaita.window_bg_color":"#e8eef8","libadwaita.window_fg_color":"#1b2540","libadwaita.view_fg_color":"#1b2540","libadwaita.accent_bg_color":"#2f6fdb","libadwaita.dialog_bg_color":"#f5f8fd"},"dark":{"baude.frame":"#141b2d","baude.inside":"#0d1220","baude.raised":"#dbe4f50f","libadwaita.sidebar_bg_color":"#141b2d","libadwaita.view_bg_color":"#0d1220","libadwaita.window_bg_color":"#111827","libadwaita.window_fg_color":"#dbe4f5","libadwaita.view_fg_color":"#dbe4f5","libadwaita.accent_bg_color":"#2a8fa6","libadwaita.dialog_bg_color":"#141b2d"}},{"name":"Sepia","css":"","light":{"baude.frame":"#ede3cf","baude.inside":"#fbf6ea","baude.raised":"#3b2f220f","libadwaita.sidebar_bg_color":"#ede3cf","libadwaita.view_bg_color":"#fbf6ea","libadwaita.window_bg_color":"#f3ead7","libadwaita.window_fg_color":"#3b2f22","libadwaita.view_fg_color":"#3b2f22","libadwaita.accent_bg_color":"#b5651d","libadwaita.dialog_bg_color":"#fbf6ea"},"dark":{"baude.frame":"#2b241c","baude.inside":"#1f1a14","baude.raised":"#efe3cc0f","libadwaita.sidebar_bg_color":"#2b241c","libadwaita.view_bg_color":"#1f1a14","libadwaita.window_bg_color":"#261f18","libadwaita.window_fg_color":"#efe3cc","libadwaita.view_fg_color":"#efe3cc","libadwaita.accent_bg_color":"#c07a30","libadwaita.dialog_bg_color":"#2b241c"}}]"""

/** What claude says when its sign-in has run out and can't be renewed. */
private const val EXPIRED = "Failed to authenticate: this login reached its time limit and cannot be renewed. Run " +
    "`claude auth login` to sign in again, or use `claude setup-token` and set CLAUDE_CODE_OAUTH_TOKEN for unattended jobs"

@RunWith(RobolectricTestRunner::class)
@Config(qualifiers = "w411dp-h891dp-notnight-xxhdpi")
class LightScreens : Screens("light")

@RunWith(RobolectricTestRunner::class)
@Config(qualifiers = "w411dp-h891dp-night-xxhdpi")
class DarkScreens : Screens("dark")

