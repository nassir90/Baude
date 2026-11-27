package dev.baude.android

import android.app.job.JobInfo
import android.app.job.JobParameters
import android.app.job.JobScheduler
import android.app.job.JobService
import android.content.ComponentName
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.SystemClock

/** A Bot's notification of a turn's end, left up while nothing of the app hears its host (Android ended the app, or
 * closed its links while it was put away): its hosts reached again as the app's start reaches them (Mesh.start; or,
 * the app running, the links that are down made again: Mesh.wake), and each notification whose turn's end has been
 * seen on a head of the mesh since taken back as its host lists its sessions (Mesh.withdrawSeen). Scheduled as each is
 * posted, and again, LATER_MS on, while any is up. Android runs it as it sees fit: put off while the phone dozes, till
 * its next window for jobs. */
class SeenJob : JobService() {
    override fun onStartJob(params: JobParameters): Boolean {
        run(this) { jobFinished(params, false) }
        return true
    }

    override fun onStopJob(params: JobParameters) = true

    companion object {
        private const val JOB = 0x5EE0 // (its job's id)
        const val FIRST_MS = 60_000L // (from a notification's post: the app's links may be gone by then)
        const val LATER_MS = 15 * 60_000L // (while one's still up)
        const val LISTEN_MS = 45_000L // (how long its hosts are waited for to list their sessions)
        private val main = Handler(Looper.getMainLooper())

        /** The job scheduled (in place of one that is), after delay. */
        fun schedule(ctx: Context, delay: Long = FIRST_MS) {
            val app = ctx.applicationContext
            try {
                app.getSystemService(JobScheduler::class.java)?.schedule(JobInfo.Builder(JOB, ComponentName(app, SeenJob::class.java))
                    .setMinimumLatency(delay).setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY).setPersisted(true).build())
            } catch (_: Exception) {
            }
        }

        /** What the job does: the hosts with a notification up reached, each waited for (LISTEN_MS at most) to list
         * its sessions since, which takes back those seen; done() then, the job scheduled again if any are still up. */
        fun run(ctx: Context, done: () -> Unit) {
            Mesh.start(ctx)
            Mesh.wake()
            val began = SystemClock.elapsedRealtime()
            fun look() {
                val up = Mesh.turnNotes()
                if (up.isNotEmpty() && up.any { Mesh.listedAt(it) < began } && SystemClock.elapsedRealtime() - began < LISTEN_MS) {
                    main.postDelayed(::look, 500)
                    return
                }
                if (Mesh.turnNotes().isNotEmpty()) schedule(ctx, LATER_MS)
                done()
            }
            main.post(::look)
        }
    }
}
