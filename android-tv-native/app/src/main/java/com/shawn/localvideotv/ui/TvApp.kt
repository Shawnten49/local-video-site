package com.shawn.localvideotv.ui

import android.content.res.Configuration
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.shawn.localvideotv.AppScreen
import com.shawn.localvideotv.AppViewModel
import com.shawn.localvideotv.PlayerActivity

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
    val isTelevision = forceTvUi ||
        configuration.uiMode and Configuration.UI_MODE_TYPE_MASK ==
        Configuration.UI_MODE_TYPE_TELEVISION
    val leaveSettings = {
        focusManager.clearFocus(force = true)
        keyboardController?.hide()
        viewModel.backToLibrary()
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
                loading = state.detailLoading,
                error = state.detailError,
                posterUrl = viewModel.assetUrl(state.detail?.posterUrl),
                onPlay = { videoId ->
                    context.startActivity(
                        PlayerActivity.createIntent(
                            context = context,
                            videoId = videoId,
                            forceTvUi = isTelevision,
                        )
                    )
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
    }
}
