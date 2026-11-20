package dev.baude.android

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.job.JobInfo
import android.app.job.JobParameters
import android.app.job.JobScheduler
import android.app.job.JobService
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.widget.Toast
import androidx.compose.runtime.mutableStateOf
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.Executors

/** Kept up to date from the Baude site, as Baude is from git (update.py): FEED, the site's word on its latest build
 * (build_shots.py's baude-android.json: its versionCode, the APK beside it, its size and sha256), looked at as the
 * app starts, each time it's back in front (CHECK_MS apart at most) and every hour in the background (UpdateJob). A
 * newer build's downloaded by itself on a network that isn't metered (on one that is, once Update's tapped, unless
 * Settings → Download updates on metered networks is on), checked
 * (its size and sha256 as the feed says, this app's package at the feed's version, signed as this one is) and only
 * then offered: Update at the home screen's foot, as Baude's sidebar has it, and a notification (once a build) if it
 * came while the app wasn't in front. One that can't be installed over this one is said there instead, and not
 * downloaded again. Tapped: installed over this one by Android's installer, once installs from this app are allowed
 * (asked for straight away the first time, and the install carried on as you come back). Android closes the app to
 * replace it: the new build says "Baude updated" in a notification that opens it again. */
object Update {
    const val FEED = "https://mpts.ie/apps/baude/baude-android.json"
    private const val CHECK_MS = 60_000L // (in front: how often the feed's looked at, at most)
    private const val JOB = 0x0BA0DE // (UpdateJob's)
    private const val NOTE = 0x0BA0DE // (A new version is ready's notification, then Baude updated's)
    private const val CHANNEL = "updates"
    const val INSTALLED = "dev.baude.android.INSTALLED" // (UpdateReceiver's: what the installer said)
    private const val RETRY_MS = 3_600_000L // (a download that wasn't the build the feed said: tried again after
    private const val RETRY_MAX_MS = 86_400_000L // this, twice as long each time it isn't, a day at most)

    /** "": nothing to offer; "found": a newer build, waiting on Update to be downloaded (a metered network);
     * "downloading": being downloaded, Update tapped; "ready": downloaded and checked; "wrong": a newer build that
     * can't be installed over this one (why). */
    val state = mutableStateOf("")
    val progress = mutableStateOf(0f) // (downloading: how much)
    val why = mutableStateOf("") // (wrong: what's said at the home screen's foot)

    private val main = Handler(Looper.getMainLooper())
    private val work = Executors.newSingleThreadExecutor()
    @Volatile private var ready: File? = null
    @Volatile private var wanted = false // (Update tapped before it was ready: installed once it is)
    @Volatile private var looked = 0L

    private fun prefs(ctx: Context): SharedPreferences = ctx.getSharedPreferences("update", Context.MODE_PRIVATE)

    /** As the app starts: the hourly look scheduled (if it isn't), a look now, and "Baude updated" said if this is a
     * newer build than the one that ran last. */
    fun start(ctx: Context) {
        val app = ctx.applicationContext
        val js = app.getSystemService(JobScheduler::class.java)
        if (js.getPendingJob(JOB) == null)
            js.schedule(JobInfo.Builder(JOB, ComponentName(app, UpdateJob::class.java))
                .setPeriodic(3_600_000L).setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY).setPersisted(true).build())
        updated(app)?.let { Toast.makeText(app, it, Toast.LENGTH_LONG).show() }
        prefs(app).edit().putLong("ran", installed(app)).apply()
        NotificationManagerCompat.from(app).cancel(NOTE)
        check(app, force = true)
    }

    /** "Baude updated", as Baude's toast says it after a restart onto a new version, if this build's newer than the
     * one that ran last: with how many new commits (versionCodes count them: the difference). */
    private fun updated(app: Context): String? {
        val now = installed(app)
        val before = prefs(app).getLong("ran", 0L)
        if (before !in 1 until now) return null
        val n = now - before
        return Wording.Updates.updatedCommits(app = "Baude", n = n)
    }

    /** The feed looked at (off the main thread), unless it was less than CHECK_MS ago (force: whatever it was);
     * user: Update tapped, so downloaded on a metered network too; done: once it's been. */
    fun check(ctx: Context, force: Boolean = false, user: Boolean = false, done: (() -> Unit)? = null) {
        val app = ctx.applicationContext
        val now = System.currentTimeMillis()
        if (!force && !user && now - looked < CHECK_MS) {
            done?.invoke()
            return
        }
        looked = now
        work.execute {
            try {
                look(app, user)
            } catch (e: Exception) { // (offline, the site down, a download cut short: looked at again later)
                android.util.Log.w("baude", "update: $e")
                main.post {
                    if (state.value == "downloading") {
                        wanted = false
                        state.value = "found"
                    }
                }
            }
            done?.invoke()
        }
    }

    /** The app back in front: a look, and the install carried on if installs from this app were just allowed. */
    fun resumed(ctx: Context) {
        val app = ctx.applicationContext
        val asked = prefs(app).getLong("allowing", 0L)
        if (asked != 0L) {
            prefs(app).edit().remove("allowing").apply()
            if (System.currentTimeMillis() - asked < 600_000L && allowed(app)) install(app)
        }
        check(app)
    }

    /** The app opened: from A new version is ready's Update (its intent carrying the token only that notification
     * has: no other app's), the install. */
    fun opened(ctx: Context, intent: Intent?) {
        val token = intent?.getStringExtra("update") ?: return
        intent.removeExtra("update")
        val app = ctx.applicationContext
        if (token != prefs(app).getString("token", null)) return
        prefs(app).edit().remove("token").apply()
        install(app)
    }

    /** Update tapped (or the notification's): installed if it's ready; downloaded first if it isn't (and then
     * installed). */
    fun install(ctx: Context) {
        val app = ctx.applicationContext
        val apk = ready
        if (apk == null || !apk.exists()) {
            if (state.value == "wrong") return
            wanted = true
            progress.value = 0f
            state.value = "downloading"
            check(app, user = true)
            return
        }
        if (!allowed(app)) { // (Android's one-time "allow from this source": its page for this app, then back here)
            prefs(app).edit().putLong("allowing", System.currentTimeMillis()).apply()
            app.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:" + app.packageName))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            return
        }
        prefs(app).edit().putLong("installing", System.currentTimeMillis()).apply()
        if (!notifies(app)) // (no Baude updated to open it again with: said before it goes)
            Toast.makeText(app, Wording.Updates.closesToUpdate(app = "Baude"), Toast.LENGTH_LONG).show()
        work.execute {
            if (ready != apk || !apk.exists()) { // (a newer build came since: that one's, fetched first)
                main.post { install(app) }
                return@execute
            }
            val installer = app.packageManager.packageInstaller
            var id = -1
            try {
                val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
                    setAppPackageName(app.packageName)
                    setSize(apk.length())
                    if (Build.VERSION.SDK_INT >= 31) // (an app updating itself: no second question once Update's tapped)
                        setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
                }
                id = installer.createSession(params)
                installer.openSession(id).use { s ->
                    s.openWrite("baude.apk", 0, apk.length()).use { out ->
                        apk.inputStream().use { it.copyTo(out) }
                        s.fsync(out)
                    }
                    val status = PendingIntent.getBroadcast(app, id,
                        Intent(app, UpdateReceiver::class.java).setAction(INSTALLED),
                        PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0))
                    s.commit(status.intentSender)
                }
            } catch (e: Exception) {
                if (id >= 0) try {
                    installer.abandonSession(id)
                } catch (_: Exception) {
                }
                main.post { failed(app, e.message ?: e.toString()) }
            }
        }
    }

    fun failed(ctx: Context, why: String) {
        Toast.makeText(ctx.applicationContext, Wording.Updates.couldntUpdate(why = why), Toast.LENGTH_LONG).show()
    }

    /** Installed (this is the new build, just started): what was downloaded for it, and its notification, gone; if it
     * was Update that installed it, Baude updated said in a notification that opens the app again. */
    fun replaced(ctx: Context) {
        File(ctx.cacheDir, "updates").deleteRecursively()
        NotificationManagerCompat.from(ctx).cancel(NOTE)
        val began = prefs(ctx).getLong("installing", 0L)
        prefs(ctx).edit().remove("installing").apply()
        if (System.currentTimeMillis() - began > 600_000L) return // (Update wasn't tapped: installed some other way)
        try { // (Android before 10 lets it open again by itself; later ones only from the notification)
            ctx.startActivity(Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (_: Exception) {
        }
        notify(ctx.applicationContext, updated(ctx.applicationContext) ?: Wording.Updates.updated(app = "Baude"), offer = false)
    }

    private fun allowed(app: Context) = Build.VERSION.SDK_INT < 26 || app.packageManager.canRequestPackageInstalls()

    private fun installed(app: Context): Long {
        val info = app.packageManager.getPackageInfo(app.packageName, 0)
        return if (Build.VERSION.SDK_INT >= 28) info.longVersionCode else @Suppress("DEPRECATION") info.versionCode.toLong()
    }

    /** One look at the feed (on work): nothing newer, everything downloaded for an older one deleted; something newer,
     * downloaded (unless it's waiting on a metered network for Update, or didn't check out before), checked and
     * offered. What didn't check out is kept (prefs, by its sha256): one that can't be installed over this one
     * ("wrong") never downloaded again, one that wasn't what the feed said ("bad") tried again later and later. */
    private fun look(app: Context, user: Boolean) {
        val conn = URL(FEED).openConnection() as HttpURLConnection
        conn.connectTimeout = 15_000
        conn.readTimeout = 15_000
        conn.setRequestProperty("Cache-Control", "no-cache")
        val feed = try {
            if (conn.responseCode != 200) throw java.io.IOException("the feed: HTTP ${conn.responseCode}")
            JSONObject(conn.inputStream.bufferedReader().readText())
        } finally {
            conn.disconnect()
        }
        val version = feed.getLong("versionCode")
        val sha = feed.getString("sha256").lowercase()
        val size = feed.getLong("size")
        val url = URL(URL(FEED), feed.optString("apk", "baude-android.apk"))
        val dir = File(app.cacheDir, "updates").apply { mkdirs() }
        val apk = File(dir, "$sha.apk")
        val newer = version > installed(app) && Regex("[0-9a-f]{64}").matches(sha)
        dir.listFiles()?.filter { it != apk || !newer }?.forEach { it.delete() } // (an older one's, a download cut short)
        if (ready != null && (ready != apk || !newer)) { // (what was offered is gone: not offered)
            ready = null
            main.post { if (state.value == "ready") state.value = "" }
        }
        if (!newer) {
            main.post {
                wanted = false
                state.value = ""
            }
            return
        }
        prefs(app).getString("wrong", null)?.split(" ", limit = 2)?.takeIf { it[0] == sha }?.let {
            main.post {
                wanted = false
                why.value = it[1]
                state.value = "wrong"
            }
            return
        }
        if (apk.exists()) return offered(app, apk, sha)
        val bad = prefs(app).getString("bad", null)?.split(" ")?.takeIf { it.size == 3 && it[0] == sha }
        val tries = bad?.get(1)?.toIntOrNull() ?: 0
        if (!user && bad != null &&
            System.currentTimeMillis() - bad[2].toLong() < minOf(RETRY_MS shl (tries - 1).coerceIn(0, 10), RETRY_MAX_MS)) {
            main.post { if (state.value == "") state.value = "found" } // (Update: tried again now)
            return
        }
        dev.baude.android.Settings.load(app)
        if (!user && !wanted && !dev.baude.android.Settings.meteredUpdates.value &&
            app.getSystemService(ConnectivityManager::class.java).isActiveNetworkMetered) {
            main.post { if (state.value == "") state.value = "found" }
            return
        }
        val part = File(dir, "$sha.part")
        val digest = MessageDigest.getInstance("SHA-256")
        val get = url.openConnection() as HttpURLConnection
        get.connectTimeout = 15_000
        get.readTimeout = 30_000
        get.setRequestProperty("Cache-Control", "no-cache")
        main.post { progress.value = 0f }
        try {
            if (get.responseCode != 200) throw java.io.IOException("the APK: HTTP ${get.responseCode}")
            var got = 0L
            var said = 0L
            get.inputStream.use { inp ->
                part.outputStream().use { out ->
                    val buf = ByteArray(64 * 1024)
                    while (true) {
                        val n = inp.read(buf)
                        if (n < 0) break
                        out.write(buf, 0, n)
                        digest.update(buf, 0, n)
                        got += n
                        if (got > size) break
                        if (got - said > 256 * 1024) {
                            said = got
                            val f = got.toFloat() / size
                            main.post { progress.value = f }
                        }
                    }
                }
            }
            val hex = digest.digest().joinToString("") { "%02x".format(it) }
            if (got != size || hex != sha) {
                prefs(app).edit().putString("bad", "$sha ${tries + 1} ${System.currentTimeMillis()}").apply()
                throw java.io.IOException("the APK isn't the one the feed says: $got of $size bytes, sha256 $hex")
            }
            checks(app, part, version)?.let {
                prefs(app).edit().putString("wrong", "$sha $it").apply()
                main.post {
                    wanted = false
                    why.value = it
                    state.value = "wrong"
                }
                throw java.io.IOException("the APK can't be installed: $it")
            }
            if (!part.renameTo(apk)) throw java.io.IOException("the APK not kept")
        } finally {
            get.disconnect()
            part.delete()
        }
        prefs(app).edit().remove("bad").remove("wrong").apply()
        offered(app, apk, sha)
    }

    /** Why the APK can't be installed over this one, said (the home screen's foot shows it), or null: it's this app
     * (its package), at version, and signed as this one is. */
    private fun checks(app: Context, file: File, version: Long): String? {
        val pm = app.packageManager
        val flags = if (Build.VERSION.SDK_INT >= 28) PackageManager.GET_SIGNING_CERTIFICATES else
            @Suppress("DEPRECATION") PackageManager.GET_SIGNATURES
        val info = pm.getPackageArchiveInfo(file.path, flags) ?: return Wording.Updates.cantRead
        val v = if (Build.VERSION.SDK_INT >= 28) info.longVersionCode else @Suppress("DEPRECATION") info.versionCode.toLong()
        if (info.packageName != app.packageName || v != version)
            return Wording.Updates.notThisApp
        val mine = pm.getPackageInfo(app.packageName, flags)
        @Suppress("DEPRECATION")
        fun signers(i: android.content.pm.PackageInfo) = (if (Build.VERSION.SDK_INT >= 28)
            i.signingInfo?.apkContentsSigners else i.signatures)?.map { it.toCharsString() }?.toSet()
        val theirs = signers(info)
        return if (theirs != null && theirs == signers(mine)) null
        else Wording.Updates.otherKey
    }

    /** Downloaded and checked: Update shown; installed if it was asked for and the app's in front, else a
     * notification (once a build, unless it was asked for). */
    private fun offered(app: Context, apk: File, sha: String) {
        ready = apk
        main.post {
            state.value = "ready"
            val asked = wanted
            wanted = false
            if (Mesh.shown) {
                if (asked) install(app)
            } else if (asked || prefs(app).getString("noted", null) != sha) {
                prefs(app).edit().putString("noted", sha).apply()
                notify(app, Wording.UpdateBar.ready, offer = true)
            }
        }
    }

    /** Notifications reach you: allowed, and this one's channel not turned off. */
    private fun notifies(app: Context): Boolean {
        channel(app)
        val nm = NotificationManagerCompat.from(app)
        return nm.areNotificationsEnabled() &&
            nm.getNotificationChannel(CHANNEL)?.importance != NotificationManager.IMPORTANCE_NONE
    }

    private fun channel(app: Context) = app.getSystemService(NotificationManager::class.java)
        .createNotificationChannel(NotificationChannel(CHANNEL, Wording.UpdateBar.update, NotificationManager.IMPORTANCE_HIGH))

    /** A notification that opens the app: A new version is ready (offer: silent, with Update), or Baude updated
     * (shown over what's in front: the app was closed to be replaced, and this opens it again). */
    private fun notify(app: Context, title: String, offer: Boolean) {
        if (Build.VERSION.SDK_INT >= 33 &&
            app.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return
        channel(app)
        val open = PendingIntent.getActivity(app, NOTE, Intent(app, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP), PendingIntent.FLAG_IMMUTABLE)
        val n = NotificationCompat.Builder(app, CHANNEL)
            .setSmallIcon(R.drawable.ic_baude)
            .setContentTitle(title)
            .setContentIntent(open)
            .setAutoCancel(true)
            .setSilent(offer)
        if (offer) {
            val token = UUID.randomUUID().toString() // (opened: Update.opened installs only with it)
            prefs(app).edit().putString("token", token).apply()
            n.addAction(0, Wording.UpdateBar.update, PendingIntent.getActivity(app, NOTE + 1, Intent(app, MainActivity::class.java)
                .putExtra("update", token).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
        }
        try {
            NotificationManagerCompat.from(app).notify(NOTE, n.build())
        } catch (_: SecurityException) {
        }
    }

    /** What the installer said: its question passed on (Android's own, where it still asks one), a failure said. */
    fun status(ctx: Context, intent: Intent) {
        when (val s = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                @Suppress("DEPRECATION") val ask = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT) ?: return
                ctx.startActivity(ask.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }
            PackageInstaller.STATUS_SUCCESS, PackageInstaller.STATUS_FAILURE_ABORTED -> {}
            else -> failed(ctx, intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) ?: "status $s")
        }
    }
}

/** The installer's word on an install (Update.status), and this app replaced by a new build (Update.replaced). */
class UpdateReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        when (intent.action) {
            Intent.ACTION_MY_PACKAGE_REPLACED -> Update.replaced(ctx)
            Update.INSTALLED -> Update.status(ctx, intent)
        }
    }
}

/** The hourly look at the feed, the app open or not. */
class UpdateJob : JobService() {
    override fun onStartJob(params: JobParameters): Boolean {
        Update.check(this, force = true) { jobFinished(params, false) }
        return true
    }

    override fun onStopJob(params: JobParameters) = true
}
