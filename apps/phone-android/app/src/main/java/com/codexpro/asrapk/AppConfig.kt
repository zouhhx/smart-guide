package com.codexpro.asrapk

import android.content.Context
import java.security.KeyStore
import java.util.UUID

object AppConfig {
    const val DEFAULT_SERVER_URL = "http://192.168.16.61:3001/api/asr-text"

    private const val PREFS_NAME = "digital_human_asr_prefs"
    private const val KEY_SERVER_URL = "server_url"
    private const val KEY_TARGET_SCREEN_ID = "target_screen_id_v2"
    private const val KEY_TARGET_LOCKED = "target_locked"
    private const val KEY_SOUND_ENABLED = "sound_feedback_enabled"
    private const val KEY_INSTALLATION_ID = "installation_id_v1"
    private const val KEY_CONFIG_SCHEMA = "config_schema"
    private const val CONFIG_SCHEMA_HALL_API = 2

    @Synchronized
    fun migrateToCurrentProtocol(context: Context) {
        val preferences = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        if (preferences.getInt(KEY_CONFIG_SCHEMA, 0) >= CONFIG_SCHEMA_HALL_API) return
        preferences.edit()
            .putString(KEY_SERVER_URL, DEFAULT_SERVER_URL)
            .remove("knowledge_base_id")
            .remove(KEY_TARGET_SCREEN_ID)
            .putBoolean(KEY_TARGET_LOCKED, false)
            .putInt(KEY_CONFIG_SCHEMA, CONFIG_SCHEMA_HALL_API)
            .commit()
        context.getSharedPreferences("gateway_client_auth", Context.MODE_PRIVATE)
            .edit().clear().commit()
        runCatching {
            val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
            if (keyStore.containsAlias("input_gateway_client_token_aes_v1")) {
                keyStore.deleteEntry("input_gateway_client_token_aes_v1")
            }
        }
    }

    fun getServerUrl(context: Context): String? {
        return context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .getString(KEY_SERVER_URL, null)
            ?.trim()
            ?.takeIf { it.isNotEmpty() }
            ?: DEFAULT_SERVER_URL
    }

    fun saveServerUrl(context: Context, url: String) {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_SERVER_URL, url.trim().trimEnd('/'))
            .apply()
    }

    @Synchronized
    fun getOrCreateInstallationId(context: Context): String {
        val preferences = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        preferences.getString(KEY_INSTALLATION_ID, null)
            ?.trim()
            ?.takeIf { it.isNotEmpty() }
            ?.let { return it }
        return "hall-phone-${UUID.randomUUID()}".also {
            preferences.edit().putString(KEY_INSTALLATION_ID, it).commit()
        }
    }

    fun getTargetScreenId(context: Context): String? {
        return context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .getString(KEY_TARGET_SCREEN_ID, null)
            ?.trim()
            ?.takeIf { it.isNotEmpty() }
    }

    fun saveTargetScreenId(context: Context, targetScreenId: String) {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putString(KEY_TARGET_SCREEN_ID, targetScreenId.trim())
            .apply()
    }

    fun clearTargetScreenId(context: Context) {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .remove(KEY_TARGET_SCREEN_ID)
            .apply()
    }

    fun isTargetLocked(context: Context): Boolean =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .getBoolean(KEY_TARGET_LOCKED, false)

    fun setTargetLocked(context: Context, locked: Boolean) {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putBoolean(KEY_TARGET_LOCKED, locked)
            .apply()
    }

    fun isSoundFeedbackEnabled(context: Context): Boolean =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .getBoolean(KEY_SOUND_ENABLED, true)

    fun setSoundFeedbackEnabled(context: Context, enabled: Boolean) {
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .edit()
            .putBoolean(KEY_SOUND_ENABLED, enabled)
            .apply()
    }
}
