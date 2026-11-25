package com.shawn.localvideotv.ui

import android.content.res.Configuration
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.shawn.localvideotv.AppScreen
import com.shawn.localvideotv.AppViewModel
import com.shawn.localvideotv.PlayerActivity
import com.shawn.localvideotv.player.ExternalPlayerLaunchResult
import com.shawn.localvideotv.player.ExternalPlayerLauncher
import kotlinx.coroutines.launch

@Composable
fun TvApp(
    viewModel: AppViewModel,
    forceTvUi: Boolean = false,
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val gridState = rememberLazyGridState()
    val configuration = LocalConfiguration.current
    val context = LocalContext.current
    val focusManager = LocalFocusManager.current
    val keyboardController = LocalSoftwareKeyboardController.current
    val coroutineScope = rememberCoroutineScope()
    var externalPlayerDialog by remember {
        mutableStateOf<ExternalPlayerDialogState?>(null)
    }
    val isTelevision = forceTvUi ||
        configuration.uiMode and Configuration.UI_MODE_TYPE_MASK ==
        Configuration.UI_MODE_TYPE_TELEVISION
    val leaveSettings = {
        focusManager.clearFocus(force = true)
        keyboardController?.hide()
        viewModel.backToLibrary()
    }
    val openExternalPlayer: (Int) -> Unit = { videoId ->
        coroutineScope.launch {
            val detail = state.detail
            val fallbackMimeType = when (detail?.extension?.lowercase()) {
                ".mkv" -> "video/x-matroska"
                ".webm" -> "video/webm"
                else -> "video/*"
            }
            viewModel.originalPlayback(
                videoId = videoId,
                fallbackMimeType = fallbackMimeType,
                fallbackTitle = detail?.title.orEmpty(),
            )
                .onSuccess { target ->
                    val result = ExternalPlayerLauncher.open(context, target)
                    when (result) {
                        ExternalPlayerLaunchResult.VlcStarted -> Unit
                        ExternalPlayerLaunchResult.ChooserStarted -> Unit
                        ExternalPlayerLaunchResult.ChooserRequired -> {
                            externalPlayerDialog = ExternalPlayerDialogState(
                                title = "未检测到 VLC",
                                message = (
                                    "未安装 VLC for Android（org.videolan.vlc）。"
                                        + "可以安装 VLC，或选择其他支持 MKV 的播放器。"
                                    ),
                                target = target,
                            )
                        }
                        is ExternalPlayerLaunchResult.Failed -> {
                            externalPlayerDialog = ExternalPlayerDialogState(
                                title = "无法打开外部播放器",
                                message = result.message,
                            )
                        }
                    }
                }
                .onFailure { error ->
                    externalPlayerDialog = ExternalPlayerDialogState(
                        title = "原始视频不可用",
                        message = error.message?.takeIf { it.isNotBlank() }
                            ?: "无法获取原始视频地址",
                    )
                }
        }
    }

    BackHandler(
        enabled = state.screen is AppScreen.Detail ||
            state.screen is AppScreen.Settings
    ) {
        when (state.screen) {
            is AppScreen.Detail -> viewModel.backToLibrary()
            is AppScreen.Settings -> leaveSettings()
            is AppScreen.Library -> Unit
        }
    }

    CompositionLocalProvider(LocalTvUi provides isTelevision) {
        when (val screen = state.screen) {
            is AppScreen.Library -> LibraryScreen(
                cards = state.cards,
                total = state.total,
                loading = state.libraryLoading,
                loadingMore = state.loadingMore,
                error = state.libraryError,
                gridState = gridState,
                assetUrl = viewModel::assetUrl,
                onOpenDetail = viewModel::openDetail,
                onLoadMore = viewModel::loadMore,
                onRetry = viewModel::refreshLibrary,
                onSettings = viewModel::openSettings,
                requestInitialFocus = isTelevision,
            )
            is AppScreen.Detail -> DetailScreen(
                detail = state.detail,
                selectedVideoId = (
                    state.selectedVideoId
                    ?: state.detail?.id
                    ?: screen.videoId
                ),
                loading = state.detailLoading,
                error = state.detailError,
                posterUrl = viewModel.assetUrl(state.detail?.posterUrl),
                onPlay = { videoId ->
                    viewModel.selectVideo(videoId)
                    coroutineScope.launch {
                        viewModel.playbackDetail(videoId)
                            .onSuccess { selected ->
                                val useExternal = (
                                    selected.extension.equals(
                                        ".mkv",
                                        ignoreCase = true,
                                    )
                                    || selected.playback.playbackMode ==
                                    "external_vlc"
                                )
                                if (useExternal) {
                                    openExternalPlayer(videoId)
                                } else {
                                    context.startActivity(
                                        PlayerActivity.createIntent(
                                            context = context,
                                            videoId = videoId,
                                            forceTvUi = isTelevision,
                                        )
                                    )
                                }
                            }
                            .onFailure { error ->
                                externalPlayerDialog =
                                    ExternalPlayerDialogState(
                                        title = "无法读取播放信息",
                                        message = error.message
                                            ?.takeIf { it.isNotBlank() }
                                            ?: "请检查服务器连接后重试",
                                    )
                            }
                    }
                },
                onOpenExternal = { videoId ->
                    viewModel.selectVideo(videoId)
                    openExternalPlayer(videoId)
                },
                onRetry = { viewModel.openDetail(screen.videoId) },
                onSettings = viewModel::openSettings,
                onBack = viewModel::backToLibrary,
            )
            is AppScreen.Settings -> SettingsScreen(
                serverUrl = state.serverUrl,
                onSave = viewModel::saveServerUrl,
                onReset = viewModel::resetServerUrl,
                onBack = leaveSettings,
            )
        }
        ExternalPlayerDialog(
            state = externalPlayerDialog,
            onChooseOtherPlayer = { target ->
                val result = ExternalPlayerLauncher.openChooser(context, target)
                when (result) {
                    ExternalPlayerLaunchResult.ChooserStarted,
                    ExternalPlayerLaunchResult.VlcStarted
                    -> externalPlayerDialog = null

                    ExternalPlayerLaunchResult.ChooserRequired -> {
                        externalPlayerDialog = ExternalPlayerDialogState(
                            title = "未找到兼容播放器",
                            message = "请先安装 VLC 或其他支持 MKV 的视频播放器。",
                            target = target,
                        )
                    }
                    is ExternalPlayerLaunchResult.Failed -> {
                        externalPlayerDialog = ExternalPlayerDialogState(
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
