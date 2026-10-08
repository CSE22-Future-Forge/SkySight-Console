package lk.ac.mrt.sar.nativemodule

import android.app.NotificationManager
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import expo.modules.kotlin.Promise
import expo.modules.kotlin.exception.Exceptions
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition

/**
 * Native helpers the rescue app needs and Expo does not provide: Wi-Fi binding,
 * the foreground service, siren alarms with full-screen intent, resumable map
 * download + SHA-256, and battery-optimisation / full-screen-intent settings.
 */
class SarNativeModule : Module() {
    private val ctx
        get() = appContext.reactContext ?: throw Exceptions.ReactContextLost()

    private fun openSettings(action: String) {
        val intent = Intent(action, Uri.parse("package:${ctx.packageName}")).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        ctx.startActivity(intent)
    }

    private fun intentPayload(intent: Intent?): Map<String, String?>? {
        val victimId = intent?.getStringExtra(Alarms.EXTRA_VICTIM)
        val action = intent?.getStringExtra(Alarms.EXTRA_ACTION) ?: return null
        return mapOf("victimId" to victimId, "action" to action)
    }

    override fun definition() = ModuleDefinition {
        Name("SarNative")

        Events("onDownloadProgress", "onNotificationOpen", "onNetworkChange")

        OnCreate {
            WifiBinder.listener = { bound -> sendEvent("onNetworkChange", mapOf("bound" to bound)) }
            appContext.reactContext?.let { Alarms.ensureChannels(it) }
        }

        OnNewIntent { intent ->
            intentPayload(intent)?.let { sendEvent("onNotificationOpen", it) }
        }

        // ------------------------------------------------------------ network ---
        AsyncFunction("bindToWifi") { timeoutMs: Int, promise: Promise ->
            WifiBinder.bind(ctx, timeoutMs.toLong()) { ok -> promise.resolve(ok) }
        }
        Function("unbindNetwork") { WifiBinder.unbind(ctx) }
        Function("isBoundToWifi") { WifiBinder.bound != null }

        // -------------------------------------------------------------- files ---
        AsyncFunction("sha256File") { path: String -> Files.sha256(Files.path(path)) }
        AsyncFunction("downloadFile") { url: String, path: String, headers: Map<String, String> ->
            Files.download(url, Files.path(path), headers) { written, total ->
                sendEvent("onDownloadProgress", mapOf("url" to url, "written" to written.toDouble(),
                                                      "total" to total.toDouble()))
            }.toDouble()
        }

        // ------------------------------------------------------------ service ---
        Function("startService") { title: String, text: String -> SarForegroundService.start(ctx, title, text) }
        Function("updateService") { title: String, text: String -> SarForegroundService.update(ctx, title, text) }
        Function("stopService") { SarForegroundService.stop(ctx) }
        Function("isServiceRunning") { SarForegroundService.running }

        // ------------------------------------------------------- notifications ---
        Function("showAlarm") { victimId: String, title: String, body: String, imagePath: String?, fullScreen: Boolean ->
            Alarms.showAlarm(ctx, victimId, title, body, imagePath, fullScreen)
        }
        Function("showInfo") { id: String, title: String, body: String, victimId: String? ->
            Alarms.showInfo(ctx, id, title, body, victimId)
        }
        Function("cancelNotification") { id: String -> Alarms.cancel(ctx, id) }

        Function("canUseFullScreenIntent") {
            if (Build.VERSION.SDK_INT >= 34) ctx.getSystemService(NotificationManager::class.java).canUseFullScreenIntent()
            else true
        }
        Function("openFullScreenIntentSettings") {
            if (Build.VERSION.SDK_INT >= 34) openSettings(Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT)
        }
        Function("isIgnoringBatteryOptimizations") {
            ctx.getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(ctx.packageName)
        }
        Function("requestIgnoreBatteryOptimizations") {
            openSettings(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS)
        }

        /** Show the alert screen over the lock screen (only while the alert is up). */
        Function("setShowWhenLocked") { on: Boolean ->
            appContext.currentActivity?.let { activity ->
                activity.runOnUiThread {
                    if (Build.VERSION.SDK_INT >= 27) {
                        activity.setShowWhenLocked(on)
                        activity.setTurnScreenOn(on)
                    }
                }
            }
        }

        /** The notification action that cold-started the app, consumed once. */
        Function("takeLaunchAction") {
            val activity = appContext.currentActivity
            val payload = intentPayload(activity?.intent)
            activity?.intent?.removeExtra(Alarms.EXTRA_ACTION)
            payload
        }
    }
}
