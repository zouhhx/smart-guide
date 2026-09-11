package com.codexpro.facereception

import android.content.Context

object FaceAppConfig {
    const val DEFAULT_SERVER_URL = "http://192.168.16.61:3001"
    private const val PREFS = "face_reception"
    private const val SERVER_URL = "server_url"
    private const val TARGET_SCREEN = "target_screen"

    fun serverUrl(context: Context): String = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        .getString(SERVER_URL, DEFAULT_SERVER_URL)
        ?.trim()
        ?.trimEnd('/')
        ?.takeIf { it.startsWith("http://") || it.startsWith("https://") }
        ?: DEFAULT_SERVER_URL

    fun targetScreen(context: Context): String = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        .getString(TARGET_SCREEN, "all")
        ?.trim()
        ?.takeIf { it.isNotEmpty() }
        ?: "all"

    fun save(context: Context, serverUrl: String, targetScreen: String) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putString(SERVER_URL, serverUrl.trim().trimEnd('/'))
            .putString(TARGET_SCREEN, targetScreen.trim().ifEmpty { "all" })
            .apply()
    }
}
