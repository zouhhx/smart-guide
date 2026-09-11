package com.codexpro.asrapk

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class LegacyProtocolGuardTest {
    @Test
    fun `production source uses only the current hall control protocol`() {
        val root = File(System.getProperty("user.dir") ?: error("user.dir unavailable"))
        val source = sequenceOf(
            File(root, "app/src/main/java"),
            File(root, "src/main/java")
        ).firstOrNull { it.isDirectory } ?: error("main source directory not found")
        val text = source.walkTopDown()
            .filter { it.isFile && it.extension in setOf("kt", "java") }
            .joinToString("\n") { it.readText() }

        assertTrue(text.contains("api/asr-text"))
        assertTrue(text.contains("api/remote-control"))
        assertTrue(text.contains("api/screens"))
        assertFalse(text.contains("v1/input-events"))
        assertFalse(text.contains("v1/screens"))
        assertFalse(text.contains("Authorization"))
        assertFalse(text.contains("GatewayBearerTokenStore"))
        assertFalse(text.contains("knowledgeBaseIds"))
        assertFalse(text.contains("RestrictionsManager"))
    }
}
