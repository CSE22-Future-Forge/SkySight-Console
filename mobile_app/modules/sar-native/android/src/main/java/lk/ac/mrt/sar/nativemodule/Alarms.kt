package lk.ac.mrt.sar.nativemodule

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.graphics.BitmapFactory
import android.media.AudioAttributes
import android.net.Uri
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat

/**
 * Notifications (ARCHITECTURE.md 9.4): a siren alarm channel with full-screen intent for
 * new victims, a quiet channel for assignment news, and the foreground-service channel.
 * Taps and actions relaunch the app with extras that SarNativeModule hands to JS.
 */
object Alarms {
    const val CHANNEL_ALARM = "sar_alarm_v1"   // bump the suffix to change sound/importance: channels are immutable
    const val CHANNEL_INFO = "sar_info_v1"
    const val CHANNEL_SERVICE = "sar_service_v1"

    const val EXTRA_VICTIM = "sar_victim_id"
    const val EXTRA_ACTION = "sar_action"

    fun ensureChannels(ctx: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val nm = ctx.getSystemService(NotificationManager::class.java)
        val siren = Uri.parse("android.resource://${ctx.packageName}/${R.raw.siren}")
        val alarm = NotificationChannel(CHANNEL_ALARM, "New victim alarms", NotificationManager.IMPORTANCE_HIGH).apply {
            description = "Siren and full-screen alert when the base reports a new victim"
            setSound(siren, AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ALARM)
                .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                .build())
            enableVibration(true)
            vibrationPattern = longArrayOf(0, 900, 300, 900, 300, 900)
            setBypassDnd(true)
            lockscreenVisibility = NotificationCompat.VISIBILITY_PUBLIC
        }
        val info = NotificationChannel(CHANNEL_INFO, "Assignments", NotificationManager.IMPORTANCE_DEFAULT).apply {
            description = "Reassignments and other updates from the base"
        }
        val service = NotificationChannel(CHANNEL_SERVICE, "Connection to base", NotificationManager.IMPORTANCE_LOW).apply {
            description = "Keeps the link to the SAR base open in the background"
            setShowBadge(false)
        }
        nm.createNotificationChannels(listOf(alarm, info, service))
    }

    fun launchIntent(ctx: Context, victimId: String?, action: String): PendingIntent {
        val intent = ctx.packageManager.getLaunchIntentForPackage(ctx.packageName)!!.apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            putExtra(EXTRA_VICTIM, victimId)
            putExtra(EXTRA_ACTION, action)
        }
        val code = ((victimId ?: "") + action).hashCode()
        return PendingIntent.getActivity(ctx, code, intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
    }

    private fun notificationId(id: String) = id.hashCode()

    fun showAlarm(ctx: Context, victimId: String, title: String, body: String, imagePath: String?, fullScreen: Boolean) {
        ensureChannels(ctx)
        val builder = NotificationCompat.Builder(ctx, CHANNEL_ALARM)
            .setSmallIcon(R.drawable.ic_sar_notify)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setColor(0xFFE53935.toInt())
            .setAutoCancel(true)
            .setOnlyAlertOnce(false) // each repeat re-sounds the siren until the alert is opened
            .setContentIntent(launchIntent(ctx, victimId, "open"))
            .addAction(0, "Accept", launchIntent(ctx, victimId, "accept"))
            .addAction(0, "View on map", launchIntent(ctx, victimId, "map"))
        if (fullScreen) builder.setFullScreenIntent(launchIntent(ctx, victimId, "alert"), true)
        imagePath?.let { p ->
            val f = Files.path(p)
            if (f.isFile) BitmapFactory.decodeFile(f.path)?.let { builder.setLargeIcon(it) }
        }
        notify(ctx, notificationId(victimId), builder)
    }

    fun showInfo(ctx: Context, id: String, title: String, body: String, victimId: String?) {
        ensureChannels(ctx)
        val builder = NotificationCompat.Builder(ctx, CHANNEL_INFO)
            .setSmallIcon(R.drawable.ic_sar_notify)
            .setContentTitle(title)
            .setContentText(body)
            .setAutoCancel(true)
            .setContentIntent(launchIntent(ctx, victimId, "open"))
        notify(ctx, notificationId(id), builder)
    }

    fun cancel(ctx: Context, id: String) = NotificationManagerCompat.from(ctx).cancel(notificationId(id))

    private fun notify(ctx: Context, id: Int, builder: NotificationCompat.Builder) {
        try {
            NotificationManagerCompat.from(ctx).notify(id, builder.build())
        } catch (e: SecurityException) {
            // POST_NOTIFICATIONS not granted: the in-app alert still shows.
        }
    }
}
