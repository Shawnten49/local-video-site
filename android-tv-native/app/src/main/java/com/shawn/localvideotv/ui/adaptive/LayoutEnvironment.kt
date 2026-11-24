package com.shawn.localvideotv.ui.adaptive

import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.unit.dp
import com.shawn.localvideotv.ui.LocalTvUi

enum class WindowWidthClass {
    Compact,
    Medium,
    Expanded,
}

@Immutable
data class LayoutEnvironment(
    val isTelevision: Boolean,
    val isPortrait: Boolean,
    val widthClass: WindowWidthClass,
) {
    val isPhonePortrait: Boolean
        get() = !isTelevision && isPortrait

    val isPhoneLandscape: Boolean
        get() = !isTelevision && !isPortrait
}

@Composable
fun rememberLayoutEnvironment(): LayoutEnvironment {
    val configuration = LocalConfiguration.current
    val isTelevision = LocalTvUi.current
    val widthClass = when {
        configuration.screenWidthDp < 600 -> WindowWidthClass.Compact
        configuration.screenWidthDp < 1000 -> WindowWidthClass.Medium
        else -> WindowWidthClass.Expanded
    }
    return LayoutEnvironment(
        isTelevision = isTelevision,
        isPortrait = configuration.screenHeightDp >= configuration.screenWidthDp,
        widthClass = widthClass,
    )
}

@Composable
fun Modifier.tvOverscan(environment: LayoutEnvironment): Modifier {
    if (!environment.isTelevision) {
        return windowInsetsPadding(
            androidx.compose.foundation.layout.WindowInsets.safeDrawing
        )
    }
    return windowInsetsPadding(
        androidx.compose.foundation.layout.WindowInsets.safeDrawing
    ).padding(48.dp)
}
