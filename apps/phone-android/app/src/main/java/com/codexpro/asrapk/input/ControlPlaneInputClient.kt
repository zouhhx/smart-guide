package com.codexpro.asrapk.input

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Call
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

class InputClientException(
    val code: String,
    val httpStatus: Int? = null,
    cause: Throwable? = null
) : IOException(code, cause)

data class HallApiResult(
    val ok: Boolean,
    val reply: String?,
    val error: String?,
    val seq: Long?,
    val action: String?,
    val mode: String?,
    val ignored: Boolean,
    val duplicate: Boolean
)

interface HallApiTransport : AutoCloseable {
    fun submitVoice(
        asrEndpoint: String,
        text: String,
        target: String,
        source: String,
        requestId: String
    ): HallApiResult

    fun submitGesture(
        asrEndpoint: String,
        action: String,
        target: String,
        source: String
    ): HallApiResult

    override fun close()
}

class HallApiClient : HallApiTransport {
    private var closed = false
    private var activeCall: Call? = null
    private val client = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(90, TimeUnit.SECONDS)
        .followRedirects(false)
        .followSslRedirects(false)
        .retryOnConnectionFailure(false)
        .build()

    override fun submitVoice(
        asrEndpoint: String,
        text: String,
        target: String,
        source: String,
        requestId: String
    ): HallApiResult {
        val normalizedText = text.trim()
        if (normalizedText.isEmpty()) throw InputClientException("ASR_TEXT_EMPTY")
        val body = JSONObject()
            .put("text", normalizedText)
            .put("target", validateTarget(target))
            .put("source", validateSource(source))
            .put("requestId", requestId)
        return post(asrEndpoint, "api/asr-text", body, VOICE_TIMEOUT_SECONDS)
    }

    override fun submitGesture(
        asrEndpoint: String,
        action: String,
        target: String,
        source: String
    ): HallApiResult {
        if (action !in GESTURE_ACTIONS) throw InputClientException("REMOTE_ACTION_INVALID")
        val body = JSONObject()
            .put("action", action)
            .put("target", validateTarget(target))
            .put("source", validateSource(source))
        return post(asrEndpoint, "api/remote-control", body, CONTROL_TIMEOUT_SECONDS)
    }

    private fun post(
        asrEndpoint: String,
        path: String,
        body: JSONObject,
        timeoutSeconds: Long
    ): HallApiResult {
        val endpoint = runCatching { GatewayConfigurationValidator.endpoint(asrEndpoint, path) }
            .getOrElse { throw InputClientException("HALL_API_URL_INVALID", cause = it) }
        val bytes = body.toString().toByteArray(Charsets.UTF_8)
        if (bytes.size > MAX_BYTES) throw InputClientException("INPUT_REQUEST_TOO_LARGE")
        val request = Request.Builder()
            .url(endpoint)
            .header("Accept", "application/json")
            .post(bytes.toRequestBody(JSON_MEDIA_TYPE))
            .build()
        val call = synchronized(this) {
            if (closed) throw InputClientException("HALL_API_RESULT_UNKNOWN")
            client.newCall(request).apply {
                timeout().timeout(timeoutSeconds, TimeUnit.SECONDS)
            }.also { activeCall = it }
        }
        val response = try {
            call.execute()
        } catch (error: IOException) {
            throw InputClientException("HALL_API_RESULT_UNKNOWN", cause = error)
        }
        response.use {
            val responseBytes = it.body?.bytes() ?: ByteArray(0)
            if (responseBytes.size > MAX_BYTES) throw InputClientException("INPUT_RESPONSE_TOO_LARGE")
            val json = runCatching { JSONObject(responseBytes.toString(Charsets.UTF_8)) }
                .getOrElse { error ->
                    throw InputClientException("HALL_API_RESPONSE_INVALID", it.code, error)
                }
            val result = parseResult(json)
            if (!it.isSuccessful || !result.ok) {
                throw InputClientException(
                    result.error?.takeIf(String::isNotBlank) ?: "HALL_API_HTTP_${it.code}",
                    it.code
                )
            }
            return result
        }
    }

    private fun parseResult(json: JSONObject): HallApiResult = HallApiResult(
        ok = json.optBoolean("ok", false),
        reply = json.optString("reply").takeIf(String::isNotBlank),
        error = json.optString("error").takeIf(String::isNotBlank),
        seq = json.optLong("seq").takeIf { json.has("seq") },
        action = json.optString("action").takeIf(String::isNotBlank),
        mode = json.optString("mode").takeIf(String::isNotBlank),
        ignored = json.optBoolean("ignored", false),
        duplicate = json.optBoolean("duplicate", false)
    )

    private fun validateTarget(value: String): String {
        val target = value.trim()
        if (target.toIntOrNull()?.let { it > 0 } != true) {
            throw InputClientException("TARGET_NOT_SELECTED")
        }
        return target
    }

    private fun validateSource(value: String): String = value.trim().takeIf { it.isNotEmpty() }
        ?: throw InputClientException("SOURCE_INVALID")

    @Synchronized
    override fun close() {
        closed = true
        activeCall?.cancel()
        client.dispatcher.executorService.shutdown()
        client.connectionPool.evictAll()
    }

    companion object {
        const val SWIPE_LEFT = "gesture_swipe_left"
        const val SWIPE_RIGHT = "gesture_swipe_right"
        const val DOUBLE_TAP = "gesture_double_tap"
        private val GESTURE_ACTIONS = setOf(SWIPE_LEFT, SWIPE_RIGHT, DOUBLE_TAP)
        private const val MAX_BYTES = 1024 * 1024
        private const val CONTROL_TIMEOUT_SECONDS = 10L
        private const val VOICE_TIMEOUT_SECONDS = 90L
        private val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()
    }
}
