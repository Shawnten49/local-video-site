package com.shawn.localvideotv.ui

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.basicMarquee
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyGridState
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import com.shawn.localvideotv.data.EpisodeInfo
import com.shawn.localvideotv.data.VideoDetail
import com.shawn.localvideotv.ui.adaptive.rememberLayoutEnvironment
import com.shawn.localvideotv.ui.adaptive.tvOverscan
import com.shawn.localvideotv.ui.components.ErrorPanel
import com.shawn.localvideotv.ui.components.FocusableButton
import com.shawn.localvideotv.ui.components.FocusSurface
import com.shawn.localvideotv.ui.theme.Accent
import com.shawn.localvideotv.ui.theme.Background
import com.shawn.localvideotv.ui.theme.Panel
import com.shawn.localvideotv.ui.theme.PanelFocused
import com.shawn.localvideotv.ui.theme.TextPrimary
import com.shawn.localvideotv.ui.theme.TextSecondary
import kotlinx.coroutines.delay

@Composable
fun DetailScreen(
    detail: VideoDetail?,
    selectedVideoId: Int,
    loading: Boolean,
    error: String?,
    posterUrl: String?,
    onPlay: (Int) -> Unit,
    onOpenExternal: (Int) -> Unit,
    onRetry: () -> Unit,
    onSettings: () -> Unit,
    onBack: () -> Unit,
) {
    val environment = rememberLayoutEnvironment()
    if (loading) {
        Box(
            modifier = Modifier.fillMaxSize().background(Background),
            contentAlignment = Alignment.Center,
        ) {
            CircularProgressIndicator(color = Accent)
        }
        return
    }
    if (error != null || detail == null) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(Background)
                .tvOverscan(environment)
                .padding(20.dp),
            contentAlignment = Alignment.Center,
        ) {
            Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
                if (!environment.isTelevision) {
                    DetailReturnRow(onBack)
                }
                ErrorPanel(
                    message = error ?: "视频详情不可用",
                    onRetry = onRetry,
                    onSettings = onSettings,
                )
            }
        }
        return
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(
                Brush.verticalGradient(listOf(Color(0xFF1A1A1A), Background))
            )
            .tvOverscan(environment),
    ) {
        when {
            environment.isTelevision -> TvDetailContent(
                detail = detail,
                selectedVideoId = selectedVideoId,
                posterUrl = posterUrl,
                onPlay = onPlay,
                onOpenExternal = onOpenExternal,
            )
            environment.isPhonePortrait -> PhonePortraitDetail(
                detail = detail,
                selectedVideoId = selectedVideoId,
                posterUrl = posterUrl,
                onPlay = onPlay,
                onOpenExternal = onOpenExternal,
                onBack = onBack,
            )
            else -> PhoneLandscapeDetail(
                detail = detail,
                selectedVideoId = selectedVideoId,
                posterUrl = posterUrl,
                onPlay = onPlay,
                onOpenExternal = onOpenExternal,
                onBack = onBack,
            )
        }
    }
}

@Composable
private fun PhonePortraitDetail(
    detail: VideoDetail,
    selectedVideoId: Int,
    posterUrl: String?,
    onPlay: (Int) -> Unit,
    onOpenExternal: (Int) -> Unit,
    onBack: () -> Unit,
) {
    val episodes = detail.series?.episodes.orEmpty()
    Column(modifier = Modifier.fillMaxSize()) {
        DetailReturnRow(onBack)
        DetailPoster(
            detail = detail,
            posterUrl = posterUrl,
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp)
                .aspectRatio(16f / 9f)
                .clip(RoundedCornerShape(16.dp)),
        )
        DetailName(
            detail = detail,
            episodeCount = episodes.size,
            modifier = Modifier.padding(horizontal = 20.dp, vertical = 14.dp),
        )
        SelectedEpisodeStatus(
            episodes = episodes,
            selectedVideoId = selectedVideoId,
            modifier = Modifier.padding(horizontal = 20.dp),
        )
        if (episodes.isEmpty()) {
            DetailActionButtons(
                detail = detail,
                onPlay = onPlay,
                onOpenExternal = onOpenExternal,
                modifier = Modifier.padding(horizontal = 20.dp),
            )
        } else {
            ExternalPlayButton(
                detail = detail,
                videoId = selectedVideoId,
                onOpenExternal = onOpenExternal,
                modifier = Modifier.padding(horizontal = 20.dp),
            )
            EpisodeGrid(
                detail = detail,
                episodes = episodes,
                selectedVideoId = selectedVideoId,
                onPlay = onPlay,
                columns = 2,
                contentPadding = PaddingValues(
                    start = 0.dp,
                    top = 0.dp,
                    end = 0.dp,
                    bottom = 24.dp,
                ),
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f)
                    .padding(horizontal = 20.dp),
            )
        }
    }
}

@Composable
private fun PhoneLandscapeDetail(
    detail: VideoDetail,
    selectedVideoId: Int,
    posterUrl: String?,
    onPlay: (Int) -> Unit,
    onOpenExternal: (Int) -> Unit,
    onBack: () -> Unit,
) {
    val episodes = detail.series?.episodes.orEmpty()
    Row(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        horizontalArrangement = Arrangement.spacedBy(20.dp),
    ) {
        Box(
            modifier = Modifier
                .weight(0.43f)
                .fillMaxHeight()
                .clip(RoundedCornerShape(16.dp)),
        ) {
            DetailPoster(
                detail = detail,
                posterUrl = posterUrl,
                modifier = Modifier.fillMaxSize(),
            )
            PosterBackButton(
                onBack = onBack,
                modifier = Modifier.align(Alignment.TopStart).padding(16.dp),
            )
        }
        Column(
            modifier = Modifier.weight(0.57f).fillMaxHeight(),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            DetailName(detail = detail, episodeCount = episodes.size)
            SelectedEpisodeStatus(
                episodes = episodes,
                selectedVideoId = selectedVideoId,
            )
            if (episodes.isNotEmpty()) {
                ExternalPlayButton(
                    detail = detail,
                    videoId = selectedVideoId,
                    onOpenExternal = onOpenExternal,
                )
            }
            if (episodes.isEmpty()) {
                DetailActionButtons(
                    detail = detail,
                    onPlay = onPlay,
                    onOpenExternal = onOpenExternal,
                )
            } else {
                EpisodeGrid(
                    detail = detail,
                    episodes = episodes,
                    selectedVideoId = selectedVideoId,
                    onPlay = onPlay,
                    columns = 3,
                    contentPadding = PaddingValues(bottom = 20.dp),
                    modifier = Modifier.fillMaxWidth().weight(1f),
                )
            }
        }
    }
}

@Composable
private fun TvDetailContent(
    detail: VideoDetail,
    selectedVideoId: Int,
    posterUrl: String?,
    onPlay: (Int) -> Unit,
    onOpenExternal: (Int) -> Unit,
) {
    val episodes = detail.series?.episodes.orEmpty()
    if (episodes.size > 1) {
        TvMultiEpisodeDetailContent(
            detail = detail,
            episodes = episodes,
            selectedVideoId = selectedVideoId,
            posterUrl = posterUrl,
            onPlay = onPlay,
        )
        return
    }

    val firstPlayableFocus = remember { FocusRequester() }
    LaunchedEffect(detail.id, episodes.size, selectedVideoId) {
        withFrameNanos { }
        runCatching { firstPlayableFocus.requestFocus() }
        delay(120L)
        runCatching { firstPlayableFocus.requestFocus() }
    }
    Row(
        modifier = Modifier.fillMaxSize(),
        horizontalArrangement = Arrangement.spacedBy(32.dp),
    ) {
        Box(
            modifier = Modifier
                .weight(0.56f)
                .fillMaxHeight()
                .clip(RoundedCornerShape(20.dp)),
        ) {
            DetailPoster(
                detail = detail,
                posterUrl = posterUrl,
                modifier = Modifier.fillMaxSize(),
            )
        }
        Column(
            modifier = Modifier.weight(0.44f).fillMaxHeight(),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            DetailName(detail = detail, episodeCount = 0)
            DetailActionButtons(
                detail = detail,
                onPlay = onPlay,
                onOpenExternal = onOpenExternal,
                focusRequester = firstPlayableFocus,
            )
        }
    }
}

@Composable
private fun TvMultiEpisodeDetailContent(
    detail: VideoDetail,
    episodes: List<EpisodeInfo>,
    selectedVideoId: Int,
    posterUrl: String?,
    onPlay: (Int) -> Unit,
) {
    val initialEpisode = episodes.firstOrNull {
        it.videoId == selectedVideoId
    } ?: episodes.firstOrNull { it.videoId != null }
    var highlightedVideoId by remember(detail.id, selectedVideoId) {
        mutableIntStateOf(initialEpisode?.videoId ?: selectedVideoId)
    }
    val highlightedEpisode = episodes.firstOrNull {
        it.videoId == highlightedVideoId
    } ?: initialEpisode
    val firstEpisodeFocus = remember { FocusRequester() }
    val initialEpisodeIndex = highlightedEpisode?.let(episodes::indexOf) ?: 0
    val gridStartIndex = (initialEpisodeIndex / 3) * 3
    val episodeGridState = rememberLazyGridState(
        initialFirstVisibleItemIndex = gridStartIndex,
    )

    LaunchedEffect(detail.id, episodes.size, selectedVideoId) {
        episodeGridState.scrollToItem(gridStartIndex)
        withFrameNanos { }
        runCatching { firstEpisodeFocus.requestFocus() }
        delay(120L)
        runCatching { firstEpisodeFocus.requestFocus() }
    }

    Column(
        modifier = Modifier.fillMaxSize(),
        verticalArrangement = Arrangement.spacedBy(18.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().weight(0.30f),
            horizontalArrangement = Arrangement.spacedBy(20.dp),
        ) {
            TvSeasonSelectionSummary(
                detail = detail,
                selectedEpisode = highlightedEpisode,
                modifier = Modifier.weight(0.62f).fillMaxHeight(),
            )
            Box(
                modifier = Modifier
                    .weight(0.38f)
                    .fillMaxHeight()
                    .clip(RoundedCornerShape(18.dp))
                    .border(
                        width = 1.dp,
                        color = Color(0x33FFFFFF),
                        shape = RoundedCornerShape(18.dp),
                    ),
            ) {
                DetailPoster(
                    detail = detail,
                    posterUrl = posterUrl,
                    modifier = Modifier.fillMaxSize(),
                )
            }
        }
        TvEpisodeGrid(
            episodes = episodes,
            highlightedVideoId = highlightedVideoId,
            firstEpisodeFocus = firstEpisodeFocus,
            gridState = episodeGridState,
            onHighlight = { highlightedVideoId = it },
            onPlay = onPlay,
            modifier = Modifier.fillMaxWidth().weight(0.70f),
        )
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun TvSeasonSelectionSummary(
    detail: VideoDetail,
    selectedEpisode: EpisodeInfo?,
    modifier: Modifier = Modifier,
) {
    val seasonName = detail.series?.seasonTitle
        ?.takeIf { it.isNotBlank() }
        ?: detail.series?.seriesTitle?.takeIf { it.isNotBlank() }
        ?: detail.title
    val episodeTitle = selectedEpisode?.title
        ?.takeIf { it.isNotBlank() }
        ?: "请选择剧集"
    val shape = RoundedCornerShape(18.dp)

    Row(
        modifier = modifier
            .clip(shape)
            .background(Color(0xA612151B))
            .border(1.dp, Color(0x24FFFFFF), shape)
            .padding(horizontal = 24.dp, vertical = 16.dp),
        horizontalArrangement = Arrangement.spacedBy(16.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            modifier = Modifier
                .width(4.dp)
                .height(56.dp)
                .clip(RoundedCornerShape(2.dp))
                .background(Accent),
        )
        Column(
            modifier = Modifier.weight(1f),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                text = seasonName,
                color = TextPrimary,
                fontSize = 22.sp,
                lineHeight = 28.sp,
                fontWeight = FontWeight.Bold,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            Row(
                horizontalArrangement = Arrangement.spacedBy(10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Box(
                    modifier = Modifier
                        .width(40.dp)
                        .height(30.dp)
                        .clip(RoundedCornerShape(8.dp))
                        .background(Accent),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        text = "%02d".format(
                            selectedEpisode?.episodeNumber ?: 0,
                        ),
                        color = Color.White,
                        fontSize = 14.sp,
                        fontWeight = FontWeight.Bold,
                    )
                }
                Text(
                    text = episodeTitle,
                    color = TextPrimary,
                    fontSize = 16.sp,
                    lineHeight = 21.sp,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                    softWrap = false,
                    overflow = TextOverflow.Clip,
                    modifier = Modifier
                        .weight(1f)
                        .clipToBounds()
                        .basicMarquee(
                            initialDelayMillis = 1000,
                            repeatDelayMillis = 1200,
                            velocity = 36.dp,
                        ),
                )
            }
        }
    }
}

@Composable
private fun TvEpisodeGrid(
    episodes: List<EpisodeInfo>,
    highlightedVideoId: Int,
    firstEpisodeFocus: FocusRequester,
    gridState: LazyGridState,
    onHighlight: (Int) -> Unit,
    onPlay: (Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    LazyVerticalGrid(
        columns = GridCells.Fixed(3),
        contentPadding = PaddingValues(bottom = 12.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
        state = gridState,
        modifier = modifier.fillMaxSize(),
    ) {
        items(episodes, key = { it.episodeNumber }) { episode ->
            TvEpisodeCard(
                episode = episode,
                selected = episode.videoId == highlightedVideoId,
                onHighlight = {
                    episode.videoId?.let(onHighlight)
                },
                onClick = {
                    episode.videoId?.let(onPlay)
                },
                modifier = if (episode.videoId == highlightedVideoId) {
                    Modifier.focusRequester(firstEpisodeFocus)
                } else {
                    Modifier
                },
            )
        }
    }
}

@Composable
private fun TvEpisodeCard(
    episode: EpisodeInfo,
    selected: Boolean,
    onHighlight: () -> Unit,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    FocusSurface(
        onClick = onClick,
        enabled = episode.videoId != null,
        modifier = modifier
            .fillMaxWidth()
            .height(76.dp)
            .semantics {
                contentDescription = "第 %02d 集，%s".format(
                    episode.episodeNumber,
                    episode.title,
                )
            },
        shape = RoundedCornerShape(13.dp),
        background = if (selected) Color(0x24FF4F64) else Panel,
        focusedBackground = PanelFocused,
        idleBorderColor = if (selected) {
            Color(0x99FF4F64)
        } else {
            Color.Transparent
        },
        idleBorderWidth = if (selected) 1.5.dp else 1.dp,
        onFocusChanged = { isFocused ->
            if (isFocused) onHighlight()
        },
    ) {
        Row(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 12.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(11.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                modifier = Modifier
                    .width(42.dp)
                    .height(42.dp)
                    .clip(RoundedCornerShape(10.dp))
                    .background(
                        if (selected) {
                            Color(0x55FF4F64)
                        } else {
                            Color(0xFF242832)
                        }
                    ),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    text = "%02d".format(episode.episodeNumber),
                    color = if (episode.videoId != null) {
                        TextPrimary
                    } else {
                        TextSecondary.copy(alpha = 0.6f)
                    },
                    fontSize = 15.sp,
                    fontWeight = FontWeight.Bold,
                )
            }
            Text(
                text = episode.title,
                color = if (episode.videoId != null) {
                    TextPrimary
                } else {
                    TextSecondary.copy(alpha = 0.6f)
                },
                fontSize = 15.sp,
                lineHeight = 20.sp,
                fontWeight = if (selected) {
                    FontWeight.SemiBold
                } else {
                    FontWeight.Normal
                },
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
        }
    }
}

@Composable
private fun DetailReturnRow(
    onBack: () -> Unit,
    isTelevision: Boolean = false,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(if (isTelevision) 64.dp else 56.dp)
            .padding(horizontal = if (isTelevision) 0.dp else 20.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        FocusableButton(
            text = if (isTelevision) "← 返回" else "←",
            onClick = onBack,
            compact = !isTelevision,
            modifier = Modifier
                .width(if (isTelevision) 124.dp else 48.dp)
                .semantics { contentDescription = "返回" },
        )
    }
}

@Composable
private fun DetailPoster(
    detail: VideoDetail,
    posterUrl: String?,
    modifier: Modifier = Modifier,
) {
    Box(modifier = modifier.background(Color(0xFF1D2230))) {
        AsyncImage(
            model = posterUrl,
            contentDescription = detail.title,
            contentScale = ContentScale.Crop,
            modifier = Modifier.fillMaxSize(),
        )
        Box(
            Modifier
                .fillMaxSize()
                .background(
                    Brush.verticalGradient(
                        listOf(Color.Transparent, Color(0x55121212))
                    )
                )
        )
    }
}

@Composable
private fun EpisodeGrid(
    detail: VideoDetail,
    episodes: List<EpisodeInfo>,
    selectedVideoId: Int,
    onPlay: (Int) -> Unit,
    columns: Int,
    contentPadding: PaddingValues,
    modifier: Modifier = Modifier,
    firstFocusRequester: FocusRequester? = null,
) {
    val targetVideoId = episodes
        .firstOrNull { it.videoId == selectedVideoId && it.playable }
        ?.videoId
        ?: episodes.firstOrNull { it.playable }?.videoId
    LazyVerticalGrid(
        columns = GridCells.Fixed(columns),
        contentPadding = contentPadding,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
        modifier = modifier.fillMaxSize(),
    ) {
        items(episodes, key = { it.episodeNumber }) { episode ->
            EpisodeCard(
                episode = episode,
                selected = episode.videoId == selectedVideoId,
                onClick = {
                    episode.videoId?.takeIf { episode.playable }?.let(onPlay)
                },
                modifier = if (
                    firstFocusRequester != null &&
                    episode.videoId == targetVideoId
                ) {
                    Modifier.focusRequester(firstFocusRequester)
                } else {
                    Modifier
                },
            )
        }
    }
}

@Composable
private fun SelectedEpisodeStatus(
    episodes: List<EpisodeInfo>,
    selectedVideoId: Int,
    modifier: Modifier = Modifier,
) {
    val selected = episodes.firstOrNull {
        it.videoId == selectedVideoId
    } ?: return
    val isTelevision = LocalTvUi.current
    Text(
        text = "当前选择：第 %02d 集 · %s".format(
            selected.episodeNumber,
            selected.title,
        ),
        color = Accent,
        fontSize = if (isTelevision) 14.sp else 12.sp,
        fontWeight = FontWeight.Bold,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
        modifier = modifier.fillMaxWidth(),
    )
}

@Composable
private fun EpisodeCard(
    episode: EpisodeInfo,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val isTelevision = LocalTvUi.current
    FocusSurface(
        onClick = onClick,
        enabled = episode.playable,
        modifier = modifier
            .fillMaxWidth()
            .height(if (isTelevision) 64.dp else 56.dp),
        shape = RoundedCornerShape(12.dp),
        background = if (selected) Color(0x33FF4F64) else Panel,
        focusedBackground = PanelFocused,
        idleBorderColor = if (selected) Accent else Color.Transparent,
        idleBorderWidth = if (selected) 1.5.dp else 1.dp,
    ) {
        Row(
            modifier = Modifier.fillMaxSize().padding(horizontal = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                text = "%02d".format(episode.episodeNumber),
                color = if (episode.playable) TextPrimary else TextSecondary,
                fontSize = if (isTelevision) 16.sp else 14.sp,
                fontWeight = FontWeight.Bold,
                modifier = Modifier.width(24.dp),
            )
            Text(
                text = if (episode.playable) episode.title else "不可播放",
                color = if (episode.playable) TextSecondary else TextSecondary.copy(alpha = 0.55f),
                fontSize = if (isTelevision) 15.sp else 13.sp,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
        }
    }
}

private fun supportsExternalPlayer(detail: VideoDetail): Boolean {
    val extension = detail.extension?.lowercase()
    val videoCodec = detail.videoCodec.lowercase()
    val audioCodec = detail.audioCodec.lowercase()
    return extension == ".mkv" ||
        videoCodec in setOf("hevc", "h265") ||
        audioCodec in setOf("truehd", "eac3", "ac3")
}

@Composable
private fun DetailActionButtons(
    detail: VideoDetail,
    onPlay: (Int) -> Unit,
    onOpenExternal: (Int) -> Unit,
    modifier: Modifier = Modifier,
    focusRequester: FocusRequester? = null,
) {
    Row(
        modifier = modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        SinglePlayButton(
            onClick = { onPlay(detail.id) },
            focusRequester = focusRequester,
        )
        if (supportsExternalPlayer(detail)) {
            ExternalPlayButton(
                detail = detail,
                videoId = detail.id,
                onOpenExternal = onOpenExternal,
            )
        }
    }
}

@Composable
private fun ExternalPlayButton(
    detail: VideoDetail,
    videoId: Int,
    onOpenExternal: (Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    if (!supportsExternalPlayer(detail)) return
    val isTelevision = LocalTvUi.current
    FocusableButton(
        text = "用 VLC 播放原片",
        onClick = { onOpenExternal(videoId) },
        compact = !isTelevision,
        modifier = modifier
            .width(if (isTelevision) 210.dp else 176.dp)
            .height(if (isTelevision) 56.dp else 44.dp)
            .semantics { contentDescription = "用 VLC 播放原始视频" },
    )
}

@Composable
private fun SinglePlayButton(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    focusRequester: FocusRequester? = null,
) {
    val isTelevision = LocalTvUi.current
    FocusableButton(
        text = "▶ 单集",
        onClick = onClick,
        primary = true,
        focusRequester = focusRequester,
        compact = !isTelevision,
        modifier = modifier
            .width(if (isTelevision) 160.dp else 124.dp)
            .height(if (isTelevision) 56.dp else 44.dp),
    )
}

@Composable
private fun DetailName(
    detail: VideoDetail,
    episodeCount: Int,
    modifier: Modifier = Modifier,
) {
    val name = detail.series?.let { series ->
        listOf(series.seriesTitle, series.seasonTitle)
            .filter { it.isNotBlank() }
            .joinToString(" · ")
    }?.takeIf { it.isNotBlank() } ?: detail.title
    Row(
        modifier = modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = name,
            color = TextPrimary,
            fontSize = if (LocalTvUi.current) 26.sp else 19.sp,
            lineHeight = if (LocalTvUi.current) 32.sp else 25.sp,
            fontWeight = FontWeight.Bold,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f),
        )
        if (episodeCount > 0) {
            Text(
                text = "$episodeCount 集",
                color = TextSecondary,
                fontSize = 12.sp,
                maxLines = 1,
            )
        }
    }
}

@Composable
private fun PosterBackButton(
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
    wide: Boolean = false,
) {
    FocusableButton(
        text = if (wide) "← 返回" else "←",
        onClick = onBack,
        compact = !wide,
        modifier = modifier
            .width(if (wide) 124.dp else 48.dp)
            .height(if (wide) 56.dp else 48.dp)
            .semantics { contentDescription = "返回" },
    )
}
