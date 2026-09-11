package com.codexpro.asrapk.target

import android.content.Context
import android.os.Handler
import android.os.Looper
import com.codexpro.asrapk.AppConfig
import com.codexpro.asrapk.input.GatewayConfigurationValidator
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

data class TargetScreen(
    val targetScreenId: String,
    val displayName: String,
    val screenType: String,
    val online: Boolean,
    val ipAddress: String?,
    val currentView: String?,
    val currentPptFileName: String?,
    val currentPptSlideNumber: Int?,
    val updatedAt: Long?
) {
    val authoritative: Boolean get() = true
}

data class ScreenDirectorySnapshot(
    val serverTime: Long?,
    val screens: List<TargetScreen>
)

class TargetScreenRepository(
    context: Context,
    private val asrEndpointProvider: () -> String,
    private val onScreensChanged: (List<TargetScreen>, String?) -> Unit
) {
    private val appContext = context.applicationContext
    private val mainHandler = Handler(Looper.getMainLooper())
    private val executor = Executors.newSingleThreadExecutor()
    private val loading = AtomicBoolean(false)
    private val client = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .followRedirects(false)
        .followSslRedirects(false)
        .build()
    @Volatile private var directory: ScreenDirectorySnapshot? = null
    @Volatile private var catalogReady = false

    private val refreshRunnable = object : Runnable {
        override fun run() {
            refresh()
            mainHandler.postDelayed(this, REFRESH_INTERVAL_MS)
        }
    }

    fun start() {
        mainHandler.removeCallbacks(refreshRunnable)
        refresh()
        mainHandler.postDelayed(refreshRunnable, REFRESH_INTERVAL_MS)
    }

    fun stop() = mainHandler.removeCallbacks(refreshRunnable)

    fun refresh() {
        if (!loading.compareAndSet(false, true)) return
        val request = runCatching {
            ScreenDirectoryRequestFactory.create(asrEndpointProvider())
        }.getOrNull()
        if (request == null) {
            loading.set(false)
            catalogReady = false
            mainHandler.post {
                onScreensChanged(directory?.screens.orEmpty(), "HALL_API_URL_INVALID")
            }
            return
        }
        executor.execute {
            val result = runCatching {
                client.newCall(request).execute().use { response ->
                    if (!response.isSuccessful) error("SCREEN_DIRECTORY_HTTP_${response.code}")
                    val body = response.body?.bytes() ?: ByteArray(0)
                    if (body.size > MAX_BYTES) error("SCREEN_DIRECTORY_TOO_LARGE")
                    TargetScreenCatalogParser.parse(body.toString(Charsets.UTF_8))
                }
            }
            result.onSuccess { parsed ->
                directory = parsed
                catalogReady = true
                mainHandler.post { onScreensChanged(parsed.screens, null) }
            }.onFailure { error ->
                catalogReady = false
                val code = error.message?.takeIf { it.startsWith("SCREEN_DIRECTORY_") }
                    ?: "SCREEN_CATALOG_UNREACHABLE"
                mainHandler.post { onScreensChanged(directory?.screens.orEmpty(), code) }
            }
            loading.set(false)
        }
    }

    fun selectedScreen(): TargetScreen? {
        val selectedId = AppConfig.getTargetScreenId(appContext) ?: return null
        return directory?.screens?.firstOrNull { it.targetScreenId == selectedId }
    }

    fun isCatalogReady(): Boolean = catalogReady

    fun select(screen: TargetScreen): Boolean {
        if (!catalogReady || !screen.online) return false
        AppConfig.saveTargetScreenId(appContext, screen.targetScreenId)
        return true
    }

    fun invalidate(clearSelection: Boolean = true) {
        directory = null
        catalogReady = false
        if (clearSelection) {
            AppConfig.setTargetLocked(appContext, false)
            AppConfig.clearTargetScreenId(appContext)
        }
        onScreensChanged(emptyList(), null)
    }

    fun close() {
        stop()
        executor.shutdownNow()
        client.dispatcher.executorService.shutdown()
        client.connectionPool.evictAll()
    }

    companion object {
        private const val REFRESH_INTERVAL_MS = 5_000L
        private const val MAX_BYTES = 1024 * 1024
    }
}

object ScreenDirectoryRequestFactory {
    fun create(asrEndpoint: String): Request {
        val endpoint = GatewayConfigurationValidator.endpoint(asrEndpoint, "api/screens")
        return Request.Builder()
            .url(endpoint)
            .header("Accept", "application/json")
            .get()
            .build()
    }
}

object TargetScreenCatalogParser {
    fun parse(body: String): ScreenDirectorySnapshot {
        val root = JSONObject(body)
        val array = root.optJSONArray("screens")
            ?: throw IllegalArgumentException("SCREEN_DIRECTORY_INVALID")
        require(array.length() <= 64) { "SCREEN_DIRECTORY_TOO_MANY_SCREENS" }
        val screens = buildList {
            for (index in 0 until array.length()) {
                val item = array.optJSONObject(index)
                    ?: throw IllegalArgumentException("SCREEN_DIRECTORY_INVALID")
                val screenNumber = requiredPositiveInt(item, "screenNumber")
                val status = item.optJSONObject("status")
                add(
                    TargetScreen(
                        targetScreenId = screenNumber.toString(),
                        displayName = item.optString("name").trim().takeIf(String::isNotEmpty)
                            ?: "${screenNumber}号屏",
                        screenType = status?.optString("view")
                            ?.takeIf(String::isNotBlank) ?: "SCREEN",
                        online = item.optBoolean("online", false),
                        ipAddress = item.optString("ipAddress").takeIf(String::isNotBlank),
                        currentView = status?.optString("view")?.takeIf(String::isNotBlank),
                        currentPptFileName = status?.optString("currentPptFileName")
                            ?.takeIf(String::isNotBlank),
                        currentPptSlideNumber = status?.optInt("currentPptSlideNumber")
                            ?.takeIf { status.has("currentPptSlideNumber") },
                        updatedAt = status?.optLong("updatedAt")?.takeIf { status.has("updatedAt") }
                    )
                )
            }
        }
        require(screens.map { it.targetScreenId }.distinct().size == screens.size) {
            "SCREEN_DIRECTORY_DUPLICATE_TARGET"
        }
        return ScreenDirectorySnapshot(
            serverTime = root.optLong("serverTime").takeIf { root.has("serverTime") },
            screens = screens.sortedBy { it.targetScreenId.toInt() }
        )
    }

    private fun requiredPositiveInt(json: JSONObject, key: String): Int {
        val value = json.opt(key) as? Number
            ?: throw IllegalArgumentException("SCREEN_DIRECTORY_INVALID")
        val number = value.toDouble()
        require(number.isFinite() && number == number.toInt().toDouble() && number > 0) {
            "SCREEN_DIRECTORY_INVALID"
        }
        return number.toInt()
    }
}
