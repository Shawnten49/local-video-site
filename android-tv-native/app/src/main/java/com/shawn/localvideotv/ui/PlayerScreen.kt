package com.shawn.localvideotv.ui

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.content.pm.ActivityInfo
import android.graphics.Color as AndroidColor
import android.net.Uri
import android.util.TypedValue
import android.view.LayoutInflater
import android.view.KeyEvent
import android.view.WindowManager
import androidx.activity.compose.BackHandler
import androidx.annotation.OptIn
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.focusable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.displayCutoutPadding
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.requiredHeight
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusProperties
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChange
import androidx.compose.ui.layout.boundsInRoot
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.onClick
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.zIndex
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.C
import androidx.media3.common.TrackSelectionOverride
import androidx.media3.common.Tracks
import androidx.media3.common.VideoSize
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.CaptionStyleCompat
import androidx.media3.ui.PlayerView
import com.shawn.localvideotv.R
import com.shawn.localvideotv.NextEpisodeUi
import com.shawn.localvideotv.data.PlaybackProgressStore
import com.shawn.localvideotv.PlayerSubtitleUi
import com.shawn.localvideotv.PlayerUiState
import com.shawn.localvideotv.ui.components.FocusableButton
import com.shawn.localvideotv.ui.theme.AccentSoft
import com.shawn.localvideotv.ui.theme.GlassStrong
import com.shawn.localvideotv.ui.theme.Panel
import com.shawn.localvideotv.ui.theme.TextPrimary
import com.shawn.localvideotv.ui.theme.TextSecondary
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlin.math.max
import kotlin.math.min

@Composable
@SuppressLint("UnsafeOptInUsageError")
@OptIn(UnstableApi::class)
fun PlayerScreen(
    videoId: Int,
    state: PlayerUiState,
    progressStore: PlaybackProgressStore,
    onExit: () -> Unit,
    onRetry: () -> Unit,
    onPlayNext: () -> Unit,
    onOpenExternal: () -> Unit,
    isTelevision: Boolean,
) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val configuration = LocalConfiguration.current
    val activity = context.findActivity()

    DisposableEffect(activity, isTelevision) {
        activity?.window?.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        activity?.let {
            WindowInsetsControllerCompat(it.window, it.window.decorView)
                .hide(WindowInsetsCompat.Type.systemBars())
        }
        onDispose {
            activity?.window?.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            if (!isTelevision) {
                activity?.let {
                    WindowInsetsControllerCompat(it.window, it.window.decorView)
                        .show(WindowInsetsCompat.Type.systemBars())
                }
            }
        }
    }

    LaunchedEffect(activity, isTelevision, configuration.orientation) {
        activity?.let {
            WindowInsetsControllerCompat(it.window, it.window.decorView)
                .hide(WindowInsetsCompat.Type.systemBars())
        }
    }
    val streamUrl = state.streamUrl
    if (state.loading || streamUrl == null) {
        PlayerLoadState(
            state = state,
            onRetry = onRetry,
            onExit = onExit,
            onOpenExternal = onOpenExternal,
        )
        return
    }

    val player = remember(videoId) {
        ExoPlayer.Builder(context).build()
    }
    val coroutineScope = rememberCoroutineScope()
    val focusRequester = remember { FocusRequester() }
    var isPlaying by remember { mutableStateOf(true) }
    var ended by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var showControls by remember { mutableStateOf(true) }
    var exiting by remember { mutableStateOf(false) }
    var playerReleased by remember { mutableStateOf(false) }
    var showExitCover by remember { mutableStateOf(false) }
    var isScrubbing by remember { mutableStateOf(false) }
    var positionMs by remember { mutableLongStateOf(0L) }
    var bufferedPositionMs by remember { mutableLongStateOf(0L) }
    var durationMs by remember { mutableLongStateOf(0L) }
    var previewPositionMs by remember { mutableLongStateOf(0L) }
    var interactionTick by remember { mutableIntStateOf(0) }
    var feedbackMessage by remember { mutableStateOf<String?>(null) }
    var feedbackTick by remember { mutableIntStateOf(0) }
    var videoWidth by remember { mutableIntStateOf(16) }
    var videoHeight by remember { mutableIntStateOf(9) }
    var timelineBounds by remember { mutableStateOf(Rect.Zero) }
    var playButtonBounds by remember { mutableStateOf(Rect.Zero) }
    var fullscreenButtonBounds by remember { mutableStateOf(Rect.Zero) }

    DisposableEffect(lifecycleOwner, player) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_STOP) {
                player.pause()
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose {
            lifecycleOwner.lifecycle.removeObserver(observer)
        }
    }

    fun markInteraction() {
        showControls = true
        interactionTick++
    }

    fun showFeedback(message: String) {
        feedbackMessage = message
        feedbackTick++
    }

    fun seekBy(
        deltaMs: Long,
        revealControls: Boolean = true,
    ) {
        val requested = player.currentPosition + deltaMs
        val target = if (player.duration > 0L) {
            requested.coerceIn(0L, player.duration)
        } else {
            requested.coerceAtLeast(0L)
        }
        player.seekTo(target)
        positionMs = target
        if (revealControls) {
            markInteraction()
        } else if (showControls) {
            interactionTick++
        }
        showFeedback(if (deltaMs < 0) "«  10 秒" else "10 秒  »")
    }

    fun togglePlayback() {
        markInteraction()
        if (ended) {
            if (state.nextEpisode != null) {
                onPlayNext()
            } else {
                player.seekTo(0L)
                player.play()
            }
        } else if (player.isPlaying) {
            player.pause()
        } else {
            player.play()
        }
    }

    fun requestExitPlayer() {
        if (exiting) return
        exiting = true
        showExitCover = true
        player.pause()
        player.stop()
        player.clearVideoSurface()
        activity?.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
        coroutineScope.launch {
            withFrameNanos { }
            onExit()
        }
    }

    fun toggleFullscreen() {
        if (isTelevision) return
        activity?.requestedOrientation =
            if (configuration.orientation == android.content.res.Configuration.ORIENTATION_LANDSCAPE) {
                ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
            } else {
                ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
            }
    }
    BackHandler {
        requestExitPlayer()
    }

    DisposableEffect(player, streamUrl, state.subtitleTracks) {
        val savedPosition = progressStore.position(videoId)
        var defaultSubtitleSelected = false
        val defaultSubtitle = state.subtitleTracks.firstOrNull {
            it.isDefault
        } ?: state.subtitleTracks.firstOrNull()

        fun selectDefaultSubtitle(tracks: Tracks) {
            if (defaultSubtitleSelected || defaultSubtitle == null) {
                return
            }
            val match = tracks.groups.asSequence()
                .filter { it.type == C.TRACK_TYPE_TEXT }
                .mapNotNull { group ->
                    val trackIndex = (0 until group.length).firstOrNull {
                        group.getTrackFormat(it).id ==
                            defaultSubtitle.id.toString()
                    }
                    if (trackIndex == null) {
                        null
                    } else {
                        group to trackIndex
                    }
                }
                .firstOrNull()
                ?: return
            player.trackSelectionParameters =
                player.trackSelectionParameters.buildUpon()
                    .setTrackTypeDisabled(C.TRACK_TYPE_TEXT, false)
                    .setOverrideForType(
                        TrackSelectionOverride(
                            match.first.mediaTrackGroup,
                            match.second,
                        )
                    )
                    .build()
            defaultSubtitleSelected = true
        }

        val listener = object : Player.Listener {
            override fun onIsPlayingChanged(playing: Boolean) {
                isPlaying = playing
                if (playing) ended = false
            }

            override fun onPlaybackStateChanged(playbackState: Int) {
                durationMs = player.duration.coerceAtLeast(0L)
                positionMs = player.currentPosition.coerceAtLeast(0L)
                bufferedPositionMs = player.bufferedPosition.coerceAtLeast(0L)
                if (playbackState == Player.STATE_ENDED) {
                    ended = true
                    showControls = false
                    progressStore.clear(videoId)
                }
            }

            override fun onVideoSizeChanged(videoSize: VideoSize) {
                if (videoSize.width > 0 && videoSize.height > 0) {
                    videoWidth = videoSize.width
                    videoHeight = videoSize.height
                }
            }

            override fun onTracksChanged(tracks: Tracks) {
                selectDefaultSubtitle(tracks)
            }

            override fun onPlayerError(playerError: PlaybackException) {
                error = "当前设备无法播放该视频：${playerError.errorCodeName}"
                showControls = true
            }
        }
        player.addListener(listener)
        player.trackSelectionParameters =
            player.trackSelectionParameters.buildUpon()
                .setPreferredTextLanguage(defaultSubtitle?.language)
                .setSelectUndeterminedTextLanguage(defaultSubtitle == null)
                .build()
        player.setMediaItem(
            buildMediaItem(
                streamUrl = streamUrl,
                subtitleTracks = state.subtitleTracks,
            )
        )
        if (savedPosition > 5_000L) player.seekTo(savedPosition)
        player.prepare()
        player.playWhenReady = true
        onDispose {
            if (!playerReleased && player.duration > 0L && !ended) {
                progressStore.save(videoId, player.currentPosition)
            }
            if (!playerReleased) {
                playerReleased = true
                player.clearVideoSurface()
                player.removeListener(listener)
                player.release()
            }
        }
    }

    LaunchedEffect(player, videoId) {
        focusRequester.requestFocus()
        while (isActive) {
            durationMs = player.duration.coerceAtLeast(0L)
            bufferedPositionMs = player.bufferedPosition.coerceAtLeast(0L)
            if (!ended && !isScrubbing) {
                positionMs = player.currentPosition.coerceAtLeast(0L)
            }
            if (!ended && player.duration > 0L) {
                progressStore.save(videoId, player.currentPosition)
            }
            delay(500L)
        }
    }

    LaunchedEffect(interactionTick, isPlaying, ended, isScrubbing, error) {
        if (showControls && !ended && !isScrubbing && error == null) {
            delay(if (isPlaying) 2_400L else 3_000L)
            showControls = false
        }
    }

    LaunchedEffect(feedbackTick) {
        if (feedbackMessage != null) {
            delay(750L)
            feedbackMessage = null
        }
    }

    LaunchedEffect(ended) {
        if (!ended) {
            delay(120L)
            runCatching { focusRequester.requestFocus() }
        }
    }

    BoxWithConstraints(
        modifier = Modifier
            .fillMaxSize()
            .background(Color.Black)
            .then(
                if (!ended) {
                    Modifier.focusRequester(focusRequester).focusable()
                } else {
                    Modifier
                }
            )
            .onPreviewKeyEvent { event ->
                if (event.nativeKeyEvent.action != KeyEvent.ACTION_DOWN) {
                    return@onPreviewKeyEvent false
                }
                if (exiting) {
                    return@onPreviewKeyEvent true
                }
                if (ended) {
                    return@onPreviewKeyEvent false
                }
                when (event.nativeKeyEvent.keyCode) {
                    KeyEvent.KEYCODE_DPAD_LEFT,
                    KeyEvent.KEYCODE_MEDIA_REWIND -> {
                        seekBy(-10_000L)
                        true
                    }
                    KeyEvent.KEYCODE_DPAD_RIGHT,
                    KeyEvent.KEYCODE_MEDIA_FAST_FORWARD -> {
                        seekBy(10_000L)
                        true
                    }
                    KeyEvent.KEYCODE_DPAD_CENTER,
                    KeyEvent.KEYCODE_ENTER,
                    KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE -> {
                        togglePlayback()
                        true
                    }
                    KeyEvent.KEYCODE_DPAD_UP,
                    KeyEvent.KEYCODE_DPAD_DOWN -> {
                        markInteraction()
                        true
                    }
                    else -> false
                }
            },
    ) {
        val videoAspect = (
            videoWidth.toFloat() / videoHeight.coerceAtLeast(1).toFloat()
        ).coerceIn(0.25f, 4f)
        val availableWidth = maxWidth.value
        val availableHeight = maxHeight.value
        val fittedWidth = min(availableWidth, availableHeight * videoAspect)
        val fittedHeight = fittedWidth / videoAspect
        val videoBottomGap = ((availableHeight - fittedHeight) / 2f).coerceAtLeast(0f)
        val controlBottomOffset = (videoBottomGap + 12f).dp
        val controlWidth = (fittedWidth - 24f)
            .coerceAtLeast(min(availableWidth - 24f, 280f))
            .coerceAtMost((availableWidth - 24f).coerceAtLeast(1f))
            .dp

        AndroidView(
            factory = { viewContext ->
                (
                    LayoutInflater.from(viewContext)
                        .inflate(R.layout.player_view_texture, null, false)
                        as PlayerView
                ).apply {
                    useController = false
                    resizeMode = AspectRatioFrameLayout.RESIZE_MODE_FIT
                    setShowBuffering(PlayerView.SHOW_BUFFERING_WHEN_PLAYING)
                    subtitleView?.apply {
                        setApplyEmbeddedStyles(false)
                        setApplyEmbeddedFontSizes(false)
                        setFixedTextSize(
                            TypedValue.COMPLEX_UNIT_SP,
                            if (isTelevision) 24f else 18f,
                        )
                        setBottomPaddingFraction(0.08f)
                        setStyle(
                            CaptionStyleCompat(
                                AndroidColor.WHITE,
                                AndroidColor.TRANSPARENT,
                                AndroidColor.TRANSPARENT,
                                CaptionStyleCompat.EDGE_TYPE_NONE,
                                AndroidColor.TRANSPARENT,
                                null,
                            )
                        )
                    }
                    isFocusable = false
                    isClickable = false
                    this.player = player
                }
            },
            modifier = Modifier.fillMaxSize(),
        )

        feedbackMessage?.let {
            FeedbackBubble(
                message = it,
                modifier = Modifier.align(Alignment.Center).zIndex(3f),
            )
        }

        AnimatedVisibility(
            visible = showControls,
            modifier = Modifier.align(Alignment.TopCenter).fillMaxWidth().zIndex(10f),
            enter = fadeIn(animationSpec = tween(200)),
            exit = fadeOut(animationSpec = tween(200)),
        ) {
            PlayerTopBar(
                title = state.title,
                status = when {
                    error != null -> "播放失败"
                    isPlaying -> "正在播放"
                    else -> "已暂停"
                },
                isTelevision = isTelevision,
                onExit = ::requestExitPlayer,
            )
        }

        AnimatedVisibility(
            visible = showControls,
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .width(controlWidth)
                .offset(y = -controlBottomOffset)
                .zIndex(10f),
            enter = fadeIn(animationSpec = tween(200)),
            exit = fadeOut(animationSpec = tween(200)),
        ) {
            PlayerBottomBar(
                positionMs = if (isScrubbing) previewPositionMs else positionMs,
                bufferedPositionMs = bufferedPositionMs,
                durationMs = durationMs,
                isTelevision = isTelevision,
                isScrubbing = isScrubbing,
                isPlaying = isPlaying,
                onTogglePlayback = ::togglePlayback,
                onToggleFullscreen = ::toggleFullscreen,
                onTimelineBoundsChanged = { timelineBounds = it },
                onPlayBoundsChanged = { playButtonBounds = it },
                onFullscreenBoundsChanged = { fullscreenButtonBounds = it },
                onScrubStart = {
                    isScrubbing = true
                    showControls = true
                    interactionTick++
                },
                onScrub = {
                    previewPositionMs = it
                    showControls = true
                },
                onScrubFinished = {
                    player.seekTo(previewPositionMs)
                    positionMs = previewPositionMs
                    isScrubbing = false
                    showControls = true
                    interactionTick++
                    showFeedback("已跳转")
                },
                modifier = Modifier.fillMaxWidth(),
            )
        }

        if (!ended && !exiting) {
            var lastTapAt by remember { mutableLongStateOf(0L) }
            var lastTapX by remember { mutableStateOf(0f) }
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .zIndex(40f)
                    .pointerInput(
                        showControls,
                        timelineBounds,
                        playButtonBounds,
                        fullscreenButtonBounds,
                        isTelevision,
                    ) {
                        awaitEachGesture {
                            val down = awaitFirstDown(requireUnconsumed = false)
                            down.consume()
                            val controlsVisibleAtPress = showControls
                            if (!controlsVisibleAtPress) {
                                markInteraction()
                            }
                            var finalPosition = down.position
                            var moved = false
                            var scrubbing = controlsVisibleAtPress &&
                                timelineBounds.contains(down.position)

                            fun seekAt(x: Float) {
                                val duration = player.duration.coerceAtLeast(0L)
                                if (duration <= 0L) return
                                val edgeTolerance = 12.dp.toPx()
                                val target = when {
                                    x >= timelineBounds.right - edgeTolerance -> duration
                                    x <= timelineBounds.left + edgeTolerance -> 0L
                                    else -> {
                                        val fraction = (
                                            (x - timelineBounds.left) /
                                                timelineBounds.width.coerceAtLeast(1f)
                                            ).coerceIn(0f, 1f)
                                        (duration * fraction).toLong()
                                    }
                                }
                                val resolvedTarget = if (target >= duration) {
                                    duration
                                } else {
                                    target
                                }
                                player.seekTo(resolvedTarget)
                                positionMs = resolvedTarget
                            }

                            if (scrubbing) {
                                isScrubbing = true
                                showControls = true
                                seekAt(down.position.x)
                                interactionTick++
                            }

                            while (true) {
                                val event = awaitPointerEvent()
                                val change = event.changes.firstOrNull {
                                    it.id == down.id
                                } ?: break
                                finalPosition = change.position
                                if (change.positionChange().getDistance() >
                                    viewConfiguration.touchSlop
                                ) {
                                    moved = true
                                }
                                if (scrubbing) {
                                    seekAt(change.position.x)
                                }
                                change.consume()
                                if (!change.pressed) break
                            }

                            if (scrubbing) {
                                seekAt(finalPosition.x)
                                isScrubbing = false
                                showControls = true
                                interactionTick++
                                showFeedback("已跳转")
                                return@awaitEachGesture
                            }
                            if (moved) {
                                return@awaitEachGesture
                            }

                            val now = System.currentTimeMillis()
                            val isDoubleTap =
                                now - lastTapAt in 1..320 &&
                                    kotlin.math.abs(finalPosition.x - lastTapX) <
                                    80.dp.toPx()
                            if (isDoubleTap) {
                                when {
                                    finalPosition.x < size.width / 3f ->
                                        seekBy(-10_000L, revealControls = false)
                                    finalPosition.x > size.width * 2f / 3f ->
                                        seekBy(10_000L, revealControls = false)
                                    else -> markInteraction()
                                }
                                lastTapAt = 0L
                                return@awaitEachGesture
                            }

                            lastTapAt = now
                            lastTapX = finalPosition.x
                            if (!controlsVisibleAtPress) {
                                return@awaitEachGesture
                            }
                            when {
                                playButtonBounds.contains(finalPosition) -> {
                                    togglePlayback()
                                }
                                !isTelevision &&
                                    fullscreenButtonBounds.contains(finalPosition) -> {
                                    toggleFullscreen()
                                }
                                timelineBounds.contains(finalPosition) -> {
                                    seekAt(finalPosition.x)
                                    markInteraction()
                                }
                                finalPosition.y <= 100.dp.toPx() &&
                                    finalPosition.x <= 140.dp.toPx() -> {
                                    requestExitPlayer()
                                }
                                else -> showControls = false
                            }
                        }
                    },
            )
        }

        if (ended) {
            Box(Modifier.fillMaxSize().zIndex(60f)) {
                PlayerEndedPanel(
                    nextEpisode = state.nextEpisode,
                    compact = configuration.screenWidthDp < 600,
                    onPlayNext = onPlayNext,
                    onReplay = {
                        ended = false
                        player.seekTo(0L)
                        player.play()
                        markInteraction()
                    },
                )
            }
        }

        error?.let { message ->
            Box(Modifier.fillMaxSize().zIndex(65f)) {
                PlayerPlaybackErrorPanel(
                    message = message,
                    onOpenExternal = onOpenExternal,
                    onReturn = ::requestExitPlayer,
                )
            }
        }

        if (showExitCover || exiting) {
            Box(Modifier.fillMaxSize().background(Color.Black).zIndex(70f))
        }
    }
}

@OptIn(UnstableApi::class)
private fun buildMediaItem(
    streamUrl: String,
    subtitleTracks: List<PlayerSubtitleUi>,
): MediaItem {
    val builder = MediaItem.Builder().setUri(streamUrl)
    if (subtitleTracks.isNotEmpty()) {
        builder.setSubtitleConfigurations(
            subtitleTracks.map { track ->
                MediaItem.SubtitleConfiguration.Builder(Uri.parse(track.url))
                    .setId(track.id.toString())
                    .setMimeType(track.mimeType)
                    .setLanguage(track.language)
                    .setLabel(track.label)
                    .setRoleFlags(C.ROLE_FLAG_SUBTITLE)
                    .setSelectionFlags(
                        if (track.isDefault) {
                            C.SELECTION_FLAG_DEFAULT
                        } else {
                            0
                        }
                    )
                    .build()
            }
        )
    }
    return builder.build()
}

@Composable
private fun PlayerTopBar(
    title: String,
    status: String,
    isTelevision: Boolean,
    onExit: () -> Unit,
) {
    BoxWithConstraints(
        modifier = Modifier
            .fillMaxWidth()
            .background(
                Brush.verticalGradient(
                    listOf(
                        Color(0xF2121212),
                        Color(0xA0121212),
                        Color.Transparent,
                    )
                )
            )
            .displayCutoutPadding()
            .statusBarsPadding()
            .padding(
                horizontal = if (isTelevision) 30.dp else 18.dp,
                vertical = if (isTelevision) 18.dp else 12.dp,
            ),
    ) {
        if (maxWidth < 600.dp) {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    if (!isTelevision) {
                        TouchPill("← 返回", onExit)
                    }
                    Text(
                        status,
                        color = TextSecondary,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Bold,
                        textAlign = TextAlign.End,
                        modifier = Modifier.weight(1f),
                    )
                }
                Text(
                    text = title,
                    color = TextPrimary,
                    fontSize = 19.sp,
                    fontWeight = FontWeight.Bold,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        } else {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(18.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (!isTelevision) {
                    TouchPill("← 返回", onExit)
                }
                Text(
                    text = title,
                    color = TextPrimary,
                    fontSize = if (isTelevision) 24.sp else 21.sp,
                    fontWeight = FontWeight.Bold,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                Text(
                    status,
                    color = TextSecondary,
                    fontSize = 15.sp,
                    fontWeight = FontWeight.Bold,
                )
            }
        }
    }
}

@Composable
private fun PlayerBottomBar(
    positionMs: Long,
    bufferedPositionMs: Long,
    durationMs: Long,
    isTelevision: Boolean,
    isScrubbing: Boolean,
    isPlaying: Boolean,
    onTogglePlayback: () -> Unit,
    onToggleFullscreen: () -> Unit,
    onTimelineBoundsChanged: (Rect) -> Unit,
    onPlayBoundsChanged: (Rect) -> Unit,
    onFullscreenBoundsChanged: (Rect) -> Unit,
    onScrubStart: () -> Unit,
    onScrub: (Long) -> Unit,
    onScrubFinished: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val timeWidth = if (isTelevision) {
        PlayerControlTimeWidthTv
    } else {
        PlayerControlTimeWidthPhone
    }
    Row(
        modifier = modifier
            .fillMaxWidth()
            .requiredHeight(if (isTelevision) 44.dp else 40.dp)
            .padding(horizontal = 0.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(PlayerControlGap),
    ) {
        ControlIconButton(
            glyph = if (isPlaying) ControlGlyph.Pause else ControlGlyph.Play,
            description = if (isPlaying) "暂停" else "播放",
            onClick = onTogglePlayback,
            onBoundsChanged = onPlayBoundsChanged,
        )
        TimelineScrubber(
            positionMs = positionMs,
            bufferedPositionMs = bufferedPositionMs,
            durationMs = durationMs,
            onScrubStart = onScrubStart,
            onScrub = onScrub,
            onScrubFinished = onScrubFinished,
            onBoundsChanged = onTimelineBoundsChanged,
            modifier = Modifier.weight(1f),
        )
        Text(
            text = formatCompactPlaybackTime(positionMs, durationMs),
            color = if (isScrubbing) AccentSoft else TextSecondary,
            style = TextStyle(
                fontSize = if (isTelevision) 11.sp else 9.sp,
                fontFeatureSettings = "tnum",
                letterSpacing = (-0.15).sp,
                textAlign = TextAlign.End,
            ),
            maxLines = 1,
            softWrap = false,
            modifier = Modifier.width(timeWidth),
        )
        if (!isTelevision) {
            ControlIconButton(
                glyph = ControlGlyph.Fullscreen,
                description = "切换全屏",
                onClick = onToggleFullscreen,
                onBoundsChanged = onFullscreenBoundsChanged,
            )
        }
    }
}

@Composable
private fun PlayerEndedPanel(
    nextEpisode: NextEpisodeUi?,
    compact: Boolean,
    onPlayNext: () -> Unit,
    onReplay: () -> Unit,
) {
    val nextFocus = remember { FocusRequester() }
    val replayFocus = remember { FocusRequester() }
    val focusManager = LocalFocusManager.current
    val rootView = LocalView.current
    var panelFocusAchieved by remember { mutableStateOf(false) }

    LaunchedEffect(nextEpisode?.videoId) {
        repeat(8) {
            delay(150L)
            if (!panelFocusAchieved) {
                rootView.requestFocus()
                runCatching {
                    if (nextEpisode != null) {
                        nextFocus.requestFocus()
                    } else {
                        replayFocus.requestFocus()
                    }
                }
                if (!panelFocusAchieved) {
                    runCatching { focusManager.moveFocus(FocusDirection.Down) }
                }
                if (!panelFocusAchieved) {
                    rootView.dispatchKeyEvent(
                        KeyEvent(KeyEvent.ACTION_DOWN, KeyEvent.KEYCODE_DPAD_DOWN)
                    )
                    rootView.dispatchKeyEvent(
                        KeyEvent(KeyEvent.ACTION_UP, KeyEvent.KEYCODE_DPAD_DOWN)
                    )
                }
            }
        }
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xCC000000))
            .displayCutoutPadding()
            .statusBarsPadding()
            .padding(24.dp),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .widthIn(max = 520.dp)
                .background(GlassStrong, RoundedCornerShape(20.dp))
                .padding(28.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                "播放结束",
                color = TextPrimary,
                fontSize = 27.sp,
                fontWeight = FontWeight.Black,
            )
            if (nextEpisode != null) {
                Text(
                    if (nextEpisode.episodeNumber > 0) {
                        "下一集 · 第 ${nextEpisode.episodeNumber} 集"
                    } else {
                        "下一集"
                    },
                    color = TextSecondary,
                    fontSize = 16.sp,
                )
                Text(
                    nextEpisode.title,
                    color = TextPrimary,
                    fontSize = 20.sp,
                    fontWeight = FontWeight.Bold,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            if (compact) {
                Column(
                    modifier = Modifier.fillMaxWidth(),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    if (nextEpisode != null) {
                        FocusableButton(
                            text = "播放下一集",
                            primary = true,
                            onClick = onPlayNext,
                            focusRequester = nextFocus,
                            modifier = Modifier
                                .fillMaxWidth()
                                .onFocusChanged {
                                    if (it.isFocused) panelFocusAchieved = true
                                },
                        )
                    }
                    FocusableButton(
                        text = "重播",
                        onClick = onReplay,
                        focusRequester = replayFocus,
                        modifier = Modifier
                            .fillMaxWidth()
                            .onFocusChanged {
                                if (it.isFocused) panelFocusAchieved = true
                            },
                    )
                }
            } else {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    if (nextEpisode != null) {
                        FocusableButton(
                            text = "播放下一集",
                            primary = true,
                            onClick = onPlayNext,
                            focusRequester = nextFocus,
                            modifier = Modifier
                                .weight(1f)
                                .onFocusChanged {
                                    if (it.isFocused) panelFocusAchieved = true
                                },
                        )
                    }
                    FocusableButton(
                        text = "重播",
                        onClick = onReplay,
                        focusRequester = replayFocus,
                        modifier = Modifier
                            .weight(1f)
                            .onFocusChanged {
                                if (it.isFocused) panelFocusAchieved = true
                            },
                    )
                }
            }
        }
    }
}

@Composable
private fun PlayerPlaybackErrorPanel(
    message: String,
    onOpenExternal: () -> Unit,
    onReturn: () -> Unit,
) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xCC080A0F)),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            modifier = Modifier
                .widthIn(max = 620.dp)
                .background(Panel, RoundedCornerShape(18.dp))
                .padding(30.dp),
            verticalArrangement = Arrangement.spacedBy(18.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                text = "当前播放器无法播放该视频",
                color = TextPrimary,
                fontSize = 24.sp,
                fontWeight = FontWeight.Bold,
            )
            Text(
                text = message,
                color = TextSecondary,
                fontSize = 14.sp,
                lineHeight = 22.sp,
                textAlign = TextAlign.Center,
            )
            Text(
                text = "可以尝试用 VLC 打开原始 MKV。外部播放不会同步应用内进度。",
                color = TextSecondary,
                fontSize = 13.sp,
                textAlign = TextAlign.Center,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                FocusableButton(
                    text = "用 VLC 播放原片",
                    onClick = onOpenExternal,
                    primary = true,
                )
                FocusableButton(
                    text = "返回",
                    onClick = onReturn,
                )
            }
        }
    }
}

@Composable
private fun PlayerLoadState(
    state: PlayerUiState,
    onRetry: () -> Unit,
    onExit: () -> Unit,
    onOpenExternal: () -> Unit,
) {
    BackHandler {
        onExit()
    }
    Box(Modifier.fillMaxSize().background(Color.Black), contentAlignment = Alignment.Center) {
        if (state.error == null) {
            CircularProgressIndicator(color = Color(0xFFFF4F64))
        } else {
            Column(
                modifier = Modifier.background(Panel).padding(36.dp),
                verticalArrangement = Arrangement.spacedBy(20.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text(state.error, color = TextPrimary, fontSize = 22.sp)
                Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                    FocusableButton(
                        "重试",
                        onClick = onRetry,
                        primary = true,
                    )
                    FocusableButton(
                        "用 VLC 播放原片",
                        onClick = onOpenExternal,
                    )
                    FocusableButton("返回", onClick = onExit)
                }
            }
        }
    }
}

@Composable
private fun TimelineScrubber(
    positionMs: Long,
    bufferedPositionMs: Long,
    durationMs: Long,
    onScrubStart: () -> Unit,
    onScrub: (Long) -> Unit,
    onScrubFinished: () -> Unit,
    onBoundsChanged: (Rect) -> Unit,
    modifier: Modifier = Modifier,
) {
    val safeDuration = durationMs.coerceAtLeast(1L)
    val bufferedFraction = bufferedPositionMs.toFloat().div(safeDuration.toFloat()).coerceIn(0f, 1f)
    val progressFraction = positionMs.toFloat().div(safeDuration.toFloat()).coerceIn(0f, 1f)

    fun scrubAt(x: Float, width: Int) {
        val fraction = (x / width.coerceAtLeast(1).toFloat()).coerceIn(0f, 1f)
        onScrub((safeDuration * fraction).toLong())
    }

    Canvas(
        modifier = modifier
            .height(32.dp)
            .onGloballyPositioned { onBoundsChanged(it.boundsInRoot()) }
            .semantics { contentDescription = "播放进度" }
            .pointerInput(durationMs) {
                awaitEachGesture {
                    val down = awaitFirstDown(requireUnconsumed = false)
                    down.consume()
                    onScrubStart()
                    scrubAt(down.position.x, size.width)
                    try {
                        var event = awaitPointerEvent()
                        while (event.changes.any { it.pressed }) {
                            val change = event.changes.firstOrNull { it.id == down.id }
                            if (change != null && change.pressed) {
                                change.consume()
                                scrubAt(change.position.x, size.width)
                            }
                            event = awaitPointerEvent()
                        }
                    } finally {
                        onScrubFinished()
                    }
                }
            }
    ) {
        val centerY = size.height / 2f
        val trackWidth = size.width
        drawLine(
            color = Color(0xFF343B4A),
            start = androidx.compose.ui.geometry.Offset(0f, centerY),
            end = androidx.compose.ui.geometry.Offset(trackWidth, centerY),
            strokeWidth = 5.dp.toPx(),
            cap = StrokeCap.Round,
        )
        drawLine(
            color = Color(0xFF697386),
            start = androidx.compose.ui.geometry.Offset(0f, centerY),
            end = androidx.compose.ui.geometry.Offset(trackWidth * bufferedFraction, centerY),
            strokeWidth = 5.dp.toPx(),
            cap = StrokeCap.Round,
        )
        drawLine(
            color = Color(0xFFFF4F64),
            start = androidx.compose.ui.geometry.Offset(0f, centerY),
            end = androidx.compose.ui.geometry.Offset(trackWidth * progressFraction, centerY),
            strokeWidth = 5.dp.toPx(),
            cap = StrokeCap.Round,
        )
        drawCircle(
            color = Color.White,
            radius = 7.dp.toPx(),
            center = androidx.compose.ui.geometry.Offset(trackWidth * progressFraction, centerY),
        )
    }
}

private val PlayerControlIconSize = 38.dp
private val PlayerControlGap = 4.dp
private val PlayerControlTimeWidthPhone = 70.dp
private val PlayerControlTimeWidthTv = 96.dp

private enum class ControlGlyph {
    Play,
    Pause,
    Fullscreen,
}

@Composable
private fun ControlIconButton(
    glyph: ControlGlyph,
    description: String,
    onClick: () -> Unit,
    onBoundsChanged: (Rect) -> Unit,
) {
    Box(
        modifier = Modifier
            .size(PlayerControlIconSize)
            .semantics {
                contentDescription = description
                onClick {
                    onClick()
                    true
                }
            }
            .focusProperties { canFocus = false }
            .onGloballyPositioned { onBoundsChanged(it.boundsInRoot()) },
        contentAlignment = Alignment.Center,
    ) {
        PlayerControlGlyph(
            glyph = glyph,
            modifier = Modifier.size(18.dp),
        )
    }
}

@Composable
private fun PlayerControlGlyph(
    glyph: ControlGlyph,
    modifier: Modifier = Modifier,
) {
    Canvas(modifier = modifier) {
        when (glyph) {
            ControlGlyph.Pause -> {
                val barWidth = 2.75.dp.toPx()
                val barHeight = 12.dp.toPx()
                val gap = 3.dp.toPx()
                val totalWidth = barWidth * 2f + gap
                val left = (size.width - totalWidth) / 2f
                val top = (size.height - barHeight) / 2f
                drawRoundRect(
                    color = Color.White,
                    topLeft = Offset(left, top),
                    size = Size(barWidth, barHeight),
                    cornerRadius = CornerRadius(barWidth / 2f),
                )
                drawRoundRect(
                    color = Color.White,
                    topLeft = Offset(left + barWidth + gap, top),
                    size = Size(barWidth, barHeight),
                    cornerRadius = CornerRadius(barWidth / 2f),
                )
            }
            ControlGlyph.Play -> {
                val path = Path().apply {
                    moveTo(4.5.dp.toPx(), 3.5.dp.toPx())
                    lineTo(14.5.dp.toPx(), 9.dp.toPx())
                    lineTo(4.5.dp.toPx(), 14.5.dp.toPx())
                    close()
                }
                drawPath(path, Color.White)
                drawPath(
                    path = path,
                    color = Color.White,
                    style = Stroke(
                        width = 1.2.dp.toPx(),
                        cap = StrokeCap.Round,
                        join = StrokeJoin.Round,
                    ),
                )
            }
            ControlGlyph.Fullscreen -> {
                val inset = 3.dp.toPx()
                val arm = 4.5.dp.toPx()
                val stroke = 1.8.dp.toPx()
                val left = inset
                val right = size.width - inset
                val top = inset
                val bottom = size.height - inset
                drawLine(
                    Color.White,
                    Offset(left, top + arm),
                    Offset(left, top),
                    stroke,
                    StrokeCap.Round,
                )
                drawLine(
                    Color.White,
                    Offset(left, top),
                    Offset(left + arm, top),
                    stroke,
                    StrokeCap.Round,
                )
                drawLine(
                    Color.White,
                    Offset(right - arm, top),
                    Offset(right, top),
                    stroke,
                    StrokeCap.Round,
                )
                drawLine(
                    Color.White,
                    Offset(right, top),
                    Offset(right, top + arm),
                    stroke,
                    StrokeCap.Round,
                )
                drawLine(
                    Color.White,
                    Offset(left, bottom - arm),
                    Offset(left, bottom),
                    stroke,
                    StrokeCap.Round,
                )
                drawLine(
                    Color.White,
                    Offset(left, bottom),
                    Offset(left + arm, bottom),
                    stroke,
                    StrokeCap.Round,
                )
                drawLine(
                    Color.White,
                    Offset(right - arm, bottom),
                    Offset(right, bottom),
                    stroke,
                    StrokeCap.Round,
                )
                drawLine(
                    Color.White,
                    Offset(right, bottom - arm),
                    Offset(right, bottom),
                    stroke,
                    StrokeCap.Round,
                )
            }
        }
    }
}

@Composable
private fun TouchPill(
    label: String,
    onClick: () -> Unit,
) {
    Box(
        modifier = Modifier
            .height(48.dp)
            .background(Color(0xD9283040), RoundedCornerShape(12.dp))
            .focusProperties { canFocus = false }
            .clickable(onClick = onClick)
            .padding(horizontal = 18.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            label,
            color = TextPrimary,
            fontSize = 15.sp,
            fontWeight = FontWeight.Bold,
        )
    }
}

@Composable
private fun FeedbackBubble(
    message: String,
    modifier: Modifier = Modifier,
) {
    Box(
        modifier = modifier
            .background(Color(0xD9090B10), RoundedCornerShape(14.dp))
            .padding(horizontal = 24.dp, vertical = 14.dp),
    ) {
        Text(
            message,
            color = TextPrimary,
            fontSize = 20.sp,
            fontWeight = FontWeight.Bold,
        )
    }
}

private tailrec fun Context.findActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.findActivity()
    else -> null
}

private fun formatPlayerTime(milliseconds: Long): String {
    val totalSeconds = milliseconds.coerceAtLeast(0L) / 1_000L
    val hours = totalSeconds / 3600
    val minutes = (totalSeconds % 3600) / 60
    val seconds = totalSeconds % 60
    return if (hours > 0) {
        "%d:%02d:%02d".format(hours, minutes, seconds)
    } else {
        "%d:%02d".format(minutes, seconds)
    }
}

private fun formatCompactPlaybackTime(
    positionMs: Long,
    durationMs: Long,
): String = "${formatPlayerTime(positionMs)}/${formatPlayerTime(durationMs)}"
