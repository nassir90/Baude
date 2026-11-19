package dev.baude.android

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat

/** Kept running while you've hosts, so their connections stay up out of sight and a Bot's message comes as a
 * notification: a quiet one of its own says so. */
class MeshService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        Mesh.start(this)
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE)
        val n: Notification = NotificationCompat.Builder(this, Mesh.LINKED)
            .setSmallIcon(R.drawable.ic_baude)
            .setContentTitle("Connected to your hosts")
            .setContentText("Your Bots' messages come as notifications.")
            .setContentIntent(open)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_MIN)
            .build()
        ServiceCompat.startForeground(this, 1, n,
            if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_REMOTE_MESSAGING else 0)
        return START_STICKY
    }

    companion object {
        fun keep(ctx: Context) {
            if (Mesh.hosts.isEmpty()) return
            try {
                ctx.startForegroundService(Intent(ctx, MeshService::class.java))
            } catch (_: Exception) { // (not allowed from where it's asked: the app's own connections only, then)
            }
        }
    }
}
