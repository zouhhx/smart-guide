package com.codexpro.asrapk.target

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TargetScreenCatalogParserTest {
    @Test
    fun `parses current hall screens and retains offline entries`() {
        val directory = TargetScreenCatalogParser.parse(validDirectory())

        assertEquals(1788945809000L, directory.serverTime)
        assertEquals(listOf("2", "4"), directory.screens.map { it.targetScreenId })
        val screen = directory.screens.first()
        assertEquals("WIN-20241001ETD (2号屏)", screen.displayName)
        assertTrue(screen.online)
        assertEquals("ppt", screen.currentView)
        assertEquals("九天人工智能.pptx", screen.currentPptFileName)
        assertEquals(3, screen.currentPptSlideNumber)
        assertFalse(directory.screens[1].online)
        assertNull(directory.screens[1].currentView)
    }

    @Test(expected = IllegalArgumentException::class)
    fun `rejects duplicate screen numbers`() {
        TargetScreenCatalogParser.parse(
            """{"screens":[{"screenNumber":2,"online":true},{"screenNumber":2,"online":true}]}"""
        )
    }

    @Test(expected = IllegalArgumentException::class)
    fun `rejects non positive screen number`() {
        TargetScreenCatalogParser.parse("""{"screens":[{"screenNumber":0,"online":true}]}""")
    }

    private fun validDirectory() = """
        {
          "screens": [
            {
              "deviceId": "device-2",
              "screenNumber": 2,
              "name": "WIN-20241001ETD (2号屏)",
              "online": true,
              "ipAddress": "192.168.16.28",
              "status": {
                "view": "ppt",
                "currentPptFileName": "九天人工智能.pptx",
                "currentPptSlideNumber": 3,
                "updatedAt": 1788945808379
              }
            },
            {
              "deviceId": "device-4",
              "screenNumber": 4,
              "name": "4号屏",
              "online": false,
              "status": null
            }
          ],
          "total": 2,
          "serverTime": 1788945809000
        }
    """.trimIndent()
}
