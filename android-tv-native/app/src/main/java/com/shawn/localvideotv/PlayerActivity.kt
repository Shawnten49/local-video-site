package com.shawn.localvideotv

import android.content.Context
import android.content.Intent
import android.content.res.Configuration
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.core.view.WindowCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.shawn.localvideotv.ui.LocalTvUi
import com.shawn.localvideotv.ui.ExternalPlayerDialog
import com.shawn.localvideotv.ui.ExternalPlayerDialogState
import com.shawn.localvideotv.ui.PlayerScreen
import com.shawn.localvideotv.ui.theme.LocalVideoTheme
import com.shawn.localvideotv.player.ExternalPlayerLaunchResult
import com.shawn.localvideotv.player.ExternalPlayerLauncher
import kotlinx.coroutines.launch

class PlayerActivity : ComponentActivity() {
    private val viewModel: PlayerViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        overridePendingTransition(0, 0)

        val videoId = intent.getIntExtra(EXTRA_VIDEO_ID, -1)
        if (videoId < 0) {
            finish()
            return
        }
        viewModel.start(videoId)

        val isTelevision =
            (BuildConfig.DEBUG && intent.getBooleanExtra(EXTRA_FORCE_TV_UI, false)) ||
                (
                    resources.configuration.uiMode and Configuration.UI_MODE_TYPE_MASK ==
                        Configuration.UI_MODE_TYPE_TELEVISION
                )

        setContent {
            val state by viewModel.state.collectAsStateWithLifecycle()
            val coroutineScope = rememberCoroutineScope()
            var externalPlayerDialog by remember {
                mutableStateOf<ExternalPlayerDialogState?>(null)
            }
            LocalVideoTheme {
                CompositionLocalProvider(LocalTvUi provides isTelevision) {
                    PlayerScreen(
                        videoId = videoId,
                        state = state,
                        progressStore = viewModel.progressStore,
                        isTelevision = isTelevision,
                        onPlayNext = viewModel::playNext,
                        onRetry = { viewModel.start(videoId) },
                        onOpenExternal = {
                            coroutineScope.launch {
                                viewModel.originalPlayback(
                                    videoId,
                                    fallbackTitle = state.title,
                                )
                                    .onSuccess { target ->
                                        val result = ExternalPlayerLauncher.open(
                                            this@PlayerActivity,
                                            target,
                                        )
                                        when (result) {
                                            ExternalPlayerLaunchResult.VlcStarted,
                                            ExternalPlayerLaunchResult.ChooserStarted
                                            -> Unit

                                            ExternalPlayerLaunchResult.ChooserRequired -> {
                                                externalPlayerDialog =
                                                    ExternalPlayerDialogState(
                                                        title = "未检测到 VLC",
                                                        message = (
                                                            "未安装 VLC for Android"
                                                                + "（org.videolan.vlc）。"
                                                                + "可以安装 VLC，或选择其他支持 MKV 的播放器。"
                                                            ),
                                                        target = target,
                                                    )
                                            }

                                            is ExternalPlayerLaunchResult.Failed -> {
                                                externalPlayerDialog =
                                                    ExternalPlayerDialogState(
                                                        title = "无法打开外部播放器",
                                                        message = result.message,
                                                    )
                                            }
                                        }
                                    }
                                    .onFailure { error ->
                                        externalPlayerDialog =
                                            ExternalPlayerDialogState(
                                                title = "原始视频不可用",
                                                message = error.message
                                                    ?.takeIf { it.isNotBlank() }
                                                    ?: "无法获取原始视频地址",
                                            )
                                    }
                            }
                        },
                        onExit = {
                            overridePendingTransition(0, 0)
                            finish()
                        },
                    )
                    ExternalPlayerDialog(
                        state = externalPlayerDialog,
                        onChooseOtherPlayer = { target ->
                            val result = ExternalPlayerLauncher.openChooser(
                                this@PlayerActivity,
                                target,
                            )
                            when (result) {
                                ExternalPlayerLaunchResult.ChooserStarted,
                                ExternalPlayerLaunchResult.VlcStarted
                                -> externalPlayerDialog = null

                                ExternalPlayerLaunchResult.ChooserRequired -> {
                                    externalPlayerDialog =
                                        ExternalPlayerDialogState(
                                            title = "未找到兼容播放器",
                                            message = "请先安装 VLC 或其他支持 MKV 的视频播放器。",
                                            target = target,
                                        )
                                }

                                is ExternalPlayerLaunchResult.Failed -> {
                                    externalPlayerDialog =
                                        ExternalPlayerDialogState(
                                            title = "无法打开播放器选择器",
                                            message = result.message,
                                            target = target,
                                        )
                                }
                            }
                        },
                        onDismiss = { externalPlayerDialog = null },
                    )
                }
            }
        }
    }

    companion object {
        private const val EXTRA_VIDEO_ID = "video_id"
        private const val EXTRA_FORCE_TV_UI = "force_tv_ui"

        fun createIntent(
            context: Context,
            videoId: Int,
            forceTvUi: Boolean = false,
        ): Intent =
            Intent(context, PlayerActivity::class.java)
                .putExtra(EXTRA_VIDEO_ID, videoId)
                .putExtra(EXTRA_FORCE_TV_UI, forceTvUi)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP)
    }
}
