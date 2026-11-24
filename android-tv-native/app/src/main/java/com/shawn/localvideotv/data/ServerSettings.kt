package com.shawn.localvideotv.data

import android.content.Context
import com.shawn.localvideotv.BuildConfig
import java.net.URI
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

class ServerSettings(context: Context) {
    private val preferences = context.getSharedPreferences("tv_settings", Context.MODE_PRIVATE)
    private val _serverUrl = MutableStateFlow(
        normalize(preferences.getString(KEY_SERVER_URL, null)) ?: BuildConfig.DEFAULT_SERVER_URL
    )
    val serverUrl: StateFlow<String> = _serverUrl

    fun save(value: String): Result<String> {
        val normalized = normalize(value)
            ?: return Result.failure(IllegalArgumentException("请输入有效的 http:// 局域网地址"))
        preferences.edit().putString(KEY_SERVER_URL, normalized).apply()
        _serverUrl.value = normalized
        return Result.success(normalized)
    }

    fun reset(): String {
        preferences.edit().remove(KEY_SERVER_URL).apply()
        _serverUrl.value = BuildConfig.DEFAULT_SERVER_URL
        return BuildConfig.DEFAULT_SERVER_URL
    }

    companion object {
        private const val KEY_SERVER_URL = "server_url"

        fun normalize(raw: String?): String? {
            var value = raw.orEmpty().trim()
            if (value.isBlank()) return null
            if (!value.matches(Regex("^[a-zA-Z][a-zA-Z0-9+.-]*://.*$"))) {
                value = "http://$value"
            }
            return try {
                val uri = URI(value)
                val scheme = uri.scheme?.lowercase()
                if (scheme !in setOf("http", "https") || uri.host.isNullOrBlank()) {
                    null
                } else {
                    val withPath = if (uri.path.isNullOrBlank()) "$value/" else value
                    if (withPath.endsWith("/")) withPath else "$withPath/"
                }
            } catch (_: Exception) {
                null
            }
        }
    }
}
