package com.codexpro.asrapk.input

import android.content.Context
import com.codexpro.asrapk.AppConfig
data class DeploymentConfig(
    val asrEndpoint: String,
    val sourceId: String
)

class DeploymentConfigProvider(context: Context) {
    private val appContext = context.applicationContext

    fun current(): DeploymentConfig {
        return DeploymentConfig(
            asrEndpoint = runCatching {
                GatewayConfigurationValidator.normalizeAsrEndpoint(
                    AppConfig.getServerUrl(appContext).orEmpty()
                )
            }.getOrDefault(""),
            sourceId = AppConfig.getOrCreateInstallationId(appContext)
        )
    }
}
