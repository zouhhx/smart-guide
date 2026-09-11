package com.codexpro.asrapk.input

import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import java.net.InetAddress

object GatewayConfigurationValidator {
    fun normalizeAsrEndpoint(value: String): String {
        val normalized = value.trim()
        val url = normalized.toHttpUrlOrNull()
            ?: throw IllegalArgumentException("HALL_API_URL_INVALID")
        if (url.username.isNotEmpty() || url.password.isNotEmpty() ||
            url.query != null || url.fragment != null
        ) throw IllegalArgumentException("HALL_API_URL_INVALID")
        if (url.scheme !in setOf("http", "https")) {
            throw IllegalArgumentException("HALL_API_URL_INVALID")
        }
        if (url.scheme == "http" && !isPrivateOrLoopbackIpLiteral(url.host)) {
            throw IllegalArgumentException("HALL_API_HTTP_HOST_NOT_PRIVATE")
        }
        val path = url.encodedPath.trimEnd('/')
        if (path != "/api/asr-text") {
            throw IllegalArgumentException("HALL_API_URL_INVALID")
        }
        return url.newBuilder().encodedPath("/api/asr-text").build().toString()
    }

    fun endpoint(asrEndpoint: String, path: String): HttpUrl {
        val url = normalizeAsrEndpoint(asrEndpoint).toHttpUrl()
        val normalizedPath = path.trim().trimStart('/')
        require(normalizedPath in ALLOWED_PATHS) { "HALL_API_PATH_INVALID" }
        return url.newBuilder().encodedPath("/$normalizedPath").build()
    }

    internal fun isPrivateOrLoopbackIpLiteral(host: String): Boolean {
        parseIpv4(host)?.let { octets ->
            val first = octets[0]
            val second = octets[1]
            return first == 127 || first == 10 ||
                (first == 172 && second in 16..31) ||
                (first == 192 && second == 168)
        }
        if (':' !in host || '%' in host) return false
        val address = runCatching { InetAddress.getByName(host).address }.getOrNull() ?: return false
        if (address.size == 4) {
            val octets = address.map { it.toInt() and 0xff }
            val first = octets[0]
            val second = octets[1]
            return first == 127 || first == 10 ||
                (first == 172 && second in 16..31) ||
                (first == 192 && second == 168)
        }
        return address.size == 16 &&
            (address.dropLast(1).all { it == 0.toByte() } && address.last() == 1.toByte() ||
                (address[0].toInt() and 0xfe) == 0xfc)
    }

    private fun parseIpv4(host: String): List<Int>? {
        val parts = host.split('.')
        if (parts.size != 4) return null
        return parts.map { part ->
            if (part.isEmpty() || part.length > 3 || part.any { !it.isDigit() }) return null
            part.toIntOrNull()?.takeIf { it in 0..255 } ?: return null
        }
    }

    private val ALLOWED_PATHS = setOf(
        "api/asr-text", "api/screens", "api/remote-control", "api/speech-interrupt"
    )
}
