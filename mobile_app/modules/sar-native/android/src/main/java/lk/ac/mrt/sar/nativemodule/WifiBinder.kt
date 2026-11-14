package lk.ac.mrt.sar.nativemodule

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.os.Handler
import android.os.Looper

/**
 * Keeps the whole app process on the SAR-NET Wi-Fi (ARCHITECTURE.md section 3).
 *
 * SAR-NET has no internet, so Android would otherwise route our sockets over mobile
 * data and never reach the laptop. We request a Wi-Fi network WITHOUT requiring
 * NET_CAPABILITY_INTERNET and bind the process to it; every socket (fetch, WebSocket,
 * downloads) then uses it. The callback stays registered, so the binding follows the
 * team out of range and back.
 */
object WifiBinder {
    @Volatile
    var bound: Network? = null
        private set

    /** Called with true/false whenever the binding appears or goes away. */
    var listener: ((Boolean) -> Unit)? = null

    private var callback: ConnectivityManager.NetworkCallback? = null
    private val main = Handler(Looper.getMainLooper())

    @Synchronized
    fun bind(context: Context, timeoutMs: Long, done: (Boolean) -> Unit) {
        val cm = context.getSystemService(ConnectivityManager::class.java)
        if (callback != null) {
            // Already requested: the callback keeps the binding up to date.
            if (bound != null) done(true) else main.postDelayed({ done(bound != null) }, timeoutMs)
            return
        }
        var finished = false
        val request = NetworkRequest.Builder()
            .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
            .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .build()
        val cb = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                cm.bindProcessToNetwork(network)
                bound = network
                listener?.invoke(true)
                main.post {
                    if (!finished) {
                        finished = true
                        done(true)
                    }
                }
            }

            override fun onLost(network: Network) {
                if (bound == network) {
                    cm.bindProcessToNetwork(null)
                    bound = null
                    listener?.invoke(false)
                }
            }
        }
        callback = cb
        cm.requestNetwork(request, cb)
        main.postDelayed({
            if (!finished) {
                finished = true
                done(false)
            }
        }, timeoutMs)
    }

    @Synchronized
    fun unbind(context: Context) {
        val cm = context.getSystemService(ConnectivityManager::class.java)
        cm.bindProcessToNetwork(null)
        callback?.let { runCatching { cm.unregisterNetworkCallback(it) } }
        callback = null
        bound = null
    }
}
