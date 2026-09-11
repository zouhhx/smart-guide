package com.codexpro.asrapk.input

import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.Response
import okio.BufferedSink
import org.json.JSONObject
import java.io.IOException
import java.util.UUID
import java.util.concurrent.TimeUnit

data class SpeechInterruptOperation(
    val asrEndpoint: String,
    val target: String,
    val requestId: String = "mic-press-${UUID.randomUUID()}"
)

sealed class SpeechInterruptResult {
    // HTTP dispatch confirmation only; never a speaker playback ACK.
    data object Dispatched : SpeechInterruptResult()
    data class Failed(val code: String) : SpeechInterruptResult()
}

/**
 * Dedicated asynchronous connection pool/dispatcher, independent of slow ASR text requests.
 * No application or HTTP retries: a late interrupt must not stop a newer question.
 */
class SpeechInterruptClient : AutoCloseable {
    private val client = OkHttpClient.Builder()
        .connectTimeout(2, TimeUnit.SECONDS)
        .readTimeout(3, TimeUnit.SECONDS)
        .callTimeout(3, TimeUnit.SECONDS)
        .retryOnConnectionFailure(false)
        .followRedirects(false)
        .followSslRedirects(false)
        .build()

    fun interrupt(
        operation: SpeechInterruptOperation,
        onResult: (SpeechInterruptResult) -> Unit
    ): Call? {
        val request = try {
            require(Regex("[1-9][0-9]*").matches(operation.target)) { "TARGET_REQUIRED" }
            require(operation.requestId.isNotBlank() && operation.requestId.length <= 128) {
                "REQUEST_ID_REQUIRED"
            }
            val bytes = JSONObject()
                .put("target", operation.target)
                .put("requestId", operation.requestId)
                .toString().toByteArray(Charsets.UTF_8)
            // One-shot also prevents OkHttp follow-up retries on 408 / 503 Retry-After: 0.
            val body = object : RequestBody() {
                override fun contentType() = "application/json; charset=utf-8".toMediaType()
                override fun contentLength() = bytes.size.toLong()
                override fun isOneShot() = true
                override fun writeTo(sink: BufferedSink) { sink.write(bytes) }
            }
            Request.Builder()
                .url(GatewayConfigurationValidator.endpoint(operation.asrEndpoint, "api/speech-interrupt"))
                .post(body)
                .build()
        } catch (error: IllegalArgumentException) {
            onResult(SpeechInterruptResult.Failed(error.message ?: "INTERRUPT_REQUEST_INVALID"))
            return null
        }
        return client.newCall(request).also { call ->
            call.enqueue(object : Callback {
                override fun onFailure(call: Call, e: IOException) {
                    onResult(SpeechInterruptResult.Failed("INTERRUPT_RESULT_UNKNOWN"))
                }

                override fun onResponse(call: Call, response: Response) {
                    val result = response.use {
                        runCatching {
                            val json = JSONObject(it.body?.string().orEmpty())
                            if (it.code != 202 || json.opt("ok") != true) {
                                val code = (json.opt("status") as? String)?.takeIf(String::isNotBlank)
                                    ?: (json.opt("error") as? String)?.takeIf(String::isNotBlank)
                                    ?: "INTERRUPT_HTTP_${it.code}"
                                SpeechInterruptResult.Failed(code)
                            } else if (
                                json.optString("target") != operation.target ||
                                json.optString("requestId") != operation.requestId ||
                                json.optString("status") != "DISPATCHED" ||
                                json.opt("delivered") != true ||
                                json.opt("staleResultsBlocked") != true ||
                                json.opt("queueClearRequested") != true ||
                                json.opt("playbackConfirmed") != false
                            ) {
                                SpeechInterruptResult.Failed("INTERRUPT_RESPONSE_INVALID")
                            } else {
                                SpeechInterruptResult.Dispatched
                            }
                        }.getOrElse { SpeechInterruptResult.Failed("INTERRUPT_RESULT_UNKNOWN") }
                    }
                    onResult(result)
                }
            })
        }
    }

    override fun close() {
        client.dispatcher.cancelAll()
        client.dispatcher.executorService.shutdown()
        client.connectionPool.evictAll()
    }
}
