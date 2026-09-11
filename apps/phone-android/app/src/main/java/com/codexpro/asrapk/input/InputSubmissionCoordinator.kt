package com.codexpro.asrapk.input

import android.os.Handler
import android.os.Looper
import java.util.UUID
import java.util.concurrent.Executors

sealed class SubmissionUpdate {
    data object Sending : SubmissionUpdate()
    data class Succeeded(val result: HallApiResult) : SubmissionUpdate()
    data class Failed(val code: String) : SubmissionUpdate()
}

class InputSubmissionCoordinator(
    private val configProvider: () -> DeploymentConfig,
    private val clientFactory: () -> HallApiTransport = ::HallApiClient,
    private val postToMain: ((() -> Unit) -> Unit) = { Handler(Looper.getMainLooper()).post(it) },
    private val onUpdate: (SubmissionUpdate) -> Unit
) {
    private val executor = Executors.newSingleThreadExecutor()
    // State below is owned by the main thread; old callbacks are discarded by generation.
    private var sending = false
    private var generation = 0L
    private var activeClient: HallApiTransport? = null

    fun submitVoice(text: String, target: String, config: DeploymentConfig = configProvider()) =
        submit(config) { snapshot, client ->
            client.submitVoice(
                snapshot.asrEndpoint,
                text,
                target,
                snapshot.sourceId,
                UUID.randomUUID().toString()
            )
        }

    fun submitSwipeLeft(target: String) = submitGesture(HallApiClient.SWIPE_LEFT, target)

    fun submitSwipeRight(target: String) = submitGesture(HallApiClient.SWIPE_RIGHT, target)

    fun submitDoubleTap(target: String) = submitGesture(HallApiClient.DOUBLE_TAP, target)

    private fun submitGesture(action: String, target: String) = submit(configProvider()) { config, client ->
        client.submitGesture(config.asrEndpoint, action, target, config.sourceId)
    }

    private fun submit(config: DeploymentConfig, operation: (DeploymentConfig, HallApiTransport) -> HallApiResult) {
        if (sending) {
            return
        }
        if (config.asrEndpoint.isBlank()) {
            onUpdate(SubmissionUpdate.Failed("HALL_API_URL_INVALID"))
            return
        }
        sending = true
        val currentGeneration = ++generation
        val client = clientFactory()
        activeClient = client
        onUpdate(SubmissionUpdate.Sending)
        executor.execute {
            val update = try {
                SubmissionUpdate.Succeeded(operation(config, client))
            } catch (error: InputClientException) {
                SubmissionUpdate.Failed(error.code)
            } catch (_: Exception) {
                SubmissionUpdate.Failed("HALL_API_RESULT_UNKNOWN")
            } finally {
                client.close()
            }
            postToMain {
                if (generation == currentGeneration) {
                    sending = false
                    activeClient = null
                    onUpdate(update)
                }
            }
        }
    }

    fun abandonPending() {
        generation++
        sending = false
        activeClient?.close()
        activeClient = null
    }

    fun close() {
        abandonPending()
        executor.shutdownNow()
    }
}
