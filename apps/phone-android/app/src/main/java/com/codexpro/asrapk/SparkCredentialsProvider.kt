package com.codexpro.asrapk

import android.content.Context

data class SparkCredentials(
    val appId: String,
    val apiKey: String,
    val apiSecret: String
)

class SparkCredentialsProvider(context: Context) {
    private val appContext = context.applicationContext

    fun current(): SparkCredentials? = builtInCredentials()

    private fun builtInCredentials(): SparkCredentials? {
        val appId = appContext.getString(R.string.spark_builtin_app_id).trim()
        val apiKey = appContext.getString(R.string.spark_builtin_api_key).trim()
        val apiSecret = appContext.getString(R.string.spark_builtin_api_secret).trim()
        return if (appId.isNotEmpty() && apiKey.isNotEmpty() && apiSecret.isNotEmpty()) {
            SparkCredentials(appId, apiKey, apiSecret)
        } else {
            null
        }
    }
}
