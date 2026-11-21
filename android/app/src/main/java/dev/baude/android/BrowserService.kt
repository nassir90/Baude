package dev.baude.android

import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat

/** Kept running in the browser's process while a Bot's browser is open on this phone, so it goes on serving the Bot
 * with the app out of sight: a quiet notification of its own says so, to
 * show the browser again or close it. */
class BrowserService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val name = intent?.getStringExtra("name") ?: Wording.BotBrowser.theBot
        val show = Intent(this, BrowserActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)
        val close = Intent(this, BrowserActivity::class.java).putExtra("close", true)
        val n = NotificationCompat.Builder(this, Mesh.LINKED)
            .setSmallIcon(R.drawable.ic_baude)
            .setContentTitle(Wording.BotBrowser.openOnPhone(bot = name))
            .setContentText(Wording.BotBrowser.openOnPhoneBody)
            .setContentIntent(PendingIntent.getActivity(this, 2, show, PendingIntent.FLAG_IMMUTABLE))
            .addAction(0, Wording.Common.close, PendingIntent.getActivity(this, 3, close, PendingIntent.FLAG_IMMUTABLE or
                PendingIntent.FLAG_UPDATE_CURRENT))
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_MIN)
            .build()
        ServiceCompat.startForeground(this, 2, n,
            if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_REMOTE_MESSAGING else 0)
        return START_NOT_STICKY
    }

    companion object {
        fun keep(ctx: Context, name: String) {
            try {
                ctx.startForegroundService(Intent(ctx, BrowserService::class.java).putExtra("name", name))
            } catch (_: Exception) { // (not allowed from where it's asked: the browser goes on as Android lets it)
            }
        }

        fun drop(ctx: Context) = ctx.stopService(Intent(ctx, BrowserService::class.java))
    }
}
