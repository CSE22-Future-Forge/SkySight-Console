package lk.ac.mrt.sar.nativemodule

import android.Manifest
import android.app.Notification
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat

/**
 * Persistent "Connected to SAR base" service (ARCHITECTURE.md 9.1/9.4). While it runs the
 * app process, and with it the JS engine holding the WebSocket and GPS watch, survives
 * the screen turning off and the app going to the background.
 *
 * Type "location" when location permission is granted (the team is tracked), otherwise
 * "dataSync" so the connection is still kept.
 */
class SarForegroundService : Service() {
    companion object {
        private const val NOTIFICATION_ID = 0x5A12
        private const val EXTRA_TITLE = "title"
        private const val EXTRA_TEXT = "text"

        @Volatile
        var running = false
            private set

        fun start(ctx: Context, title: String, text: String) {
            val intent = Intent(ctx, SarForegroundService::class.java)
                .putExtra(EXTRA_TITLE, title).putExtra(EXTRA_TEXT, text)
            ContextCompat.startForegroundService(ctx, intent)
        }

        fun update(ctx: Context, title: String, text: String) {
            if (!running) return
            ctx.getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, build(ctx, title, text))
        }

        fun stop(ctx: Context) {
            ctx.stopService(Intent(ctx, SarForegroundService::class.java))
        }

        private fun build(ctx: Context, title: String, text: String): Notification {
            Alarms.ensureChannels(ctx)
            return NotificationCompat.Builder(ctx, Alarms.CHANNEL_SERVICE)
                .setSmallIcon(R.drawable.ic_sar_notify)
                .setContentTitle(title)
                .setContentText(text)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setCategory(NotificationCompat.CATEGORY_SERVICE)
                .setContentIntent(Alarms.launchIntent(ctx, null, "open"))
                .build()
        }
    }

    private var wakeLock: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val title = intent?.getStringExtra(EXTRA_TITLE) ?: "SAR Rescue"
        val text = intent?.getStringExtra(EXTRA_TEXT) ?: "Connected to SAR base"
        val notification = build(this, title, text)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val hasLocation = ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) ==
                PackageManager.PERMISSION_GRANTED
            val type = if (hasLocation) ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION
                       else ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
            startForeground(NOTIFICATION_ID, notification, type)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
        if (wakeLock == null) {
            // Keeps the CPU awake for the socket. Only effective in Doze if the user granted
            // the battery-optimisation exemption at setup.
            wakeLock = getSystemService(PowerManager::class.java)
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "sar:connection").apply {
                    setReferenceCounted(false)
                    acquire()
                }
        }
        running = true
        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
        super.onDestroy()
    }
}
