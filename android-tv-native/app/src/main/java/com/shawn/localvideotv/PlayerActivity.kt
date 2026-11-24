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
import androidx.core.view.WindowCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.shawn.localvideotv.ui.LocalTvUi
import com.shawn.localvideotv.ui.PlayerScreen
import com.shawn.localvideotv.ui.theme.LocalVideoTheme

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
            LocalVideoTheme {
                CompositionLocalProvider(LocalTvUi provides isTelevision) {
                    PlayerScreen(
                        videoId = videoId,
                        state = state,
                        progressStore = viewModel.progressStore,
                        isTelevision = isTelevision,
                        onPlayNext = viewModel::playNext,
                        onRetry = { viewModel.start(videoId) },
                        onExit = {
                            overridePendingTransition(0, 0)
                            finish()
                        },
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
