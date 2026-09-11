package com.codexpro.asrapk.input

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

class InterruptSubmissionTest {
    @Test fun `new mic press abandons slow response and new question proceeds on fixed target`() {
        MockWebServer().use { server ->
            server.start()
            val config = DeploymentConfig(
                server.url("/api/asr-text").newBuilder().host("127.0.0.1").build().toString(), "phone-test"
            )
            val mainQueue = LinkedBlockingQueue<() -> Unit>()
            val updates = mutableListOf<SubmissionUpdate>()
            val coordinator = InputSubmissionCoordinator(
                configProvider = { config },
                postToMain = { mainQueue.add(it) },
                onUpdate = { updates.add(it) }
            )
            try {
                server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
                coordinator.submitVoice("旧问题", "2")
                assertNotNull(server.takeRequest(2, TimeUnit.SECONDS))
                coordinator.abandonPending()
                server.enqueue(MockResponse().setBody("""{"ok":true,"reply":"新回答"}"""))
                coordinator.submitVoice("新问题", "4", config)
                val newRequest = server.takeRequest(3, TimeUnit.SECONDS)!!
                assertEquals("/api/asr-text", newRequest.path)
                assertEquals("4", JSONObject(newRequest.body.readUtf8()).getString("target"))
                mainQueue.poll(3, TimeUnit.SECONDS)!!.invoke()
                mainQueue.poll(3, TimeUnit.SECONDS)!!.invoke()
                assertEquals(2, updates.count { it == SubmissionUpdate.Sending })
                assertEquals(1, updates.count { it is SubmissionUpdate.Succeeded })
                assertFalse(updates.any { it is SubmissionUpdate.Failed })
            } finally {
                coordinator.close()
            }
        }
    }

    @Test fun `interrupt dispatch bypasses a pending ninety second voice request`() {
        MockWebServer().use { server ->
            server.start()
            val config = DeploymentConfig(
                server.url("/api/asr-text").newBuilder().host("127.0.0.1").build().toString(), "phone-test"
            )
            val mainQueue = LinkedBlockingQueue<() -> Unit>()
            val coordinator = InputSubmissionCoordinator(
                configProvider = { config }, postToMain = { mainQueue.add(it) }, onUpdate = {}
            )
            try {
                server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
                coordinator.submitVoice("尚未返回的问题", "2")
                assertNotNull(server.takeRequest(2, TimeUnit.SECONDS))
                SpeechInterruptClient().use { client ->
                    val op = SpeechInterruptOperation(config.asrEndpoint, "2")
                    server.enqueue(MockResponse().setResponseCode(202).setBody(
                        """{"ok":true,"status":"DISPATCHED","target":"2","requestId":"${op.requestId}",
                        "delivered":true,"staleResultsBlocked":true,"queueClearRequested":true,"playbackConfirmed":false}"""
                    ))
                    val results = LinkedBlockingQueue<SpeechInterruptResult>()
                    client.interrupt(op) { results.add(it) }
                    assertEquals(SpeechInterruptResult.Dispatched, results.poll(3, TimeUnit.SECONDS))
                    assertEquals("/api/speech-interrupt", server.takeRequest(2, TimeUnit.SECONDS)!!.path)
                    assertTrue(mainQueue.isEmpty())
                }
            } finally {
                coordinator.close()
            }
        }
    }
}
