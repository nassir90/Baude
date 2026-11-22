package dev.baude.android.screenshots

import android.view.View
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.Composable
import androidx.compose.ui.platform.ViewRootForTest
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsNode
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.state.ToggleableState
import com.github.takahirom.roborazzi.captureScreenRoboImage
import dev.baude.android.Ask
import dev.baude.android.AppVersion
import dev.baude.android.BaudeTheme
import dev.baude.android.Change
import dev.baude.android.ChangelogScreen
import dev.baude.android.ChatShell
import dev.baude.android.DatabaseScreen
import dev.baude.android.KubeScreen
import dev.baude.android.Home
import dev.baude.android.Host
import dev.baude.android.Key
import dev.baude.android.BotSettings
import dev.baude.android.FeedbackScreen
import dev.baude.android.GitAvatars
import dev.baude.android.GitPages
import dev.baude.android.FileLinks
import dev.baude.android.FilesPrefs
import dev.baude.android.Fixes
import dev.baude.android.FixesScreen
import dev.baude.android.Item
import dev.baude.android.Mesh
import dev.baude.android.Queued
import dev.baude.android.Meta
import dev.baude.android.Msg
import dev.baude.android.Settings
import dev.baude.android.SignalLinks
import dev.baude.android.SignalNames
import dev.baude.android.SettingsScreen
import dev.baude.android.Update
import dev.baude.android.Wanted
import dev.baude.android.Wording
import dev.baude.android.processesState
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.ParameterizedRobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import org.robolectric.shadows.ShadowChoreographer
import java.io.File
import java.security.KeyPairGenerator
import java.security.interfaces.ECPublicKey
import java.time.Duration

/** The phone's side of the parity rig (tests/parity/): each scene of tests/parity/scenes.json drawn from
 * tests/parity/fixtures, as tests/parity/out/phone/<theme>/<scene>.png, and what's on screen in it as
 * tests/parity/out/phone/<scene>.json, in the shape tests/parity/dump.py writes for Baude (the text, buttons, inputs,
 * switches and images a user sees, top to bottom; read from Compose's semantics). A scene the phone has no screen for
 * is written {"missing": …}. A menu or dialog in a window of its own is in the dump but not in the picture (Roborazzi's
 * screen capture leaves those windows out here, and drawing one by hand comes out empty). A render rig, not a test: nothing's checked. In android/:
 *     ./gradlew --offline testDebugUnitTest --tests '*Parity*' */
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35])
abstract class Parity(private val theme: String, private val scene: String) {
    private lateinit var activity: ComponentActivity
    private val root = File("../../tests/parity")
    private val out = File(root, "out/phone")
    private val sessions = JSONObject(File(root, "fixtures/sessions.json").readText())
    private val host = sessions.getJSONObject("host")
    private val home = host.getString("home")

    private lateinit var conn: FixtureConnection

    @Before fun up() {
        val sc = scenes().first { it.getString("name") == scene }
        files = sc.optString("files").ifBlank { null }
        gitScene = sc.optJSONObject("git")
        github = sc.optBoolean("github")
        GitAvatars.off = true
        signal = sc.optString("signal").ifBlank { null }?.let { JSONObject(File(root, "fixtures/$it").readText()).getJSONArray("refs") }
        subagents = sc.optString("subagents").ifBlank { null }
        FilesPrefs.gallery.value = false
        val opened = sessions.getJSONArray("sessions").getJSONObject(sc.optInt("session", 0))
        shown = (if (sc.has("transcript")) transcript(sc.getString("transcript"), sc.optInt("lines", -1))
            else if (opened.isNull("transcript")) standIn(opened)
            else transcript(opened.getString("transcript"), sc.optInt("lines", -1))).toByteArray()
        SignalLinks.open.value = null
        SignalNames.forget()
        freshCompose()
        ShadowChoreographer.setPaused(true)
        ShadowChoreographer.setFrameDelay(Duration.ofMillis(16))
        activity = Robolectric.buildActivity(ComponentActivity::class.java).setup().get()
        Mesh.app = activity.applicationContext
        // (the running version, as Settings' Version and the Changelog say it: the fixture's, not this build's)
        val log = JSONObject(File(root, "fixtures/changelog.json").readText())
        AppVersion.short = log.getString("commit")
        AppVersion.made = log.getLong("made")
        AppVersion.read = log.getJSONArray("commits").let { a ->
            (0 until a.length()).map { a.getJSONArray(it) }.map { Change(it.getString(0), it.getLong(1), it.getString(2)) }
        }
        Settings.load(activity)
        val set = JSONObject(File(root, "fixtures/settings.json").readText())
        Settings.accent.value = if (set.isNull("accent")) null else set.getString("accent")
        Settings.appearance.value = "system"
        Settings.textScale.value = set.optDouble("text_scale", 1.0).toFloat()
        Settings.iconScale.value = set.optDouble("icon_scale", 1.0).toFloat()
        Settings.globalScale.value = set.optDouble("zoom_default", 1.0).toFloat()
        Settings.fullWidth.value = set.optBoolean("full_width", true)
        Settings.readOnly.clear()
        Settings.metaModel.value = null
        Settings.metaEffort.value = null
        Settings.metaCommands.value = true
        Settings.codeFont.value = null
        Settings.notify.value = set.optBoolean("notifications", true)
        Settings.meteredUpdates.value = false
        Update.state.value = ""
        val pair = KeyPairGenerator.getInstance("EC").apply { initialize(256) }.generateKeyPair()
        Mesh.key.value = Key(pair.private, pair.public as ECPublicKey)
        val h = Host(id = host.getString("id"), name = host.getString("name"), user = "dev", address = "workshop.example")
        Mesh.hosts.add(h)
        Meta.host.value = h.id
        conn = FixtureConnection.up(activity, h, home) { msg -> answer(msg) }
        val now = System.currentTimeMillis() / 1000.0
        val list = sessions.getJSONArray("sessions")
        val first = list.getJSONObject(0)
        Mesh.sessions[h.id] = (0 until list.length()).map { list.getJSONObject(it) }.mapIndexed { i, s ->
            Msg.Session(id = s.getString("id"), title = s.getString("title"), cwd = "$home/${s.getString("folder")}",
                updated = now - s.getDouble("ago"), said = now - s.getDouble("ago"),
                created = now - s.getDouble("ago") - 600,
                state = (if (i == 0) sc.optString("state").ifBlank { null } else null) ?: s.getString("state"), model = s.getString("model"),
                effort = s.getString("effort"), mode = s.getString("mode"),
                size = if (s.getString("id") == opened.getString("id")) shown.size else 0)
        }
    }

    /** The scene's transcript, as the host has it: the first `lines` lines of the fixture's (all, without). */
    private var shown = ByteArray(0)

    /** The scene's `files`: a folder of fixtures whose contents join the chat's folder (as the desktop rig copies them in). */
    private var files: String? = null

    /** The scene's `git` (the garden is a repository: pick, every, branches, commit) and `github` (gh signed in). */
    private var gitScene: JSONObject? = null
    private var github = false
    private val gitAnswers by lazy { JSONObject(File(root, "fixtures/git-answers.json").readText()).getJSONObject("answers") }
    private val githubFixture by lazy { JSONObject(File(root, "fixtures/github.json").readText()) }

    /** git's answers (the garden's repository, recorded by tests/parity/gitfixture.py), with GitHub's part when gh is signed in. */
    private fun gitAnswer(msg: JSONObject, reply: (Array<out Pair<String, Any?>>) -> JSONObject): JSONObject {
        val what = msg.optString("what")
        val failed = reply(arrayOf("what" to what, "error" to Wording.HostErrors.gitFailed))
        val gh = githubFixture
        if (gitScene == null && !(github && what == "pulls")) return if (what == "pulls") reply(arrayOf("what" to what, "repo" to null, "prs" to null, "gh" to false))
            else reply(arrayOf("what" to what, "error" to Wording.HostErrors.notARepo))
        when (what) {
            "pulls" -> {
                if (!github) return reply(arrayOf("what" to what, "repo" to msg.optString("repo").ifBlank { gh.getString("repo") }, "prs" to null, "gh" to false))
                val out = reply(arrayOf("what" to what, "repo" to gh.getString("repo"), "prs" to gh.getJSONArray("prs"), "gh" to true))
                if (msg.has("head") || msg.optBoolean("people")) {
                    out.put("signed_in", true).put("avatars", JSONObject())
                    val prs = gh.getJSONArray("prs")
                    (0 until prs.length()).map { prs.getJSONObject(it) }.firstOrNull { it.optString("head") == msg.optString("head") }?.let {
                        out.put("pr", JSONObject(it.toString()).put("head", JSONObject.NULL))
                    }
                }
                return out
            }
            "page" -> {
                val key = "page" + (if (msg.optBoolean("every")) "|every" else "") +
                    (if (!msg.optBoolean("follow", true)) "|" + (0 until (msg.optJSONArray("revs")?.length() ?: 0)).joinToString(",") { msg.getJSONArray("revs").getString(it) } else "")
                val a = gitAnswers.optJSONObject(key) ?: return failed
                val out = JSONObject(a.toString().replace("@HOME@", home)).put("op", "git").put("req", msg.opt("req"))
                if (github) out.put("prs", gh.getJSONArray("prs"))
                return out
            }
            "card" -> {
                val sha = msg.optString("sha")
                val full = gitAnswers.keys().asSequence().firstOrNull { it.startsWith("card|") && it.removePrefix("card|").startsWith(sha) }
                val a = full?.let { gitAnswers.getJSONObject(it) } ?: return failed
                if (msg.optBoolean("github")) {
                    if (!github) return reply(arrayOf("what" to what, "sha" to sha))
                    val pulls = gh.getJSONArray("commit_pulls")
                    val prs = gh.getJSONArray("prs")
                    val mine = (0 until pulls.length()).map { pulls.getInt(it) }
                    return reply(arrayOf("what" to what, "sha" to sha, "login" to gh.getJSONObject("logins").optString(a.optString("email")).ifBlank { null },
                        "prs" to JSONArray((0 until prs.length()).map { prs.getJSONObject(it) }.filter { it.getInt("number") in mine })))
                }
                return JSONObject(a.toString().replace("@HOME@", home)).put("op", "git").put("req", msg.opt("req"))
            }
            "log" -> return reply(arrayOf("what" to what, "commits" to JSONArray(), "skip" to 0, "more" to false))
        }
        return failed
    }

    /** The scene's `signal`: the names the folder's Signal glossary has (signal_names answers them), none without. */
    private var signal: JSONArray? = null
    /** The scene's `subagents`: a folder of fixtures with the first session's subagents' transcripts. */
    private var subagents: String? = null

    /** What the fixture host answers, as the desktop's host (baudelib/server.py, hostfiles.py) would: the open chat's
     * transcript, and the folder's files (fixtures/garden, not a git repository, with no Signal glossary). */
    private fun answer(msg: JSONObject): JSONObject? {
        val op = msg.optString("op")
        fun reply(vararg more: Pair<String, Any?>) = JSONObject().put("op", op).put("req", msg.opt("req")).also { r ->
            more.forEach { (k, v) -> r.put(k, v) }
        }
        // (the scene's files first, then fixtures/garden: the desktop rig's folder holds both)
        fun local(path: String): File? {
            val rel = path.removePrefix("$home/garden").trimStart('/')
            if (path != "$home/garden" && !path.startsWith("$home/garden/")) return null
            val roots = listOfNotNull(files, "garden").map { File(root, "fixtures/$it") }
            val at = roots.map { if (rel.isEmpty()) it else File(it, rel) }
            return at.firstOrNull { it.exists() } ?: at.last()
        }
        if (op == "kube" || op == "databases") return hostAnswer(msg)
        when (op) {
            "transcript" -> {
                val from = msg.optLong("offset", 0).coerceIn(0, shown.size.toLong()).toInt()
                val part = shown.copyOfRange(from, shown.size)
                return reply("session" to msg.optString("session"), "offset" to from, "next" to shown.size, "size" to shown.size,
                    "data" to java.util.Base64.getEncoder().encodeToString(part))
            }
            "list_dir" -> {
                val path = msg.optString("path")
                val rel = path.removePrefix("$home/garden").trimStart('/')
                val dirs = listOfNotNull(files, "garden").map { File(root, "fixtures/$it").let { g -> if (rel.isEmpty()) g else File(g, rel) } }
                    .filter { it.isDirectory }
                if (local(path) == null || dirs.isEmpty()) return reply("op" to "dir", "path" to path, "entries" to JSONArray(),
                    "error" to "No such file or directory")
                val entries = dirs.flatMap { it.listFiles().orEmpty().toList() }.filter { it.name != ".git" }.distinctBy { it.name }
                    .sortedWith(compareBy({ !it.isDirectory }, { it.name.lowercase() }))
                return reply("op" to "dir", "path" to path, "more" to false, "entries" to JSONArray(entries.map { e ->
                    JSONObject().put("name", e.name).put("dir", e.isDirectory).put("ignored", false)
                        .apply { if (!e.isDirectory) put("size", e.length()) }
                }))
            }
            "read_file" -> {
                val path = msg.optString("path")
                val f = local(path)?.takeIf { it.isFile } ?: return reply("op" to "file", "path" to path, "error" to "No such file or directory")
                return reply("op" to "file", "path" to path, "size" to f.length(), "truncated" to false,
                    "data" to java.util.Base64.getEncoder().encodeToString(f.readBytes()))
            }
            "subagents" -> {
                val have = msg.optJSONObject("have")
                val arr = JSONArray()
                for (f in File(root, "fixtures/${subagents ?: return reply("session" to msg.optString("session"),
                    "files" to arr, "more" to false)}").listFiles().orEmpty().sortedBy { it.name }) {
                    val bytes = f.readText().replace("@CWD@", "$home/garden").toByteArray()
                    if (have?.optJSONArray(f.name)?.optLong(0) == bytes.size.toLong()) continue
                    arr.put(JSONObject().put("name", f.name).put("size", bytes.size).put("mtime", 0).put("offset", 0)
                        .put("next", bytes.size).put("data", java.util.Base64.getEncoder().encodeToString(bytes)))
                }
                return reply("session" to msg.optString("session"), "files" to arr, "more" to false)
            }
            "task_output" -> {
                val path = msg.optString("path")
                val f = File(root, "fixtures/tasks/" + path.substringAfterLast('/')).takeIf { path.startsWith("/tmp/parity/tasks/") && it.isFile }
                    ?: return reply("op" to "file", "path" to path, "error" to "No such file or directory")
                val limit = msg.optInt("limit", 4000)
                val all = f.readBytes()
                return reply("op" to "file", "path" to path, "size" to all.size, "truncated" to (all.size > limit),
                    "data" to java.util.Base64.getEncoder().encodeToString(all.takeLast(limit).toByteArray()))
            }
            "git" -> return gitAnswer(msg) { more -> reply(*more) }
            "signal_names" -> return reply("glossary" to "$home/garden/.baude", "root" to "$home/garden",
                "refs" to (signal ?: JSONArray()))
            // (what Baude answers with its own functions: tests/parity/host.py, a throwaway process for each request)
            "fixes" -> {
                val p = ProcessBuilder("python3", "-I", File(root, "host.py").path).start()
                p.outputStream.use { it.write(msg.toString().toByteArray()) }
                val out = p.inputStream.readBytes().toString(Charsets.UTF_8)
                p.waitFor()
                return JSONObject(out)
            }
        }
        return null
    }

    /** What the fixture host answers for kube and databases: tests/parity/host.py's, which reads the fixtures with the
     * host's own code (hostkube.py, hostdb.py), one process a request. */
    private fun hostAnswer(msg: JSONObject): JSONObject {
        val p = ProcessBuilder("python3", "-I", File(root, "host.py").path).start()
        p.outputStream.use { it.write(msg.toString().toByteArray()) }
        val out = p.inputStream.bufferedReader().readText()
        p.waitFor()
        return JSONObject(out)
    }

    @After fun down() {
        Fixes.items = emptyList()
        Fixes.repo = null
        Fixes.error = null
        Fixes.readAt = 0
        Fixes.lastRead = 0
        Fixes.firstRead = true
        Fixes.loading = false
        Meta.load(JSONObject())
        Meta.host.value = null
        FixtureConnection.down()
        Mesh.hosts.clear()
        Mesh.sessions.clear()
        Mesh.infos.clear()
        Mesh.states.clear()
        Mesh.key.value = null
    }

    @Test fun draw() {
        val sc = scenes().first { it.getString("name") == scene }
        val made = runCatching { draw(sc) }
        if (made.isFailure) {
            if (theme == "light") write(scene, JSONObject().put("scene", scene).put("app", "phone")
                .put("missing", "failed: ${made.exceptionOrNull()}"))
            return
        }
        if (made.getOrNull() == false) {
            if (theme == "light") write(scene, JSONObject().put("scene", scene).put("app", "phone")
                .put("missing", "no phone screen"))
            return
        }
        File(out, theme).mkdirs()
        captureScreenRoboImage(File(out, "$theme/$scene.png").path)
        if (theme == "light") write(scene, JSONObject().put("scene", scene).put("app", "phone")
            .put("size", JSONArray(listOf(411, 891))).put("items", items()))
    }

    /** The scene drawn: true, or false when the phone has no screen for it. */
    private fun draw(sc: JSONObject): Boolean {
        val name = sc.getString("name")
        val first = sessions.getJSONArray("sessions").getJSONObject(sc.optInt("session", 0))
        when {
            name.startsWith("home") -> {
                show { Home(open = { _, _ -> }, feedback = {}) }
                if (name == "home-row-menu") hold(first.getString("title"))
                if (name == "home-host-menu" || name == "home-add-host") hold(host.getString("name"))
                if (name == "home-add-host") tap(Wording.Sidebar.addHost)
            }
            name.startsWith("chat") || name.startsWith("panel") || name == "menu" -> {
                if (sc.has("meta")) {
                    Meta.load(metaState(sc.getString("meta")))
                    if (sc.optBoolean("open")) for (call in Meta.calls.values) {
                        call.inputOpen = true
                        call.outputOpen = true
                    }
                }
                if (sc.optBoolean("open")) openAll(first.getString("id"))
                if (sc.optBoolean("down")) {
                    conn.state = "down"
                    Mesh.tick.value++
                }
                if (sc.has("draft")) Mesh.openChat(host.getString("id"), first.getString("id")).draft.value = sc.getString("draft")
                if (sc.has("reveal")) FileLinks.wanted.value = Wanted(host.getString("id"), "$home/garden/${sc.getString("reveal")}", null, true)
                // (the page asks its host for the folder's entries as a link to it is followed: the same here, for the chat's own)
                if (sc.has("gallery") || sc.has("hold")) FileLinks.wanted.value = Wanted(host.getString("id"), "$home/garden", null, true)
                GitPages.prepare(host.getString("id"), first.getString("id"),
                    gitScene?.optJSONArray("pick")?.let { a -> (0 until a.length()).map { a.getString(it) } }, gitScene?.optBoolean("every") == true)
                show { ChatShell(host.getString("id"), first.getString("id"), back = {}, open = { _, _ -> }, feedback = {},
                    first = sc.optString("page").ifBlank { "chat" }) }
                gitScene?.let { g ->
                    val st = GitPages.of(host.getString("id"), first.getString("id"))
                    repeat(20) { if (st.commits.isEmpty()) settle() }
                    settle()
                    if (g.optBoolean("branches")) st.listOpen = true
                    g.optString("commit").ifBlank { null }?.let { subject ->
                        st.card = st.commits.first { it.subject == subject }.sha
                    }
                    androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
                    settle()
                }
                if (sc.has("process") || sc.has("view") || sc.has("gallery") || sc.has("hold")) flush()
                if (sc.has("running")) {
                    val tasks = sc.getJSONArray("running")
                    for (i in 0 until tasks.length()) {
                        val t = tasks.getJSONObject(i)
                        val said = JSONObject().put("op", "claude").put("session", first.getString("id"))
                            .put("msg", JSONObject(t.toString()).put("type", "system").put("subtype", "task_started"))
                        for (l in Mesh.listeners.toList()) l(host.getString("id"), said)
                    }
                    androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
                    settle()
                }
                if (sc.has("process")) {
                    val st = processesState(Mesh.open!!)
                    if (sc.optBoolean("full")) st.shown.value = sc.getString("process") else st.open[sc.getString("process")] = true
                    flush()
                }
                if (sc.has("view")) {
                    Mesh.open!!.showAgent.value = sc.getString("view")
                    flush()
                }
                if (sc.has("gallery")) {
                    tap(sc.getString("gallery"))
                    tap(Wording.CodeView.showGallery)
                    repeat(3) { flush(true) } // (the pictures are read and drawn)
                }
                if (sc.has("hold")) hold(sc.getString("hold"))
                if (name == "menu") tap(Wording.ChatHeader.menu)
                if (sc.has("card")) tap(sc.getString("card"))
                if (sc.optBoolean("signal_menu")) tapDescribed(Wording.SignalMenu.tipLinks(status = "").substringBefore(":"))
                if (sc.has("ref")) {
                    SignalLinks.tap(sc.getString("ref"))
                    androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
                    settle()
                }
                if (sc.optBoolean("open") && !sc.has("meta")) toEnd()
                if (sc.has("ask")) ask(sc.getString("ask"))
                if (sc.has("queued")) {
                    Mesh.open!!.queued.add(Queued(sc.getString("queued"), emptyList()))
                    androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
                    settle()
                    hold(sc.getString("queued"))
                }
            }
            name == "bot-settings" -> show {
                BotSettings(Item(host.getString("id"), Mesh.sessions.getValue(host.getString("id")).first(), 0)) {}
            }
            name == "feedback-ongoing" -> show { FixesScreen({}, {}, show = "12") }
            name == "feedback-ongoing-list" -> show { FixesScreen({}, {}) }
            name == "feedback" -> show { FeedbackScreen(listOf("Chat: Fix the flaky upload"), back = {}) }
            name == "settings" -> show { SettingsScreen({}, {}) }
            name == "changelog" -> show { ChangelogScreen(null, {}) }
            name == "databases" -> show { DatabaseScreen(host.getString("id"), {}) }
            name == "kubernetes" -> {
                show { KubeScreen(host.getString("id"), {}) }
                tap("CPU")
                tap("CPU")
            }
            else -> return false
        }
        return true
    }

    /** The desktop's meta store fixture (messages and actions) as the phone keeps Meta's conversation (Meta.save's). */
    private fun metaState(file: String): JSONObject {
        val src = JSONObject(File(root, "fixtures/$file").readText())
        val lines = JSONArray()
        val calls = JSONObject()
        val turns = JSONObject()
        val messages = src.getJSONArray("messages")
        var user = ""
        for (i in 0 until messages.length()) {
            val m = messages.getJSONObject(i)
            if (m.getString("role") == "user") {
                user = "u$i"
                lines.put(JSONArray(listOf("you", m.getString("text"), user)))
                turns.put(user, JSONObject().put("t", m.optDouble("t")))
                continue
            }
            turns.optJSONObject(user)?.put("reply_t", m.optDouble("t"))?.put("model", m.optString("model"))
            val items = m.getJSONArray("items")
            for (j in 0 until items.length()) {
                val it = items.getJSONObject(j)
                if (it.getString("kind") == "text") {
                    lines.put(JSONArray(listOf("bot", it.getString("text"), "")))
                    continue
                }
                val key = "c$i-$j"
                val shown = JSONArray()
                val specs = it.optJSONArray("shown")
                for (k in 0 until (specs?.length() ?: 0)) {
                    val sp = specs!!.getJSONObject(k)
                    when (sp.getString("kind")) {
                        "setting" -> shown.put(JSONObject().put("setting", sp.getString("name")).put("set", sp.get("set")))
                        "changelog" -> shown.put(JSONObject().put("changelog", true))
                    }
                }
                calls.put(key, JSONObject().put("key", key).put("id", key).put("name", it.getString("name"))
                    .put("input", it.optJSONObject("input") ?: JSONObject()).put("state", it.optString("state", "done"))
                    .put("result", it.opt("result") ?: JSONObject.NULL).put("actions", it.optJSONArray("actions") ?: JSONArray())
                    .put("shown", shown).put("ran", true))
                lines.put(JSONArray(listOf("metacall", key, "")))
            }
        }
        val actions = JSONArray()
        val list = src.getJSONArray("actions")
        for (i in 0 until list.length()) {
            val a = list.getJSONObject(i)
            val kind = a.getString("kind")
            val marks = JSONArray()
            val ms = a.optJSONArray("marks")
            for (k in 0 until (ms?.length() ?: 0)) {
                val m = ms!!.getJSONArray(k)
                marks.put(JSONArray(listOf(m.getString(0), host.getString("id") + "/" + m.getString(1), m.getString(2), m.getString(3))))
            }
            actions.put(JSONObject().put("id", a.getString("id")).put("said", a.getString("label"))
                .put("kind", if (kind == "move_chats") "move" else kind)
                .put("data", if (kind == "setting") JSONObject().put("name", a.getJSONObject("undo").getString("name"))
                    .put("from", a.getJSONObject("undo").get("value")).put("to", a.getJSONObject("redo").get("value")) else JSONObject())
                .put("turn", 1).put("undone", a.getString("state") != "done").put("kept", a.getBoolean("kept")).put("marks", marks))
        }
        return JSONObject().put("host", host.getString("id")).put("lines", lines).put("calls", calls).put("turns", turns)
            .put("actions", actions).put("started", false)
    }

    /** What the desktop rig writes for a session with no transcript of its own: its prompt, a reply, its title. */
    private fun standIn(s: JSONObject): String {
        fun rec(type: String, vararg more: Pair<String, Any>) = JSONObject().put("type", type).also { o ->
            more.forEach { (k, v) -> o.put(k, v) }
        }
        val cwd = "$home/${s.getString("folder")}"
        val id = s.getString("id")
        return listOf(
            rec("user", "cwd" to cwd, "sessionId" to id, "message" to JSONObject().put("role", "user").put("content", s.getString("title"))),
            rec("assistant", "cwd" to cwd, "sessionId" to id, "message" to JSONObject().put("role", "assistant")
                .put("content", JSONArray().put(JSONObject().put("type", "text").put("text", "On it.")))),
            rec("custom-title", "customTitle" to s.getString("title"), "sessionId" to id),
        ).joinToString("\n") { it.toString() } + "\n"
    }

    /** The permission its call asks, as claude's control_request for it (the call is the transcript's, with no result). */
    private fun ask(id: String) {
        val chat = Mesh.open!!
        if (chat.calls.byId[id] == null) settle()
        val call = chat.calls.byId.getValue(id)
        chat.asks.add(Ask("perm-$id", call.name, JSONObject(call.input.toString()), id))
        settle()
    }

    /** The chat read and every group and every call's input and output opened, before it's drawn (as desktop.py opens
     * them: all open, the chat at its end). */
    private fun openAll(first: String) {
        val chat = Mesh.openChat(host.getString("id"), first)
        settle()
        for (call in chat.calls.byId.values) {
            call.inputOpen = true
            call.outputOpen = true
            chat.calls.opened[call.id] = true
        }
    }

    /** The chat's list (the one that scrolls up and down, with items) scrolled to its end. */
    private fun toEnd() {
        val list = roots().flatMap { all(it.semanticsOwner.rootSemanticsNode) }.firstOrNull { n ->
            val c = n.config
            c.contains(SemanticsProperties.CollectionInfo) && c.contains(SemanticsProperties.VerticalScrollAxisRange) &&
                c.contains(SemanticsActions.ScrollBy)
        } ?: return
        repeat(8) {
            list.config[SemanticsActions.ScrollBy].action?.invoke(0f, 2500f)
            settle()
        }
    }

    private fun transcript(file: String, lines: Int): String {
        val all = File(root, "fixtures/$file").readLines().filter { it.isNotBlank() }
            .map { it.replace("@CWD@", "$home/garden") }
        val (title, body) = all.partition { it.contains("\"custom-title\"") }
        return ((if (lines < 0) body else body.take(lines)) + title).joinToString("\n") + "\n"
    }

    private fun show(screen: @Composable () -> Unit) {
        activity.setContent { BaudeTheme { screen() } }
        settle()
    }

    /** A click on what's labelled label (its text or its description), as a finger's would. */
    private fun tap(label: String) {
        repeat(5) { if (find(label) == null) flush(true) }
        val node = roots().flatMap { all(it.semanticsOwner.rootSemanticsNode) }.firstOrNull { n ->
            n.config.contains(SemanticsActions.OnClick) && (label in said(n) || label == described(n))
        } ?: error("nothing labelled $label")
        node.config[SemanticsActions.OnClick].action?.invoke()
        // (what the tap wrote is told to the composition now: in a run's later scenes the write observer's notice doesn't arrive)
        androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
        settle()
    }

    /** A click on what's described as starting with prefix. */
    private fun tapDescribed(prefix: String) {
        val node = roots().flatMap { all(it.semanticsOwner.rootSemanticsNode) }.firstOrNull { n ->
            n.config.contains(SemanticsActions.OnClick) && described(n)?.startsWith(prefix) == true
        } ?: error("nothing described as $prefix")
        node.config[SemanticsActions.OnClick].action?.invoke()
        androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
        settle()
    }

    /** A long-press on what's labelled label, as a finger's would be. */
    private fun hold(label: String) {
        fun found() = roots().flatMap { all(it.semanticsOwner.rootSemanticsNode) }.firstOrNull { n ->
            n.config.contains(SemanticsActions.OnLongClick) && (label in said(n) || label == described(n))
        }
        repeat(5) { if (found() == null) flush(true) }
        val node = found() ?: error("nothing to hold labelled $label; have " + roots().flatMap { all(it.semanticsOwner.rootSemanticsNode) }.map { said(it) + "|" + it.config.contains(SemanticsActions.OnLongClick) }.filter { it.length > 1 }.take(30))
        node.config[SemanticsActions.OnLongClick].action?.invoke()
        androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
        settle()
    }

    private fun find(label: String) = roots().flatMap { all(it.semanticsOwner.rootSemanticsNode) }.firstOrNull { n ->
        n.config.contains(SemanticsActions.OnClick) && (label in said(n) || label == described(n))
    }

    /** What the host answered is told to the composition, and drawn. */
    private fun flush(frame: Boolean = false) {
        if (frame) { // (a picture taken draws a frame, which is what lays out a list's rows)
            val scratch = File(out, "scratch.png")
            out.mkdirs()
            captureScreenRoboImage(scratch.path)
            scratch.delete()
        }
        repeat(3) {
            Thread.sleep(100) // (the fixture host answers on a thread of its own)
            androidx.compose.runtime.snapshots.Snapshot.sendApplyNotifications()
            settle()
        }
    }

    private fun all(n: SemanticsNode): List<SemanticsNode> = listOf(n) + n.children.flatMap { all(it) }

    private fun said(n: SemanticsNode) = n.config.getOrNull(SemanticsProperties.Text)?.joinToString(" ") { it.text } ?: ""
    private fun described(n: SemanticsNode) = n.config.getOrNull(SemanticsProperties.ContentDescription)?.joinToString(" ")

    /** Every window's root view, shown: the activity's, then a menu's or a dialog's (each a window of its own). */
    private fun windows(): List<View> {
        val global = Class.forName("android.view.WindowManagerGlobal")
        val wm = global.getMethod("getInstance").invoke(null)
        // (the activity's own window first)
        @Suppress("UNCHECKED_CAST")
        val all = global.getDeclaredField("mViews").apply { isAccessible = true }.get(wm) as List<View>
        return all.filter { it.isShown }.sortedBy { if (it === activity.window.decorView) 0 else 1 }
    }

    /** Every window's Compose root. */
    private fun roots(): List<ViewRootForTest> {
        val views = windows()
        fun find(v: View): ViewRootForTest? = v as? ViewRootForTest
            ?: (v as? android.view.ViewGroup)?.let { g -> (0 until g.childCount).firstNotNullOfOrNull { find(g.getChildAt(it)) } }
        return views.mapNotNull { find(it) }
    }

    private fun items(): JSONArray {
        val density = activity.resources.displayMetrics.density
        val out = mutableListOf<JSONObject>()
        roots().forEachIndexed { i, r ->
            val area = if (r.view.rootView === activity.window.decorView) "screen" else "popover"
            val top = r.semanticsOwner.rootSemanticsNode
            val view = r.view
            val at = IntArray(2).also { view.getLocationOnScreen(it) }
            fun walk(n: SemanticsNode) {
                val c = n.config
                val b = n.boundsInRoot
                val text = said(n).ifBlank { null }
                val edit = c.getOrNull(SemanticsProperties.EditableText)?.text
                val desc = described(n)
                val role = c.getOrNull(SemanticsProperties.Role)
                val click = c.contains(SemanticsActions.OnClick)
                val toggle = c.getOrNull(SemanticsProperties.ToggleableState)
                val kind = when {
                    edit != null || c.contains(SemanticsActions.SetText) -> "input"
                    role == Role.Checkbox -> "check"
                    role == Role.Switch || toggle != null -> "switch"
                    role == Role.Image -> "image"
                    role == Role.Tab -> "tab"
                    click || role == Role.Button || role == Role.DropdownList -> "button"
                    text != null -> "text"
                    desc != null -> "image"
                    else -> null
                }
                if (kind != null && b.width > 1 && b.height > 1) {
                    val o = JSONObject().put("kind", kind)
                    (text ?: edit)?.let { o.put("text", it) }
                    c.getOrNull(SemanticsProperties.EditableText)?.let { _ -> text?.let { o.put("sub", it) } }
                    desc?.let { o.put("tip", it) }
                    if (toggle != null) o.put("on", toggle == ToggleableState.On)
                    if (c.contains(SemanticsProperties.Selected) && c[SemanticsProperties.Selected]) o.put("selected", true)
                    if (c.contains(SemanticsProperties.Disabled)) o.put("dim", true)
                    o.put("area", area)
                    o.put("at", JSONArray(listOf((b.left + at[0]) / density, (b.top + at[1]) / density, b.width / density,
                        b.height / density).map { Math.round(it) }))
                    out.add(o)
                }
                n.children.forEach { walk(it) }
            }
            walk(top)
        }
        out.sortWith(compareBy({ if (it.getString("area") == "screen") 0 else 1 },
            { it.getJSONArray("at").getInt(1) / 6 }, { it.getJSONArray("at").getInt(0) }))
        return JSONArray(out)
    }

    private fun write(name: String, o: JSONObject) {
        out.mkdirs()
        File(out, "$name.json").writeText(o.toString(1))
    }
}

/** tests/parity/scenes.json's scenes. */
fun scenes(): List<JSONObject> = JSONObject(File("../../tests/parity/scenes.json").readText()).getJSONArray("scenes")
    .let { a -> (0 until a.length()).map { a.getJSONObject(it) } }

/** Their names, read without org.json (the runner lists them outside Robolectric, where Android's is a stub). */
fun sceneNames(): List<Array<Any>> = Regex(""""name":\s*"([^"]+)"""")
    .findAll(File("../../tests/parity/scenes.json").readText()).map { arrayOf<Any>(it.groupValues[1]) }.toList()

@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(qualifiers = "w411dp-h891dp-notnight-xxhdpi")
class LightParity(scene: String) : Parity("light", scene) {
    companion object {
        @JvmStatic @ParameterizedRobolectricTestRunner.Parameters(name = "{0}") fun names() = sceneNames()
    }
}

@RunWith(ParameterizedRobolectricTestRunner::class)
@Config(qualifiers = "w411dp-h891dp-night-xxhdpi")
class DarkParity(scene: String) : Parity("dark", scene) {
    companion object {
        @JvmStatic @ParameterizedRobolectricTestRunner.Parameters(name = "{0}") fun names() = sceneNames()
    }
}
