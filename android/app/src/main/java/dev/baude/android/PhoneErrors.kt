package dev.baude.android

import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** This app's errors (baudelib/errorbook.py, the same): each kept once by its fingerprint with how many times it
 * happened, till Telemetry tells them to this phone's host: a message not sent or a draft not saved (Mesh.ask's), and
 * the app crashing (an exception nothing caught, on any thread: written at once to crash.json, as the app goes, and
 * kept by the host as it starts again). Each goes with the screen shown and the user's last few screens (its
 * breadcrumbs), by their schema/ui names. */
object PhoneErrors {
    private const val STACK_MAX = 20
    private const val CRUMBS_MAX = 8
    private const val TEXT_MAX = 300
    private const val PENDING_MAX = 500
    /** The requests whose error is the user's: a message not sent, a draft not saved (errorbook's FAILS). */
    val FAILS = mapOf("input" to "send", "start" to "send", "held" to "send", "queue" to "send", "draft_save" to "draft_save")
    /** Each screen's schema/ui entity (MainActivity's screen[0]): what's on screen, as the desktop names it. */
    private val SCREENS = mapOf("contacts" to "Sidebar.region", "chat" to "Composer.region", "phone" to "ThisPhone.page",
        "feedback" to "SendFeedback.dialog", "settings" to "Settings.dialog", "subscriptions" to "Subscriptions.dialog",
        "changelog" to "Changelog.dialog", "mesh" to "MeshVersions.dialog", "databases" to "DatabaseSchemas.page",
        "kubernetes" to "Kubernetes.page", "docker" to "Docker.page", "terminal" to "Terminal.page",
        "fixes" to "OngoingFeedback.page")
    private val pending = LinkedHashMap<String, JSONObject>()
    private val crumbs = ArrayDeque<JSONObject>()
    @Volatile private var screen: String? = null
    var crashFile: File? = null
        private set
    var wake: () -> Unit = {}

    private fun plain(t: String?) = t.orEmpty().trim().lineSequence().firstOrNull().orEmpty().take(TEXT_MAX)
    private fun shape(t: String?) = plain(t).replace(Regex("0x[0-9a-fA-F]+|[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}|\\d+"), "#").take(120)

    /** As the app starts: a crash on any thread written to crash.json (the host keeps it as it starts again), then
     * handled as before. */
    fun install(filesDir: File) {
        crashFile = File(filesDir, "crash.json")
        val before = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { t, e ->
            try {
                val now = System.currentTimeMillis() / 1000.0
                val err = of(e, "crash").put("count", 1).put("first", now).put("last", now)
                    .put("commit", BuildConfig.COMMIT.take(12).ifEmpty { null } ?: JSONObject.NULL)
                    .put("wire", WireConstants.VERSION)
                context(err)
                crashFile?.writeText(err.toString())
            } catch (_: Throwable) {
            }
            before?.uncaughtException(t, e)
        }
    }

    /** A throwable as an error (without its count and times): its fingerprint its kind, type and the innermost frame of
     * Baude's own code (the innermost frame, if none is). */
    fun of(e: Throwable, kind: String): JSONObject {
        val frames = e.stackTrace.toList()
        val top = frames.firstOrNull { it.className.startsWith("dev.baude") } ?: frames.firstOrNull()
        val type = e.javaClass.name
        fun said(f: StackTraceElement) = "${f.fileName}:${f.lineNumber} ${f.className.substringAfterLast('.')}.${f.methodName}"
        return JSONObject().put("kind", kind).put("type", type).put("message", plain(e.message).ifEmpty { type })
            .put("file", top?.fileName ?: JSONObject.NULL).put("line", top?.lineNumber ?: JSONObject.NULL)
            .put("function", top?.let { "${it.className.substringAfterLast('.')}.${it.methodName}" } ?: JSONObject.NULL)
            .put("stack", JSONArray(frames.take(STACK_MAX).reversed().map(::said)))
            .put("fingerprint", if (top != null) "$kind:$type@${top.fileName}:${top.lineNumber}" else "$kind:$type")
    }

    /** An error noted as it happened (no stack): its fingerprint its kind, type and key (else its message's shape). */
    fun note(kind: String, type: String, message: String?, key: String? = null, op: String? = null, code: Any? = null) {
        add(JSONObject().put("kind", kind).put("type", type).put("message", plain(message).ifEmpty { type })
            .put("fingerprint", "$kind:$type:${key ?: shape(message)}").put("stack", JSONArray())
            .apply { op?.let { put("wire_op", it) }; code?.let { put("code", it) } })
    }

    private fun context(err: JSONObject) {
        err.put("app", "phone").put("screen", screen ?: JSONObject.NULL).put("part", JSONObject.NULL)
            .put("crumbs", synchronized(crumbs) { JSONArray(crumbs.toList()) })
    }

    @Synchronized
    private fun add(err: JSONObject) {
        val now = System.currentTimeMillis() / 1000.0
        context(err)
        val fp = err.getString("fingerprint")
        val got = pending[fp]
        if (got != null) {
            for (k in err.keys()) got.put(k, err.get(k))
            got.put("count", got.getInt("count") + 1).put("last", now)
        } else if (pending.size < PENDING_MAX) pending[fp] = err.put("count", 1).put("first", now).put("last", now)
        wake()
    }

    /** What's happened since they were last taken, each once with its count. */
    @Synchronized
    fun take(): List<JSONObject> = pending.values.toList().also { pending.clear() }

    /** Errors taken but not kept (the host wasn't there): held again, counted with any since. */
    @Synchronized
    fun giveBack(errors: List<JSONObject>) {
        for (e in errors) {
            val got = pending[e.getString("fingerprint")]
            if (got == null) { if (pending.size < PENDING_MAX) pending[e.getString("fingerprint")] = e }
            else got.put("count", got.getInt("count") + e.getInt("count")).put("first", minOf(got.getDouble("first"), e.getDouble("first")))
        }
    }

    /** The schema/ui entity on screen, as last shown. */
    fun shownScreen(): String? = screen

    /** The screen shown changed (Telemetry.screen's): kept as what's on screen, and among the last few. */
    fun shown(now: List<String>) {
        val entity = SCREENS[now.firstOrNull()] ?: return
        if (entity == screen) return
        screen = entity
        synchronized(crumbs) {
            crumbs.addLast(JSONObject().put("at", System.currentTimeMillis() / 1000.0).put("action", "open").put("screen", entity))
            while (crumbs.size > CRUMBS_MAX) crumbs.removeFirst()
        }
    }

    /** The crash written as the app last went, if it did: taken (and the file gone). */
    fun crashed(): JSONObject? {
        val f = crashFile ?: return null
        if (!f.exists()) return null
        return try { JSONObject(f.readText()) } catch (_: Exception) { null }.also { f.delete() }
    }
}
