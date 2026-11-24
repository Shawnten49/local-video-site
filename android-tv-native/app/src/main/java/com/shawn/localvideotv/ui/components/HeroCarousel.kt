package com.shawn.localvideotv.ui.components

import androidx.compose.foundation.background
import android.view.KeyEvent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import com.shawn.localvideotv.data.VideoCard
import com.shawn.localvideotv.ui.theme.Accent
import com.shawn.localvideotv.ui.theme.TextPrimary
import com.shawn.localvideotv.ui.theme.TextSecondary
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

@Composable
fun HeroCarousel(
    cards: List<VideoCard>,
    thumbnailUrl: (String?) -> String?,
    onOpenDetail: (Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    val heroCards = cards.take(5)
    if (heroCards.isEmpty()) return
    val pagerState = rememberPagerState(pageCount = { heroCards.size })

    LaunchedEffect(heroCards.size, pagerState) {
        if (heroCards.size <= 1) return@LaunchedEffect
        while (isActive) {
            delay(8_000L)
            if (!pagerState.isScrollInProgress) {
                pagerState.animateScrollToPage(
                    (pagerState.currentPage + 1) % heroCards.size
                )
            }
        }
    }

    Box(modifier = modifier.clip(RoundedCornerShape(16.dp))) {
        HorizontalPager(
            state = pagerState,
            modifier = Modifier.fillMaxSize(),
        ) { page ->
            FeatureHero(
                card = heroCards[page],
                thumbnailUrl = thumbnailUrl(heroCards[page].thumbnailUrl),
                onOpenDetail = onOpenDetail,
                modifier = Modifier.fillMaxSize(),
            )
        }
        Row(
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .padding(bottom = 10.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            repeat(heroCards.size) { index ->
                Box(
                    modifier = Modifier
                        .clip(CircleShape)
                        .background(
                            if (index == pagerState.currentPage) {
                                Accent
                            } else {
                                Color.White.copy(alpha = 0.3f)
                            }
                        )
                        .then(
                            if (index == pagerState.currentPage) {
                                Modifier.width(16.dp).height(5.dp)
                            } else {
                                Modifier.width(5.dp).height(5.dp)
                            }
                        ),
                )
            }
        }
    }
}

@Composable
fun FeatureHero(
    card: VideoCard,
    thumbnailUrl: String?,
    onOpenDetail: (Int) -> Unit,
    modifier: Modifier = Modifier,
    isTelevision: Boolean = false,
    dense: Boolean = false,
    focusable: Boolean = true,
    actionFocusRequester: FocusRequester? = null,
) {
    if (focusable) {
        FocusSurface(
            onClick = { onOpenDetail(card.id) },
            modifier = modifier,
            shape = RoundedCornerShape(16.dp),
            background = Color.Transparent,
            focusedBackground = Color.Transparent,
            idleBorderColor = Color.Transparent,
            idleBorderWidth = 0.dp,
            focusRequester = actionFocusRequester,
        ) {
            HeroContent(
                card = card,
                thumbnailUrl = thumbnailUrl,
                isTelevision = isTelevision,
                dense = dense,
            )
        }
    } else {
        Box(modifier = modifier) {
            HeroContent(
                card = card,
                thumbnailUrl = thumbnailUrl,
                isTelevision = isTelevision,
                dense = dense,
            )
        }
    }
}

@Composable
fun TvHeroCarousel(
    cards: List<VideoCard>,
    thumbnailUrl: (String?) -> String?,
    onOpenDetail: (Int) -> Unit,
    modifier: Modifier = Modifier,
    actionFocusRequester: FocusRequester? = null,
) {
    val heroCards = cards.take(5)
    if (heroCards.isEmpty()) return
    val pagerState = rememberPagerState(pageCount = { heroCards.size })
    val scope = rememberCoroutineScope()

    FocusSurface(
        onClick = {
            heroCards.getOrNull(pagerState.currentPage)?.let { onOpenDetail(it.id) }
        },
        modifier = modifier.onPreviewKeyEvent { event ->
            if (event.nativeKeyEvent.action != KeyEvent.ACTION_DOWN) {
                false
            } else {
                when (event.nativeKeyEvent.keyCode) {
                    KeyEvent.KEYCODE_DPAD_LEFT -> {
                        scope.launch {
                            pagerState.animateScrollToPage(
                                (pagerState.currentPage - 1).coerceAtLeast(0)
                            )
                        }
                        true
                    }
                    KeyEvent.KEYCODE_DPAD_RIGHT -> {
                        scope.launch {
                            pagerState.animateScrollToPage(
                                (pagerState.currentPage + 1)
                                    .coerceAtMost(heroCards.lastIndex)
                            )
                        }
                        true
                    }
                    else -> false
                }
            }
        },
        shape = RoundedCornerShape(16.dp),
        background = Color.Transparent,
        focusedBackground = Color.Transparent,
        idleBorderColor = Color.Transparent,
        idleBorderWidth = 0.dp,
        focusRequester = actionFocusRequester,
    ) {
        Box(Modifier.fillMaxSize()) {
            HorizontalPager(
                state = pagerState,
                modifier = Modifier.fillMaxSize(),
            ) { page ->
                FeatureHero(
                    card = heroCards[page],
                    thumbnailUrl = thumbnailUrl(heroCards[page].thumbnailUrl),
                    onOpenDetail = onOpenDetail,
                    modifier = Modifier.fillMaxSize(),
                    isTelevision = true,
                    focusable = false,
                )
            }
            Row(
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .padding(bottom = 14.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                repeat(heroCards.size) { index ->
                    Box(
                        modifier = Modifier
                            .clip(CircleShape)
                            .background(
                                if (index == pagerState.currentPage) {
                                    Accent
                                } else {
                                    Color.White.copy(alpha = 0.3f)
                                }
                            )
                            .then(
                                if (index == pagerState.currentPage) {
                                    Modifier.width(18.dp).height(6.dp)
                                } else {
                                    Modifier.width(6.dp).height(6.dp)
                                }
                            ),
                    )
                }
            }
        }
    }
}

@Composable
private fun HeroContent(
    card: VideoCard,
    thumbnailUrl: String?,
    isTelevision: Boolean,
    dense: Boolean,
) {
    Box(Modifier.fillMaxSize()) {
            AsyncImage(
                model = thumbnailUrl,
                contentDescription = card.title,
                contentScale = ContentScale.Crop,
                modifier = Modifier.fillMaxSize(),
            )
            Box(
                Modifier
                    .fillMaxSize()
                    .background(
                        Brush.verticalGradient(
                            0f to Color(0x22000000),
                            0.52f to Color(0x55000000),
                            1f to Color(0xEB121212),
                        )
                    )
            )
            Column(
                modifier = Modifier
                    .align(Alignment.BottomStart)
                    .padding(
                        horizontal = when {
                            isTelevision -> 36.dp
                            dense -> 14.dp
                            else -> 20.dp
                        },
                        vertical = when {
                            isTelevision -> 32.dp
                            dense -> 14.dp
                            else -> 18.dp
                        },
                    ),
                verticalArrangement = Arrangement.spacedBy(
                    when {
                        isTelevision -> 10.dp
                        dense -> 5.dp
                        else -> 7.dp
                    }
                ),
            ) {
                val subtitle = listOfNotNull(
                    card.seriesTitle?.takeIf { it.isNotBlank() },
                    card.seasonTitle?.takeIf { it.isNotBlank() },
                ).joinToString(" · ")
                if (subtitle.isNotBlank()) {
                    Text(
                        text = subtitle,
                        color = Color(0xFFFFC1C8),
                        fontSize = when {
                            isTelevision -> 17.sp
                            dense -> 10.sp
                            else -> 12.sp
                        },
                        fontWeight = FontWeight.Bold,
                    )
                }
                Text(
                    text = card.title,
                    color = TextPrimary,
                    fontSize = when {
                        isTelevision -> 38.sp
                        dense -> 21.sp
                        else -> 25.sp
                    },
                    lineHeight = when {
                        isTelevision -> 44.sp
                        dense -> 26.sp
                        else -> 30.sp
                    },
                    fontWeight = FontWeight.Black,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                if (isTelevision) {
                    Text(
                        text = if (card.kind == "season") {
                            "${card.availableEpisodeCount ?: 0}/${card.episodeCount ?: 0} 集可播放"
                        } else {
                            formatDuration(card.durationSeconds)
                        },
                        color = TextSecondary,
                        fontSize = 16.sp,
                    )
                }
            }
    }
}
