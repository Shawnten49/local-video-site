package com.shawn.localvideotv.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyGridState
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed as gridItemsIndexed
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.snapshotFlow
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.shawn.localvideotv.data.VideoCard
import com.shawn.localvideotv.ui.adaptive.rememberLayoutEnvironment
import com.shawn.localvideotv.ui.adaptive.tvOverscan
import com.shawn.localvideotv.ui.components.ErrorPanel
import com.shawn.localvideotv.ui.components.TvHeroCarousel
import com.shawn.localvideotv.ui.components.FocusableButton
import com.shawn.localvideotv.ui.components.GlassSurface
import com.shawn.localvideotv.ui.components.HeroCarousel
import com.shawn.localvideotv.ui.components.VideoPosterCard
import com.shawn.localvideotv.ui.theme.Accent
import com.shawn.localvideotv.ui.theme.Background
import com.shawn.localvideotv.ui.theme.TextPrimary
import com.shawn.localvideotv.ui.theme.TextSecondary
import kotlinx.coroutines.delay

@Composable
fun LibraryScreen(
    cards: List<VideoCard>,
    total: Int,
    loading: Boolean,
    loadingMore: Boolean,
    error: String?,
    gridState: LazyGridState,
    assetUrl: (String?) -> String?,
    onOpenDetail: (Int) -> Unit,
    onLoadMore: () -> Unit,
    onRetry: () -> Unit,
    onSettings: () -> Unit,
    requestInitialFocus: Boolean,
) {
    val environment = rememberLayoutEnvironment()
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Background)
            .tvOverscan(environment),
    ) {
        when {
            environment.isTelevision -> TvLibrary(
                cards = cards,
                total = total,
                loading = loading,
                loadingMore = loadingMore,
                error = error,
                gridState = gridState,
                assetUrl = assetUrl,
                onOpenDetail = onOpenDetail,
                onLoadMore = onLoadMore,
                onRetry = onRetry,
                onSettings = onSettings,
                requestInitialFocus = requestInitialFocus,
            )
            environment.isPhonePortrait -> PhonePortraitLibrary(
                cards = cards,
                total = total,
                loading = loading,
                loadingMore = loadingMore,
                error = error,
                gridState = gridState,
                assetUrl = assetUrl,
                onOpenDetail = onOpenDetail,
                onLoadMore = onLoadMore,
                onRetry = onRetry,
                onSettings = onSettings,
            )
            else -> PhoneLandscapeLibrary(
                cards = cards,
                total = total,
                loading = loading,
                loadingMore = loadingMore,
                error = error,
                gridState = gridState,
                assetUrl = assetUrl,
                onOpenDetail = onOpenDetail,
                onLoadMore = onLoadMore,
                onRetry = onRetry,
                onSettings = onSettings,
            )
        }
    }
}

@Composable
private fun PhonePortraitLibrary(
    cards: List<VideoCard>,
    total: Int,
    loading: Boolean,
    loadingMore: Boolean,
    error: String?,
    gridState: LazyGridState,
    assetUrl: (String?) -> String?,
    onOpenDetail: (Int) -> Unit,
    onLoadMore: () -> Unit,
    onRetry: () -> Unit,
    onSettings: () -> Unit,
) {
    GridLoadMoreEffect(cards, gridState, onLoadMore)
    LazyVerticalGrid(
        columns = GridCells.Fixed(2),
        state = gridState,
        contentPadding = PaddingValues(
            start = 20.dp,
            top = 12.dp,
            end = 20.dp,
            bottom = 32.dp,
        ),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
        modifier = Modifier.fillMaxSize(),
    ) {
        item(
            key = "header",
            span = { androidx.compose.foundation.lazy.grid.GridItemSpan(maxLineSpan) },
        ) {
            PageHeader(onSettings = onSettings)
        }
        when {
            loading -> item(
                key = "loading",
                span = { androidx.compose.foundation.lazy.grid.GridItemSpan(maxLineSpan) },
            ) {
                LibraryLoading()
            }
            error != null -> item(
                key = "error",
                span = { androidx.compose.foundation.lazy.grid.GridItemSpan(maxLineSpan) },
            ) {
                ErrorPanel(error, onRetry, onSettings)
            }
            cards.isEmpty() -> item(
                key = "empty",
                span = { androidx.compose.foundation.lazy.grid.GridItemSpan(maxLineSpan) },
            ) {
                LibraryEmpty(onSettings)
            }
            else -> {
                item(
                    key = "hero",
                    span = { androidx.compose.foundation.lazy.grid.GridItemSpan(maxLineSpan) },
                ) {
                    HeroCarousel(
                        cards = cards,
                        thumbnailUrl = assetUrl,
                        onOpenDetail = onOpenDetail,
                        modifier = Modifier.fillMaxWidth().aspectRatio(16f / 9f),
                    )
                }
                item(
                    key = "section",
                    span = { androidx.compose.foundation.lazy.grid.GridItemSpan(maxLineSpan) },
                ) {
                    SectionHeader(total)
                }
                gridItemsIndexed(cards, key = { _, card -> card.id }) { index, card ->
                    VideoPosterCard(
                        card = card,
                        thumbnailUrl = assetUrl(card.thumbnailUrl),
                        onClick = { onOpenDetail(card.id) },
                        onFocused = {
                            if (index >= cards.lastIndex - 4) onLoadMore()
                        },
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
                if (loadingMore) {
                    item(
                        key = "loading-more",
                        span = { androidx.compose.foundation.lazy.grid.GridItemSpan(maxLineSpan) },
                    ) {
                        LoadingMore()
                    }
                }
            }
        }
    }
}

@Composable
private fun PhoneLandscapeLibrary(
    cards: List<VideoCard>,
    total: Int,
    loading: Boolean,
    loadingMore: Boolean,
    error: String?,
    gridState: LazyGridState,
    assetUrl: (String?) -> String?,
    onOpenDetail: (Int) -> Unit,
    onLoadMore: () -> Unit,
    onRetry: () -> Unit,
    onSettings: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 24.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        PageHeader(onSettings = onSettings)
        when {
            loading -> LibraryLoading(Modifier.weight(1f))
            error != null -> Box(
                modifier = Modifier.fillMaxSize(),
                contentAlignment = Alignment.Center,
            ) {
                ErrorPanel(error, onRetry, onSettings)
            }
            cards.isEmpty() -> LibraryEmpty(onSettings)
            else -> Row(
                modifier = Modifier.fillMaxSize(),
                horizontalArrangement = Arrangement.spacedBy(20.dp),
            ) {
                HeroCarousel(
                    cards = cards,
                    thumbnailUrl = assetUrl,
                    onOpenDetail = onOpenDetail,
                    modifier = Modifier.weight(0.42f).fillMaxHeight(),
                )
                Column(
                    modifier = Modifier.weight(0.58f).fillMaxHeight(),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    LazyVerticalGrid(
                        columns = GridCells.Fixed(2),
                        state = gridState,
                        contentPadding = PaddingValues(bottom = 20.dp),
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp),
                        modifier = Modifier.fillMaxSize(),
                    ) {
                        item(
                            key = "section",
                            span = { androidx.compose.foundation.lazy.grid.GridItemSpan(maxLineSpan) },
                        ) {
                            SectionHeader(total, compact = true)
                        }
                        gridItemsIndexed(cards, key = { _, card -> card.id }) { index, card ->
                            VideoPosterCard(
                                card = card,
                                thumbnailUrl = assetUrl(card.thumbnailUrl),
                                onClick = { onOpenDetail(card.id) },
                                onFocused = {
                                    if (index >= cards.lastIndex - 4) onLoadMore()
                                },
                                modifier = Modifier.fillMaxWidth(),
                            )
                        }
                        if (loadingMore) {
                            item {
                                LoadingMore()
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun TvLibrary(
    cards: List<VideoCard>,
    total: Int,
    loading: Boolean,
    loadingMore: Boolean,
    error: String?,
    gridState: LazyGridState,
    assetUrl: (String?) -> String?,
    onOpenDetail: (Int) -> Unit,
    onLoadMore: () -> Unit,
    onRetry: () -> Unit,
    onSettings: () -> Unit,
    requestInitialFocus: Boolean,
) {
    val heroFocus = remember { FocusRequester() }
    if (requestInitialFocus) {
        LaunchedEffect(cards.size) {
            if (cards.isNotEmpty()) {
                repeat(6) {
                    withFrameNanos { }
                    runCatching { heroFocus.requestFocus() }
                    delay(100L)
                }
            }
        }
    }
    GridLoadMoreEffect(cards, gridState, onLoadMore)

    BoxWithConstraints(modifier = Modifier.fillMaxSize()) {
        val columns = when {
            maxWidth >= 1600.dp -> 5
            maxWidth >= 1200.dp -> 4
            maxWidth >= 900.dp -> 3
            else -> 2
        }
        val heroHeight = minOf(
            440.dp,
            (maxHeight * 0.46f).coerceAtLeast(260.dp),
        )
        LazyVerticalGrid(
            columns = GridCells.Fixed(columns),
            state = gridState,
            contentPadding = PaddingValues(
                start = 0.dp,
                top = 8.dp,
                end = 16.dp,
                bottom = 96.dp,
            ),
            horizontalArrangement = Arrangement.spacedBy(20.dp),
            verticalArrangement = Arrangement.spacedBy(24.dp),
            modifier = Modifier.fillMaxSize(),
        ) {
            item(
                key = "header",
                span = { androidx.compose.foundation.lazy.grid.GridItemSpan(maxLineSpan) },
            ) {
                PageHeader(onSettings = onSettings)
            }
            when {
                loading -> item(
                    key = "loading",
                    span = { androidx.compose.foundation.lazy.grid.GridItemSpan(maxLineSpan) },
                ) {
                    LibraryLoading()
                }
                error != null -> item(
                    key = "error",
                    span = { androidx.compose.foundation.lazy.grid.GridItemSpan(maxLineSpan) },
                ) {
                    ErrorPanel(error, onRetry, onSettings)
                }
                cards.isEmpty() -> item(
                    key = "empty",
                    span = { androidx.compose.foundation.lazy.grid.GridItemSpan(maxLineSpan) },
                ) {
                    LibraryEmpty(onSettings)
                }
                else -> {
                    item(
                        key = "hero",
                        span = { androidx.compose.foundation.lazy.grid.GridItemSpan(maxLineSpan) },
                    ) {
                        TvHeroCarousel(
                            cards = cards,
                            thumbnailUrl = assetUrl,
                            onOpenDetail = onOpenDetail,
                            modifier = Modifier.fillMaxWidth().height(heroHeight),
                            actionFocusRequester = heroFocus,
                        )
                    }
                    item(
                        key = "section",
                        span = { androidx.compose.foundation.lazy.grid.GridItemSpan(maxLineSpan) },
                    ) {
                        SectionHeader(total, compact = true)
                    }
                    gridItemsIndexed(cards, key = { _, card -> card.id }) { _, card ->
                        VideoPosterCard(
                            card = card,
                            thumbnailUrl = assetUrl(card.thumbnailUrl),
                            onClick = { onOpenDetail(card.id) },
                            onFocused = {},
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                    if (loadingMore) {
                        item(
                            key = "loading-more",
                            span = { androidx.compose.foundation.lazy.grid.GridItemSpan(maxLineSpan) },
                        ) {
                            LoadingMore()
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun PageHeader(
    onSettings: (() -> Unit)?,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Text(
            text = "LOCAL VIDEO",
            color = TextPrimary,
            fontSize = 20.sp,
            fontWeight = FontWeight.Black,
            letterSpacing = 0.8.sp,
            modifier = Modifier.weight(1f),
        )
        if (onSettings != null) {
            FocusableButton(
                text = "设置",
                onClick = onSettings,
                compact = true,
                modifier = Modifier.width(76.dp),
            )
        }
    }
}

@Composable
private fun SectionHeader(total: Int, compact: Boolean = false) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(
            text = "全部视频",
            color = TextPrimary,
            fontSize = if (compact) 20.sp else 22.sp,
            fontWeight = FontWeight.Bold,
        )
        if (total > 0) {
            Text(
                text = "共 $total 部",
                color = TextSecondary,
                fontSize = 13.sp,
            )
        }
    }
}

@Composable
private fun LibraryLoading(modifier: Modifier = Modifier) {
    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        GlassSurface(
            modifier = Modifier.fillMaxWidth().height(220.dp),
            content = {},
        )
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            repeat(2) {
                GlassSurface(
                    modifier = Modifier.width(168.dp).height(150.dp),
                    content = {},
                )
            }
        }
    }
}

@Composable
private fun LibraryEmpty(onSettings: () -> Unit) {
    GlassSurface(
        modifier = Modifier.fillMaxWidth(),
        contentPadding = PaddingValues(24.dp),
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(18.dp)) {
            Text(
                "当前没有游客可见的视频",
                color = TextPrimary,
                fontSize = 22.sp,
                fontWeight = FontWeight.Bold,
            )
            Text(
                "请确认视频站的游客访问权限和服务器地址。",
                color = TextSecondary,
                fontSize = 15.sp,
            )
            FocusableButton("检查服务器设置", onSettings)
        }
    }
}

@Composable
private fun LoadingMore() {
    Box(
        modifier = Modifier
            .width(120.dp)
            .height(56.dp),
        contentAlignment = Alignment.Center,
    ) {
        CircularProgressIndicator(color = Accent, modifier = Modifier.size(24.dp))
    }
}

@Composable
private fun GridLoadMoreEffect(
    cards: List<VideoCard>,
    state: LazyGridState,
    onLoadMore: () -> Unit,
) {
    LaunchedEffect(cards.size, state) {
        snapshotFlow {
            state.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0
        }.collect { lastVisible ->
            val totalItems = state.layoutInfo.totalItemsCount
            if (cards.isNotEmpty() && totalItems > 0 && lastVisible >= totalItems - 4) {
                onLoadMore()
            }
        }
    }
}
