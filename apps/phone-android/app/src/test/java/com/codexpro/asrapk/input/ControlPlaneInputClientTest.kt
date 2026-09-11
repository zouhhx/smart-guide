package com.codexpro.asrapk.input

import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class ControlPlaneInputClientTest {
    @Test
    fun `voice sends the documented json once without authentication`() {
        val server = MockWebServer()
        server.start()
        val client = HallApiClient()
        try {
            server.enqueue(MockResponse().setResponseCode(200).setBody(
                """{"ok":true,"seq":2001,"reply":"请选择要展示的文件","target":"2"}"""
            ))

            val result = client.submitVoice(
                endpoint(server),
                " 查找九天人工智能的PPT ",
                "2",
                "hall-phone-test",
                "request-1"
            )

            val request = server.takeRequest()
            assertEquals("/api/asr-text", request.path)
            assertNull(request.getHeader("Authorization"))
            val body = JSONObject(request.body.readUtf8())
            assertEquals("查找九天人工智能的PPT", body.getString("text"))
            assertEquals("2", body.getString("target"))
            assertEquals("hall-phone-test", body.getString("source"))
            assertEquals("request-1", body.getString("requestId"))
            assertEquals("请选择要展示的文件", result.reply)
            assertEquals(2001L, result.seq)
        } finally {
            client.close()
            server.shutdown()
        }
    }

    @Test
    fun `gestures use exact current action and expose ignored duplicate flags`() {
        val server = MockWebServer()
        server.start()
        val client = HallApiClient()
        try {
            server.enqueue(MockResponse().setResponseCode(200).setBody(
                """{"ok":true,"seq":10,"action":"next_slide","sourceAction":"gesture_swipe_left","target":2,"mode":"ppt"}"""
            ))
            val accepted = client.submitGesture(
                endpoint(server), HallApiClient.SWIPE_LEFT, "2", "hall-phone-test"
            )
            assertFalse(accepted.ignored)
            assertEquals("next_slide", accepted.action)
            assertEquals(
                "gesture_swipe_left",
                JSONObject(server.takeRequest().body.readUtf8()).getString("action")
            )

            server.enqueue(MockResponse().setResponseCode(200).setBody(
                """{"ok":true,"seq":11,"action":"next_slide","target":2,"mode":"ppt","ignored":true,"duplicate":true}"""
            ))
            val duplicate = client.submitGesture(
                endpoint(server), HallApiClient.SWIPE_LEFT, "2", "hall-phone-test"
            )
            assertTrue(duplicate.ignored)
            assertTrue(duplicate.duplicate)
        } finally {
            client.close()
            server.shutdown()
        }
    }

    @Test
    fun `ok false preserves server error and is never treated as success`() {
        val server = MockWebServer()
        server.start()
        val client = HallApiClient()
        try {
            server.enqueue(MockResponse().setResponseCode(400).setBody(
                """{"ok":false,"seq":12,"error":"请先选择一个具体屏幕"}"""
            ))
            try {
                client.submitGesture(
                    endpoint(server), HallApiClient.DOUBLE_TAP, "2", "hall-phone-test"
                )
                fail("expected server error")
            } catch (error: InputClientException) {
                assertEquals("请先选择一个具体屏幕", error.code)
                assertEquals(400, error.httpStatus)
            }
        } finally {
            client.close()
            server.shutdown()
        }
    }

    private fun endpoint(server: MockWebServer): String =
        server.url("/api/asr-text").newBuilder().host("127.0.0.1").build().toString()
}
