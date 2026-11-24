package com.shawn.localvideotv.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import com.shawn.localvideotv.data.VideoCard
import com.shawn.localvideotv.ui.LocalTvUi
import com.shawn.localvideotv.ui.theme.AccentSoft
import com.shawn.localvideotv.ui.theme.Panel
import com.shawn.localvideotv.ui.theme.PanelFocused
import com.shawn.localvideotv.ui.theme.TextPrimary
import com.shawn.localvideotv.ui.theme.TextSecondary

@Composable
fun FocusableButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    primary: Boolean = false,
    focusRequester: FocusRequester? = null,
    compact: Boolean = false,
) {
    val isTelevision = LocalTvUi.current
    FocusSurface(
        onClick = onClick,
        modifier = modifier.heightIn(min = if (isTelevision) 56.dp else 48.dp),
        shape = RoundedCornerShape(12.dp),
        background = if (primary) Color(0xFFFF4F64) else Panel,
        focusedBackground = if (primary) Color(0xFFFF4F64) else PanelFocused,
        focusRequester = focusRequester,
        contentPadding = androidx.compose.foundation.layout.PaddingValues(
            horizontal = if (compact) 12.dp else 20.dp,
            vertical = if (compact) 8.dp else 12.dp,
        ),
    ) {
        Text(
            text = text,
            color = TextPrimary,
            fontSize = when {
                compact -> 13.sp
                isTelevision -> 18.sp
                else -> 16.sp
            },
            fontWeight = FontWeight.Bold,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.align(Alignment.Center),
        )
    }
}

@Composable
fun VideoPosterCard(
    card: VideoCard,
    thumbnailUrl: String?,
    onClick: () -> Unit,
    onFocused: () -> Unit,
    modifier: Modifier = Modifier,
) {
    FocusSurface(
        onClick = onClick,
        modifier = modifier,
        shape = RoundedCornerShape(14.dp),
        background = Panel,
        focusedBackground = PanelFocused,
        onFocusChanged = { focused ->
            if (focused) onFocused()
        },
    ) {
        Column {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .aspectRatio(16f / 9f)
                    .background(Color(0xFF1D2230)),
            ) {
                AsyncImage(
                    model = thumbnailUrl,
                    contentDescription = card.title,
                    contentScale = androidx.compose.ui.layout.ContentScale.Crop,
                    modifier = Modifier.fillMaxWidth().aspectRatio(16f / 9f),
                )
                if (card.kind != "season") {
                    Box(
                        modifier = Modifier
                            .align(Alignment.BottomEnd)
                            .padding(8.dp)
                            .background(Color(0xB3000000), RoundedCornerShape(999.dp))
                            .padding(horizontal = 8.dp, vertical = 3.dp),
                    ) {
                        Text(
                            text = formatDuration(card.durationSeconds),
                            color = TextPrimary,
                            fontSize = 11.sp,
                        )
                    }
                }
            }
            Column(
                modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
                verticalArrangement = Arrangement.spacedBy(5.dp),
            ) {
                Text(
                    text = card.title,
                    color = TextPrimary,
                    fontSize = if (LocalTvUi.current) 15.sp else 12.sp,
                    lineHeight = if (LocalTvUi.current) 20.sp else 17.sp,
                    fontWeight = FontWeight.Bold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (card.kind == "season") {
                        Text(
                            text = "${card.availableEpisodeCount ?: 0}/${card.episodeCount ?: 0} 集",
                            color = AccentSoft,
                            fontSize = 12.sp,
                        )
                    } else {
                        Text(
                            text = "单集",
                            color = TextSecondary,
                            fontSize = 12.sp,
                        )
                    }
                    card.seriesTitle?.takeIf { it.isNotBlank() }?.let {
                        Text(
                            text = it,
                            color = TextSecondary,
                            fontSize = 12.sp,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                }
            }
        }
    }
}

@Composable
fun ErrorPanel(
    message: String,
    onRetry: () -> Unit,
    onSettings: () -> Unit,
    modifier: Modifier = Modifier,
) {
    GlassSurface(
        modifier = modifier,
        shape = RoundedCornerShape(24.dp),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(28.dp),
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(20.dp)) {
            Text(
                "无法加载视频",
                color = TextPrimary,
                fontSize = 28.sp,
                fontWeight = FontWeight.Bold,
            )
            Text(message, color = TextSecondary, fontSize = 16.sp)
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                FocusableButton("重试", onRetry, primary = true)
                FocusableButton("服务器设置", onSettings)
            }
        }
    }
}

fun formatDuration(seconds: Double?): String {
    if (seconds == null || seconds <= 0) return "时长未知"
    val total = seconds.toLong()
    val hours = total / 3600
    val minutes = (total % 3600) / 60
    val secs = total % 60
    return if (hours > 0) {
        "%d:%02d:%02d".format(hours, minutes, secs)
    } else {
        "%d:%02d".format(minutes, secs)
    }
}

fun formatResolution(width: Int?, height: Int?): String {
    if (width == null || height == null) return "画质未知"
    val label = when {
        height >= 2160 -> "4K"
        height >= 1080 -> "1080p"
        height >= 720 -> "720p"
        else -> "${height}p"
    }
    return "$width×$height · $label"
}
