package com.shawn.localvideotv

import android.content.res.Configuration
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import com.shawn.localvideotv.ui.TvApp
import com.shawn.localvideotv.ui.theme.LocalVideoTheme

class MainActivity : ComponentActivity() {
    private val viewModel: AppViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        applySystemBars(resources.configuration, forceTvUi())
        setContent {
            LocalVideoTheme {
                TvApp(
                    viewModel = viewModel,
                    forceTvUi = forceTvUi(),
                )
            }
        }
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        applySystemBars(newConfig, forceTvUi())
    }

    private fun forceTvUi(): Boolean =
        BuildConfig.DEBUG && intent.getBooleanExtra("force_tv_ui", false)

    private fun applySystemBars(
        configuration: Configuration,
        forceTvUi: Boolean,
    ) {
        val isTelevision = forceTvUi ||
            configuration.uiMode and Configuration.UI_MODE_TYPE_MASK ==
                Configuration.UI_MODE_TYPE_TELEVISION
        val hideForLandscape =
            configuration.orientation == Configuration.ORIENTATION_LANDSCAPE
        val controller = WindowInsetsControllerCompat(window, window.decorView)
        if (isTelevision || hideForLandscape) {
            controller.hide(WindowInsetsCompat.Type.systemBars())
            controller.systemBarsBehavior =
                WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        } else {
            controller.show(WindowInsetsCompat.Type.systemBars())
        }
    }
}
