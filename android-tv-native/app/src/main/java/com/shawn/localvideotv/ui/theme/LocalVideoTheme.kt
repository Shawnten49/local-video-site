package com.shawn.localvideotv.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

val Background = Color(0xFF121212)
val Panel = Color(0xFF181818)
val PanelFocused = Color(0xFF202124)
val SurfacePressed = Color(0xFF29292C)
val Accent = Color(0xFFFF4F64)
val AccentSoft = Color(0xFFFFA6B1)
val TextPrimary = Color(0xFFF5F7FB)
val TextSecondary = Color(0xFFB3B3B3)
val FocusBorder = Color.White
val SubtleBorder = Color(0x1FFFFFFF)
val GlassStrong = Color(0xD1121212)
val GlassSoft = Color(0xAD181818)
val ErrorColor = Color(0xFFFF7878)

private val AppTypography = Typography(
    displaySmall = TextStyle(
        fontSize = 32.sp,
        lineHeight = 38.sp,
        fontWeight = FontWeight.Black,
    ),
    headlineMedium = TextStyle(
        fontSize = 28.sp,
        lineHeight = 34.sp,
        fontWeight = FontWeight.Black,
    ),
    titleLarge = TextStyle(
        fontSize = 20.sp,
        lineHeight = 26.sp,
        fontWeight = FontWeight.Bold,
    ),
    titleMedium = TextStyle(
        fontSize = 16.sp,
        lineHeight = 22.sp,
        fontWeight = FontWeight.Bold,
    ),
    bodyMedium = TextStyle(
        fontSize = 15.sp,
        lineHeight = 22.sp,
    ),
    labelMedium = TextStyle(
        fontSize = 12.sp,
        lineHeight = 16.sp,
        fontWeight = FontWeight.Medium,
    ),
)

private val AppShapes = Shapes(
    small = RoundedCornerShape(12.dp),
    medium = RoundedCornerShape(16.dp),
    large = RoundedCornerShape(24.dp),
)

private val TvColors = darkColorScheme(
    primary = Accent,
    onPrimary = Color.White,
    secondary = AccentSoft,
    background = Background,
    onBackground = TextPrimary,
    surface = Panel,
    onSurface = TextPrimary,
    surfaceVariant = PanelFocused,
    onSurfaceVariant = TextSecondary,
    error = ErrorColor,
)

@Composable
fun LocalVideoTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = TvColors,
        typography = AppTypography,
        shapes = AppShapes,
        content = content,
    )
}
