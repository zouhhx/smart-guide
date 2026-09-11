package com.codexpro.asrapk.target

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ScreenDirectoryRequestFactoryTest {
    @Test
    fun `screen request uses current anonymous endpoint`() {
        val request = ScreenDirectoryRequestFactory.create(
            "http://127.0.0.1:3001/api/asr-text"
        )

        assertEquals("http://127.0.0.1:3001/api/screens", request.url.toString())
        assertEquals("GET", request.method)
        assertNull(request.header("Authorization"))
    }
}
