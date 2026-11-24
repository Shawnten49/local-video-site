package com.shawn.localvideotv.ui.components

import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.unit.dp
import androidx.compose.ui.zIndex
import com.shawn.localvideotv.ui.LocalTvUi
import com.shawn.localvideotv.ui.theme.FocusBorder
import com.shawn.localvideotv.ui.theme.SubtleBorder

@Composable
fun FocusSurface(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    shape: Shape = RoundedCornerShape(14.dp),
    background: Color,
    focusedBackground: Color = background,
    idleBorderColor: Color = SubtleBorder,
    idleBorderWidth: androidx.compose.ui.unit.Dp = 1.dp,
    focusRequester: FocusRequester? = null,
    contentPadding: PaddingValues = PaddingValues(0.dp),
    onFocusChanged: ((Boolean) -> Unit)? = null,
    content: @Composable BoxScope.() -> Unit,
) {
    val isTelevision = LocalTvUi.current
    var focused by remember { mutableStateOf(false) }
    val showFocus = focused && isTelevision && enabled
    val scale by animateFloatAsState(
        targetValue = if (showFocus) 1.05f else 1f,
        animationSpec = tween(160),
        label = "focus-scale",
    )
    val elevation by animateDpAsState(
        targetValue = if (showFocus) 18.dp else 0.dp,
        animationSpec = tween(160),
        label = "focus-elevation",
    )
    val visualModifier = modifier
        .zIndex(if (showFocus) 2f else 0f)
        .graphicsLayer {
            scaleX = scale
            scaleY = scale
        }
        .shadow(elevation, shape)
        .clip(shape)
        .background(if (showFocus) focusedBackground else background, shape)
        .border(
            width = if (showFocus) 3.dp else idleBorderWidth,
            color = if (showFocus) FocusBorder else idleBorderColor,
            shape = shape,
        )
        .onFocusChanged {
            if (focused != it.isFocused) {
                focused = it.isFocused
                onFocusChanged?.invoke(it.isFocused)
            }
        }
    val focusAwareModifier = if (focusRequester != null) {
        visualModifier.focusRequester(focusRequester)
    } else {
        visualModifier
    }
    val baseModifier = focusAwareModifier
        .clickable(enabled = enabled, onClick = onClick)
        .padding(contentPadding)
    Box(
        modifier = baseModifier,
        content = content,
    )
}
