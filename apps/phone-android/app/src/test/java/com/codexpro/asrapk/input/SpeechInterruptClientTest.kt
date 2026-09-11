package com.codexpro.asrapk.input

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

class SpeechInterruptClientTest {
    private fun endpoint(server: MockWebServer) =
        server.url("/api/asr-text").newBuilder().host("127.0.0.1").build().toString()

    private fun accepted(operation: SpeechInterruptOperation) = JSONObject()
        .put("ok", true).put("status", "DISPATCHED")
        .put("target", operation.target).put("requestId", operation.requestId)
        .put("delivered", true).put("staleResultsBlocked", true)
        .put("playbackConfirmed", false).put("queueClearRequested", true)
        .put("canceledGenerations", 0).put("replayed", false)

    @Test fun `202 dispatch including idle is accepted with exact identity and no auth`() {
        MockWebServer().use { server ->
            server.start()
            SpeechInterruptClient().use { client ->
                val operation = SpeechInterruptOperation(endpoint(server), "2")
                val results = LinkedBlockingQueue<SpeechInterruptResult>()
                server.enqueue(MockResponse().setResponseCode(202).setBody(accepted(operation).toString()))
                client.interrupt(operation) { results.add(it) }
                assertEquals(SpeechInterruptResult.Dispatched, results.poll(5, TimeUnit.SECONDS))
                val request = server.takeRequest(2, TimeUnit.SECONDS)!!
                assertEquals("POST", request.method)
                assertEquals("/api/speech-interrupt", request.path)
                assertNull(request.getHeader("Authorization"))
                val json = JSONObject(request.body.readUtf8())
                assertEquals(2, json.length())
                assertEquals("2", json.getString("target"))
                assertEquals(operation.requestId, json.getString("requestId"))
                assertNotEquals(operation.requestId, SpeechInterruptOperation(endpoint(server), "2").requestId)
            }
        }
    }

    @Test fun `upgrade provider offline and overload errors never become success or retries`() {
        MockWebServer().use { server ->
            server.start()
            SpeechInterruptClient().use { client ->
                val results = LinkedBlockingQueue<SpeechInterruptResult>()
                listOf(
                    409 to "CLIENT_UPGRADE_REQUIRED", 409 to "TTS_PROVIDER_UNSUPPORTED",
                    503 to "TARGET_OFFLINE", 429 to "BUSY", 500 to "FAILED"
                ).forEach { (status, code) ->
                    server.enqueue(MockResponse().setResponseCode(status)
                        .setHeader("Retry-After", "0")
                        .setBody("""{"ok":false,"error":"$code"}"""))
                    client.interrupt(SpeechInterruptOperation(endpoint(server), "4")) { results.add(it) }
                    assertEquals(SpeechInterruptResult.Failed(code), results.poll(5, TimeUnit.SECONDS))
                }
                assertEquals(5, server.requestCount)
            }
        }
    }

    @Test fun `wrong target request id and HTTP 200 cannot claim dispatched`() {
        MockWebServer().use { server ->
            server.start()
            SpeechInterruptClient().use { client ->
                val operation = SpeechInterruptOperation(endpoint(server), "2")
                val results = LinkedBlockingQueue<SpeechInterruptResult>()
                listOf(
                    202 to accepted(operation).put("target", "4"),
                    202 to accepted(operation).put("requestId", "different-press"),
                    202 to accepted(operation).put("delivered", false),
                    200 to accepted(operation)
                ).forEach { (status, json) ->
                    server.enqueue(MockResponse().setResponseCode(status).setBody(json.toString()))
                    client.interrupt(operation) { results.add(it) }
                    assertTrue(results.poll(5, TimeUnit.SECONDS) is SpeechInterruptResult.Failed)
                }
            }
        }
    }

    @Test fun `invalid or broadcast targets never reach the server`() {
        MockWebServer().use { server ->
            server.start()
            SpeechInterruptClient().use { client ->
                listOf("", "all", "192.168.16.28", "0", "-1").forEach { target ->
                    var result: SpeechInterruptResult? = null
                    assertNull(client.interrupt(SpeechInterruptOperation(endpoint(server), target)) { result = it })
                    assertEquals(SpeechInterruptResult.Failed("TARGET_REQUIRED"), result)
                }
                assertEquals(0, server.requestCount)
            }
        }
    }

    @Test fun `release cancellation does not retry an in-flight interrupt`() {
        MockWebServer().use { server ->
            server.start()
            SpeechInterruptClient().use { client ->
                server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
                val results = LinkedBlockingQueue<SpeechInterruptResult>()
                val call = client.interrupt(SpeechInterruptOperation(endpoint(server), "2")) { results.add(it) }!!
                assertNotNull(server.takeRequest(2, TimeUnit.SECONDS))
                call.cancel()
                assertEquals(SpeechInterruptResult.Failed("INTERRUPT_RESULT_UNKNOWN"), results.poll(5, TimeUnit.SECONDS))
                assertEquals(1, server.requestCount)
            }
        }
    }
}
