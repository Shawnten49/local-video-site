package com.shawn.localvideotv.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.shawn.localvideotv.ui.adaptive.rememberLayoutEnvironment
import com.shawn.localvideotv.ui.adaptive.tvOverscan
import com.shawn.localvideotv.ui.components.FocusableButton
import com.shawn.localvideotv.ui.components.GlassSurface
import com.shawn.localvideotv.ui.theme.Background
import com.shawn.localvideotv.ui.theme.GlassStrong
import com.shawn.localvideotv.ui.theme.TextPrimary
import com.shawn.localvideotv.ui.theme.TextSecondary
import kotlinx.coroutines.delay

@Composable
fun SettingsScreen(
    serverUrl: String,
    onSave: (String) -> String?,
    onReset: () -> String,
    onBack: () -> Unit,
) {
    var input by remember(serverUrl) { mutableStateOf(serverUrl) }
    var error by remember { mutableStateOf<String?>(null) }
    val environment = rememberLayoutEnvironment()
    val backFocus = remember { FocusRequester() }
    val focusManager = LocalFocusManager.current
    val keyboardController = LocalSoftwareKeyboardController.current

    LaunchedEffect(environment.isTelevision) {
        if (environment.isTelevision) {
            repeat(6) {
                withFrameNanos { }
                runCatching { backFocus.requestFocus() }
                delay(140L)
            }
        }
    }

    fun hideKeyboard() {
        focusManager.clearFocus(force = true)
        keyboardController?.hide()
    }

    fun save() {
        hideKeyboard()
        error = onSave(input)
    }

    fun reset() {
        hideKeyboard()
        input = onReset()
        error = null
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Background)
            .tvOverscan(environment)
            .imePadding(),
    ) {
        val outerPadding = if (environment.isTelevision) 20.dp else 20.dp
        if (environment.isTelevision) {
            Row(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(outerPadding),
                horizontalArrangement = Arrangement.spacedBy(32.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(
                    modifier = Modifier.weight(0.38f),
                    verticalArrangement = Arrangement.spacedBy(24.dp),
                ) {
                    FocusableButton(
                        text = "← 返回",
                        onClick = {
                            hideKeyboard()
                            onBack()
                        },
                        focusRequester = backFocus,
                    )
                    SettingsIntro(compact = false)
                }
                SettingsForm(
                    input = input,
                    error = error,
                    onInputChange = {
                        input = it
                        error = null
                    },
                    onSave = ::save,
                    onReset = ::reset,
                    modifier = Modifier.weight(0.62f).widthIn(max = 760.dp),
                    compactActions = false,
                )
            }
        } else {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .verticalScroll(rememberScrollState())
                    .padding(outerPadding),
                verticalArrangement = Arrangement.spacedBy(20.dp),
            ) {
                FocusableButton(
                    "← 返回",
                    onClick = {
                        hideKeyboard()
                        onBack()
                    },
                    focusRequester = backFocus,
                )
                SettingsIntro(compact = true)
                SettingsForm(
                    input = input,
                    error = error,
                    onInputChange = {
                        input = it
                        error = null
                    },
                    onSave = ::save,
                    onReset = ::reset,
                    modifier = Modifier
                        .align(Alignment.CenterHorizontally)
                        .fillMaxWidth()
                        .widthIn(max = 760.dp),
                    compactActions = environment.isPhonePortrait,
                )
                Spacer(Modifier.height(20.dp))
            }
        }
    }
}

@Composable
private fun SettingsIntro(
    modifier: Modifier = Modifier,
    compact: Boolean,
) {
    Column(
        modifier = modifier,
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Text(
            text = "服务器设置",
            color = TextPrimary,
            fontSize = if (compact) 28.sp else 36.sp,
            lineHeight = if (compact) 34.sp else 44.sp,
            fontWeight = FontWeight.Black,
        )
        Text(
            text = "输入 Mac 上视频站的局域网地址。保存后会重新连接并加载游客可见的视频。",
            color = TextSecondary,
            fontSize = if (compact) 15.sp else 18.sp,
            lineHeight = if (compact) 22.sp else 26.sp,
        )
    }
}

@Composable
private fun SettingsForm(
    input: String,
    error: String?,
    onInputChange: (String) -> Unit,
    onSave: () -> Unit,
    onReset: () -> Unit,
    modifier: Modifier = Modifier,
    compactActions: Boolean,
) {
    GlassSurface(
        modifier = modifier,
        tint = GlassStrong,
        contentPadding = PaddingValues(
            horizontal = if (compactActions) 20.dp else 28.dp,
            vertical = if (compactActions) 20.dp else 28.dp,
        ),
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(18.dp)) {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(
                    text = "服务器地址",
                    color = TextPrimary,
                    fontSize = 22.sp,
                    fontWeight = FontWeight.Bold,
                )
                Text(
                    text = "例如：http://YOUR_LAN_IP:8766/",
                    color = TextSecondary,
                    fontSize = 14.sp,
                )
            }
            OutlinedTextField(
                value = input,
                onValueChange = onInputChange,
                label = { Text("服务器地址") },
                singleLine = true,
                isError = error != null,
                supportingText = error?.let {
                    {
                        Text(
                            text = it,
                            color = androidx.compose.material3.MaterialTheme.colorScheme.error,
                        )
                    }
                },
                modifier = Modifier.fillMaxWidth(),
            )
            if (compactActions) {
                Column(
                    modifier = Modifier.fillMaxWidth(),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    FocusableButton(
                        text = "保存并连接",
                        primary = true,
                        onClick = onSave,
                        modifier = Modifier.fillMaxWidth(),
                    )
                    FocusableButton(
                        text = "恢复默认",
                        onClick = onReset,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            } else {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    FocusableButton(
                        text = "保存并连接",
                        primary = true,
                        onClick = onSave,
                        modifier = Modifier.weight(1.25f),
                    )
                    FocusableButton(
                        text = "恢复默认",
                        onClick = onReset,
                        modifier = Modifier.weight(1f),
                    )
                }
            }
        }
    }
}
