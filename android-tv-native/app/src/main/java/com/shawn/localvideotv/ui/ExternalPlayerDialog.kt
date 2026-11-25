package com.shawn.localvideotv.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.compose.runtime.withFrameNanos
import com.shawn.localvideotv.player.ExternalPlaybackTarget
import com.shawn.localvideotv.ui.components.FocusableButton
import com.shawn.localvideotv.ui.theme.Panel
import com.shawn.localvideotv.ui.theme.TextPrimary
import com.shawn.localvideotv.ui.theme.TextSecondary

data class ExternalPlayerDialogState(
    val title: String,
    val message: String,
    val target: ExternalPlaybackTarget? = null,
)

@Composable
fun ExternalPlayerDialog(
    state: ExternalPlayerDialogState?,
    onChooseOtherPlayer: (ExternalPlaybackTarget) -> Unit,
    onDismiss: () -> Unit,
) {
    if (state == null) return
    val primaryFocus = remember(state) { FocusRequester() }
    LaunchedEffect(state) {
        withFrameNanos { }
        runCatching { primaryFocus.requestFocus() }
    }
    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        Column(
            modifier = Modifier
                .widthIn(max = 620.dp)
                .background(Panel, RoundedCornerShape(18.dp))
                .padding(28.dp),
            verticalArrangement = Arrangement.spacedBy(18.dp),
        ) {
            Text(
                text = state.title,
                color = TextPrimary,
                fontSize = 24.sp,
                fontWeight = FontWeight.Bold,
            )
            Text(
                text = state.message,
                color = TextSecondary,
                fontSize = 15.sp,
                lineHeight = 23.sp,
            )
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp, Alignment.End),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (state.target != null) {
                    FocusableButton(
                        text = "选择其他播放器",
                        onClick = { onChooseOtherPlayer(state.target) },
                        focusRequester = primaryFocus,
                    )
                }
                FocusableButton(
                    text = if (state.target == null) "关闭" else "取消",
                    onClick = onDismiss,
                    primary = state.target == null,
                    focusRequester = if (state.target == null) primaryFocus else null,
                )
            }
        }
    }
}
