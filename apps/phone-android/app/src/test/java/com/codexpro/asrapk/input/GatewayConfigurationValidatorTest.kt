package com.codexpro.asrapk.input

import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import org.junit.Test

class GatewayConfigurationValidatorTest {
    @Test
    fun `normalizes configured asr endpoint and derives hall endpoints`() {
        val configured = " http://192.168.16.61:3001/api/asr-text/ "
        assertEquals(
            "http://192.168.16.61:3001/api/asr-text",
            GatewayConfigurationValidator.normalizeAsrEndpoint(configured)
        )
        assertEquals(
            "http://192.168.16.61:3001/api/screens",
            GatewayConfigurationValidator.endpoint(configured, "api/screens").toString()
        )
        assertEquals(
            "http://192.168.16.61:3001/api/remote-control",
            GatewayConfigurationValidator.endpoint(configured, "api/remote-control").toString()
        )
    }

    @Test
    fun `rejects wrong path credentials query and fragment`() {
        listOf(
            "http://192.168.16.61:3001",
            "http://192.168.16.61:3001/api/screens",
            "http://user@192.168.16.61:3001/api/asr-text",
            "http://192.168.16.61:3001/api/asr-text?a=b",
            "http://192.168.16.61:3001/api/asr-text#x"
        ).forEach { value ->
            assertRejected("HALL_API_URL_INVALID") {
                GatewayConfigurationValidator.normalizeAsrEndpoint(value)
            }
        }
    }

    @Test
    fun `keeps private http boundary and permits https`() {
        assertEquals(
            "https://hall.example/api/asr-text",
            GatewayConfigurationValidator.normalizeAsrEndpoint(
                "https://hall.example/api/asr-text"
            )
        )
        listOf(
            "http://localhost:3001/api/asr-text",
            "http://8.8.8.8:3001/api/asr-text",
            "http://169.254.1.1:3001/api/asr-text"
        ).forEach { value ->
            assertRejected("HALL_API_HTTP_HOST_NOT_PRIVATE") {
                GatewayConfigurationValidator.normalizeAsrEndpoint(value)
            }
        }
    }

    private fun assertRejected(code: String, block: () -> Unit) {
        try {
            block()
            fail("expected $code")
        } catch (error: IllegalArgumentException) {
            assertEquals(code, error.message)
        }
    }
}
