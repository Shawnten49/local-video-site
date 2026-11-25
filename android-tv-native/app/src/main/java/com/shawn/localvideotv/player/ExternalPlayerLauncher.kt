package com.shawn.localvideotv.player

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri

data class ExternalPlaybackTarget(
    val url: String,
    val mimeType: String,
    val title: String,
)

sealed interface ExternalPlayerLaunchResult {
    data object VlcStarted : ExternalPlayerLaunchResult
    data object ChooserStarted : ExternalPlayerLaunchResult
    data object ChooserRequired : ExternalPlayerLaunchResult
    data class Failed(val message: String) : ExternalPlayerLaunchResult
}

object ExternalPlayerLauncher {
    const val VLC_PACKAGE = "org.videolan.vlc"

    fun open(context: Context, target: ExternalPlaybackTarget): ExternalPlayerLaunchResult {
        val uri = Uri.parse(target.url)
        if (uri.scheme !in setOf("http", "https")) {
            return ExternalPlayerLaunchResult.Failed("原始视频地址无效")
        }
        if (!isVlcInstalled(context)) {
            return ExternalPlayerLaunchResult.ChooserRequired
        }
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setPackage(VLC_PACKAGE)
            setDataAndType(uri, target.mimeType)
            putExtra(Intent.EXTRA_TITLE, target.title)
            putExtra("title", target.title)
            if (context !is Activity) {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
        }
        return try {
            context.startActivity(intent)
            ExternalPlayerLaunchResult.VlcStarted
        } catch (_: ActivityNotFoundException) {
            ExternalPlayerLaunchResult.ChooserRequired
        } catch (error: Exception) {
            ExternalPlayerLaunchResult.Failed(
                error.message?.takeIf { it.isNotBlank() } ?: "无法启动 VLC"
            )
        }
    }

    fun openChooser(context: Context, target: ExternalPlaybackTarget): ExternalPlayerLaunchResult {
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(Uri.parse(target.url), target.mimeType)
            putExtra(Intent.EXTRA_TITLE, target.title)
            if (context !is Activity) {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
        }
        if (intent.resolveActivity(context.packageManager) == null) {
            return ExternalPlayerLaunchResult.Failed("未找到可打开 MKV 的播放器")
        }
        return try {
            val chooser = Intent.createChooser(intent, "选择 MKV 播放器").apply {
                if (context !is Activity) {
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                }
            }
            context.startActivity(chooser)
            ExternalPlayerLaunchResult.ChooserStarted
        } catch (error: Exception) {
            ExternalPlayerLaunchResult.Failed(
                error.message?.takeIf { it.isNotBlank() } ?: "无法打开播放器选择器"
            )
        }
    }

    fun isVlcInstalled(context: Context): Boolean =
        context.packageManager.getLaunchIntentForPackage(VLC_PACKAGE) != null
}
