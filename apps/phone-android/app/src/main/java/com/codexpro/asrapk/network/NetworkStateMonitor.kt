package com.codexpro.asrapk.network

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.os.Handler
import android.os.Looper
import com.codexpro.asrapk.input.GatewayConfigurationValidator
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

enum class PhoneNetworkState { OFFLINE, AVAILABLE }
enum class ControlReachability { UNKNOWN, UNREACHABLE, REACHABLE }

data class NetworkState(
    val phone: PhoneNetworkState,
    val control: ControlReachability
)

class NetworkStateMonitor(
    context: Context,
    private val asrEndpointProvider: () -> String,
    private val onChanged: (NetworkState) -> Unit
) {
    private val connectivity = context.applicationContext
        .getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
    private val mainHandler = Handler(Looper.getMainLooper())
    private val executor = Executors.newSingleThreadExecutor()
    private val probing = AtomicBoolean(false)
    private val client = OkHttpClient.Builder()
        .connectTimeout(2, TimeUnit.SECONDS)
        .readTimeout(3, TimeUnit.SECONDS)
        .followRedirects(false)
        .build()
    @Volatile private var state = NetworkState(PhoneNetworkState.OFFLINE, ControlReachability.UNKNOWN)
    private var registered = false

    private val callback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) = updateAndProbe()
        override fun onLost(network: Network) = updateAndProbe()
        override fun onCapabilitiesChanged(network: Network, capabilities: android.net.NetworkCapabilities) = updateAndProbe()
    }

    private val probeRunnable = object : Runnable {
        override fun run() {
            updateAndProbe()
            mainHandler.postDelayed(this, PROBE_INTERVAL_MS)
        }
    }

    fun start() {
        if (!registered) {
            connectivity.registerDefaultNetworkCallback(callback)
            registered = true
        }
        mainHandler.removeCallbacks(probeRunnable)
        updateAndProbe()
        mainHandler.postDelayed(probeRunnable, PROBE_INTERVAL_MS)
    }

    fun stop() {
        mainHandler.removeCallbacks(probeRunnable)
        if (registered) {
            runCatching { connectivity.unregisterNetworkCallback(callback) }
            registered = false
        }
    }

    private fun updateAndProbe() {
        val available = connectivity.activeNetwork != null
        if (!available) {
            publish(NetworkState(PhoneNetworkState.OFFLINE, ControlReachability.UNKNOWN))
            return
        }
        publish(state.copy(phone = PhoneNetworkState.AVAILABLE))
        probeControl()
    }

    private fun probeControl() {
        if (!probing.compareAndSet(false, true)) return
        val asrEndpoint = asrEndpointProvider().trim()
        if (asrEndpoint.isBlank()) {
            probing.set(false)
            publish(NetworkState(PhoneNetworkState.AVAILABLE, ControlReachability.UNREACHABLE))
            return
        }
        executor.execute {
            val reachable = runCatching {
                val endpoint = GatewayConfigurationValidator.endpoint(asrEndpoint, "api/screens")
                client.newCall(Request.Builder().url(endpoint).get().build()).execute().use { response ->
                    response.isSuccessful
                }
            }.getOrDefault(false)
            probing.set(false)
            publish(
                NetworkState(
                    PhoneNetworkState.AVAILABLE,
                    if (reachable) ControlReachability.REACHABLE else ControlReachability.UNREACHABLE
                )
            )
        }
    }

    private fun publish(value: NetworkState) {
        state = value
        mainHandler.post { onChanged(value) }
    }

    fun close() {
        stop()
        executor.shutdownNow()
        client.dispatcher.executorService.shutdown()
        client.connectionPool.evictAll()
    }

    companion object {
        private const val PROBE_INTERVAL_MS = 5_000L
    }
}
