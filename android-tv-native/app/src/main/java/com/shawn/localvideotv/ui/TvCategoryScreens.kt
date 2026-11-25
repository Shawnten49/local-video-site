package com.shawn.localvideotv.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
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
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import com.shawn.localvideotv.data.TvCategoryCard
import com.shawn.localvideotv.data.TvSeasonCard
import com.shawn.localvideotv.data.TvSeriesCard
import com.shawn.localvideotv.ui.components.ErrorPanel
import com.shawn.localvideotv.ui.components.FocusSurface
import com.shawn.localvideotv.ui.components.FocusableButton
import com.shawn.localvideotv.ui.adaptive.rememberLayoutEnvironment
import com.shawn.localvideotv.ui.adaptive.tvOverscan
import com.shawn.localvideotv.ui.theme.Accent
import com.shawn.localvideotv.ui.theme.Background
import com.shawn.localvideotv.ui.theme.Panel
import com.shawn.localvideotv.ui.theme.PanelFocused
import com.shawn.localvideotv.ui.theme.TextPrimary
import com.shawn.localvideotv.ui.theme.TextSecondary
import kotlinx.coroutines.delay

private data class CategoryPalette(
    val start: Color,
    val end: Color,
    val glow: Color,
)

private fun categoryPalette(accent: String): CategoryPalette = when (accent) {
    "blue" -> CategoryPalette(
        Color(0xFF122944),
        Color(0xFF09131F),
        Color(0xFF5DA9FF),
    )
    "amber" -> CategoryPalette(
        Color(0xFF3A2814),
        Color(0xFF1B130B),
        Color(0xFFFFB85C),
    )
    "magenta" -> CategoryPalette(
        Color(0xFF3A1734),
        Color(0xFF1B0B19),
        Color(0xFFFF69C8),
    )
    "emerald" -> CategoryPalette(
        Color(0xFF12352C),
        Color(0xFF081B17),
        Color(0xFF59D4A3),
    )
    else -> CategoryPalette(
        Color(0xFF282C35),
        Color(0xFF111319),
        Color(0xFFAAB4C8),
    )
}

@Composable
fun TvHomeScreen(
    categories: List<TvCategoryCard>,
    loading: Boolean,
    error: String?,
    onOpenCategory: (TvCategoryCard) -> Unit,
    onRetry: () -> Unit,
    onSettings: () -> Unit,
) {
    val firstCardFocus = remember { FocusRequester() }
    LaunchedEffect(categories.size) {
        if (categories.isNotEmpty()) {
            withFrameNanos { }
            runCatching { firstCardFocus.requestFocus() }
            delay(120L)
            runCatching { firstCardFocus.requestFocus() }
        }
    }
    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Background)
            .padding(8.dp, 14.dp, 16.dp, 18.dp),
        verticalArrangement = Arrangement.spacedBy(18.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text(
                    text = "TV LIBRARY",
                    color = Accent,
                    fontSize = 13.sp,
                    fontWeight = FontWeight.Bold,
                    letterSpacing = 3.sp,
                )
                Text(
                    text = "我的私人影厅",
                    color = TextPrimary,
                    fontSize = 34.sp,
                    fontWeight = FontWeight.Bold,
                )
                Text(
                    text = "按类型进入视频集，再选择视频季与剧集",
                    color = TextSecondary,
                    fontSize = 15.sp,
                )
            }
            FocusableButton(
                text = "设置",
                onClick = onSettings,
                compact = true,
                modifier = Modifier.width(110.dp).height(48.dp),
            )
        }
        when {
            loading -> Box(
                Modifier.fillMaxWidth().height(310.dp),
                contentAlignment = Alignment.Center,
            ) {
                CircularProgressIndicator(color = Accent)
            }
            error != null -> Box(
                Modifier.fillMaxWidth().height(310.dp),
                contentAlignment = Alignment.Center,
            ) {
                ErrorPanel(error, onRetry, onSettings)
            }
            else -> Row(
                modifier = Modifier.fillMaxWidth().height(310.dp),
                horizontalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                categories.forEachIndexed { index, category ->
                    CategoryVirtualCard(
                        category = category,
                        onClick = { onOpenCategory(category) },
                        focusRequester = if (index == 0) firstCardFocus else null,
                        modifier = Modifier.weight(1f).fillMaxHeight(),
                    )
                }
            }
        }
    }
}

@Composable
private fun CategoryVirtualCard(
    category: TvCategoryCard,
    onClick: () -> Unit,
    focusRequester: FocusRequester?,
    modifier: Modifier = Modifier,
) {
    val palette = categoryPalette(category.accent)
    val shape = RoundedCornerShape(24.dp)
    FocusSurface(
        onClick = onClick,
        modifier = modifier,
        shape = shape,
        background = palette.end,
        focusedBackground = palette.start,
        idleBorderColor = palette.glow.copy(alpha = 0.24f),
        idleBorderWidth = 1.dp,
        focusRequester = focusRequester,
    ) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(
                    Brush.linearGradient(
                        listOf(palette.start, palette.end),
                    )
                ),
        ) {
            Canvas(Modifier.fillMaxSize()) {
                drawCircle(
                    color = palette.glow.copy(alpha = 0.12f),
                    radius = size.minDimension * 0.42f,
                    center = Offset(size.width * 0.82f, size.height * 0.2f),
                )
                drawCircle(
                    color = Color.White.copy(alpha = 0.05f),
                    radius = size.minDimension * 0.24f,
                    center = Offset(size.width * 0.1f, size.height * 0.9f),
                )
                repeat(4) { index ->
                    val y = size.height * (0.24f + index * 0.14f)
                    drawLine(
                        color = Color.White.copy(alpha = 0.055f),
                        start = Offset(size.width * 0.42f, y),
                        end = Offset(size.width, y),
                        strokeWidth = 1.dp.toPx(),
                    )
                }
            }
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(22.dp),
            ) {
                Text(
                    text = category.key.uppercase(),
                    color = palette.glow.copy(alpha = 0.75f),
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Bold,
                    letterSpacing = 2.sp,
                )
                Box(
                    modifier = Modifier
                        .padding(top = 20.dp)
                        .width(44.dp)
                        .height(2.dp)
                        .clip(RoundedCornerShape(999.dp))
                        .background(palette.glow.copy(alpha = 0.32f)),
                )
                Column(
                    modifier = Modifier.weight(1f),
                    verticalArrangement = Arrangement.Bottom,
                ) {
                    Text(
                        text = category.title,
                        color = TextPrimary,
                        fontSize = 24.sp,
                        fontWeight = FontWeight.Bold,
                    )
                    Text(
                        text = "${category.seriesCount} 个视频集",
                        color = TextPrimary.copy(alpha = 0.86f),
                        fontSize = 15.sp,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(
                        text = "${category.seasonCount} 个视频季",
                        color = TextSecondary,
                        fontSize = 12.sp,
                    )
                }
            }
        }
    }
}

@Composable
fun TvSeriesCategoryScreen(
    series: List<TvSeriesCard>,
    loading: Boolean,
    loadingMore: Boolean,
    error: String?,
    assetUrl: (String?) -> String?,
    onOpenSeries: (TvSeriesCard) -> Unit,
    onLoadMore: () -> Unit,
    onRetry: () -> Unit,
) {
    val environment = rememberLayoutEnvironment()
    LazyVerticalGrid(
        columns = GridCells.Fixed(3),
        modifier = Modifier
            .fillMaxSize()
            .tvOverscan(environment),
        contentPadding = PaddingValues(
            start = 18.dp,
            top = 30.dp,
            end = 18.dp,
            bottom = 32.dp,
        ),
        horizontalArrangement = Arrangement.spacedBy(18.dp),
        verticalArrangement = Arrangement.spacedBy(20.dp),
    ) {
        when {
            loading -> item(
                span = { androidx.compose.foundation.lazy.grid.GridItemSpan(maxLineSpan) },
            ) {
                Box(
                    Modifier.fillMaxWidth().height(320.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    CircularProgressIndicator(color = Accent)
                }
            }
            error != null -> item(
                span = { androidx.compose.foundation.lazy.grid.GridItemSpan(maxLineSpan) },
            ) {
                TvInlineError(error, onRetry)
            }
            series.isEmpty() -> item(
                span = { androidx.compose.foundation.lazy.grid.GridItemSpan(maxLineSpan) },
            ) {
                Text(
                    text = "这个分类还没有配置视频集",
                    color = TextSecondary,
                    fontSize = 20.sp,
                    modifier = Modifier.padding(vertical = 80.dp),
                )
            }
            else -> {
                itemsIndexed(
                    items = series,
                    key = { _, item -> item.seriesId },
                ) { index, item ->
                    SeriesCard(
                        series = item,
                        coverUrl = assetUrl(item.coverUrl),
                        onClick = { onOpenSeries(item) },
                        onFocused = {
                            if (index >= series.lastIndex - 3) onLoadMore()
                        },
                    )
                }
            }
        }
        if (loadingMore) {
            item(span = { androidx.compose.foundation.lazy.grid.GridItemSpan(maxLineSpan) }) {
                Box(Modifier.fillMaxWidth().height(72.dp), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator(
                        color = Accent,
                        modifier = Modifier.size(28.dp),
                        strokeWidth = 3.dp,
                    )
                }
            }
        }
    }
}

@Composable
private fun SeriesCard(
    series: TvSeriesCard,
    coverUrl: String?,
    onClick: () -> Unit,
    onFocused: () -> Unit,
) {
    val shape = RoundedCornerShape(18.dp)
    FocusSurface(
        onClick = onClick,
        onFocusChanged = { if (it) onFocused() },
        shape = shape,
        background = Panel,
        focusedBackground = PanelFocused,
        idleBorderColor = Color.White.copy(alpha = 0.07f),
        idleBorderWidth = 1.dp,
    ) {
        Column(Modifier.fillMaxWidth()) {
            Box(
                Modifier
                    .fillMaxWidth()
                    .aspectRatio(16f / 9f)
                    .background(
                        Brush.linearGradient(
                            listOf(Color(0xFF202838), Color(0xFF0D1118))
                        )
                    ),
            ) {
                AsyncImage(
                    model = coverUrl,
                    contentDescription = series.title,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize(),
                )
                Box(
                    Modifier
                        .fillMaxSize()
                        .background(
                            Brush.verticalGradient(
                                listOf(Color.Transparent, Color(0xAA05070A))
                            )
                        ),
                )
                Text(
                    text = "${series.availableSeasonCount} 季",
                    color = Color.White,
                    fontSize = 13.sp,
                    fontWeight = FontWeight.Bold,
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .padding(12.dp)
                        .clip(RoundedCornerShape(999.dp))
                        .background(Color.Black.copy(alpha = 0.55f))
                        .padding(horizontal = 9.dp, vertical = 4.dp),
                )
            }
            Column(Modifier.padding(14.dp)) {
                Text(
                    text = series.title,
                    color = TextPrimary,
                    fontSize = 19.sp,
                    fontWeight = FontWeight.Bold,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    text = "${series.availableEpisodeCount}/${series.episodeCount} 集可播放",
                    color = TextSecondary,
                    fontSize = 12.sp,
                    modifier = Modifier.padding(top = 5.dp),
                )
            }
        }
    }
}

@Composable
fun TvSeriesSeasonsScreen(
    series: TvSeriesCard?,
    loading: Boolean,
    error: String?,
    assetUrl: (String?) -> String?,
    selectedVideoId: Int?,
    onOpenSeason: (TvSeasonCard) -> Unit,
    onRetry: () -> Unit,
) {
    val environment = rememberLayoutEnvironment()
    if (loading) {
        Box(
            Modifier
                .fillMaxSize()
                .tvOverscan(environment)
                .padding(top = 30.dp),
            contentAlignment = Alignment.Center,
        ) {
            CircularProgressIndicator(color = Accent)
        }
        return
    }
    if (error != null || series == null) {
        Box(
            Modifier
                .fillMaxSize()
                .tvOverscan(environment)
                .padding(
                    start = 18.dp,
                    top = 30.dp,
                    end = 18.dp,
                    bottom = 32.dp,
                ),
            contentAlignment = Alignment.Center,
        ) {
            TvInlineError(error ?: "视频集不存在", onRetry)
        }
        return
    }
    LazyVerticalGrid(
        columns = GridCells.Fixed(4),
        modifier = Modifier
            .fillMaxSize()
            .tvOverscan(environment),
        horizontalArrangement = Arrangement.spacedBy(14.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
        contentPadding = PaddingValues(
            start = 18.dp,
            top = 30.dp,
            end = 18.dp,
            bottom = 32.dp,
        ),
    ) {
        itemsIndexed(
            series.seasons,
            key = { _, season -> season.seasonId },
        ) { _, season ->
            SeasonCard(
                season = season,
                coverUrl = assetUrl(season.coverUrl),
                selected = selectedVideoId in setOf(
                    season.entryVideoId,
                ),
                onClick = { onOpenSeason(season) },
            )
        }
    }
}

@Composable
private fun SeasonCard(
    season: TvSeasonCard,
    coverUrl: String?,
    selected: Boolean,
    onClick: () -> Unit,
) {
    FocusSurface(
        onClick = onClick,
        shape = RoundedCornerShape(17.dp),
        background = if (selected) Color(0x2EFF4F64) else Panel,
        focusedBackground = PanelFocused,
        idleBorderColor = if (selected) Accent else Color.White.copy(alpha = 0.06f),
        idleBorderWidth = if (selected) 2.dp else 1.dp,
    ) {
        Column {
            Box(
                Modifier
                    .fillMaxWidth()
                    .aspectRatio(16f / 9f)
                    .background(Color(0xFF151A23)),
            ) {
                AsyncImage(
                    model = coverUrl,
                    contentDescription = season.title,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.fillMaxSize(),
                )
            }
            Column(Modifier.padding(13.dp)) {
                Text(
                    text = season.title,
                    color = TextPrimary,
                    fontSize = 18.sp,
                    fontWeight = FontWeight.Bold,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    text = "${season.availableEpisodeCount}/${season.episodeCount} 集可播放",
                    color = TextSecondary,
                    fontSize = 12.sp,
                    modifier = Modifier.padding(top = 5.dp),
                )
            }
        }
    }
}

@Composable
private fun TvInlineError(message: String, onRetry: () -> Unit) {
    Column(
        modifier = Modifier.fillMaxWidth().padding(vertical = 56.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text(
            text = message,
            color = TextSecondary,
            fontSize = 18.sp,
        )
        FocusableButton(
            text = "重试",
            onClick = onRetry,
            primary = true,
            compact = true,
            modifier = Modifier.width(120.dp).height(46.dp),
        )
    }
}
