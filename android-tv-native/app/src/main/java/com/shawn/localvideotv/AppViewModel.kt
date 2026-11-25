package com.shawn.localvideotv

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.shawn.localvideotv.data.PlaybackProgressStore
import com.shawn.localvideotv.data.ServerSettings
import com.shawn.localvideotv.data.TvRepository
import com.shawn.localvideotv.data.TvCategoryCard
import com.shawn.localvideotv.data.TvSeriesCard
import com.shawn.localvideotv.data.VideoCard
import com.shawn.localvideotv.data.VideoDetail
import com.shawn.localvideotv.player.ExternalPlaybackTarget
import java.io.IOException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import retrofit2.HttpException

sealed interface AppScreen {
    data object Library : AppScreen
    data object Settings : AppScreen
    data class Category(
        val categoryKey: String,
        val title: String,
    ) : AppScreen
    data class Series(
        val seriesId: Int,
        val categoryKey: String,
        val categoryTitle: String,
        val title: String,
    ) : AppScreen
    data class Detail(val videoId: Int) : AppScreen
}

data class PlayerUiState(
    val loading: Boolean = true,
    val title: String = "",
    val episodeNumber: Int? = null,
    val episodeTitle: String? = null,
    val streamUrl: String? = null,
    val subtitleTracks: List<PlayerSubtitleUi> = emptyList(),
    val nextEpisode: NextEpisodeUi? = null,
    val error: String? = null,
)

data class PlayerSubtitleUi(
    val id: Int,
    val url: String,
    val language: String,
    val label: String,
    val mimeType: String,
    val isDefault: Boolean,
)

data class NextEpisodeUi(
    val videoId: Int,
    val episodeNumber: Int,
    val title: String,
)

data class TvUiState(
    val screen: AppScreen = AppScreen.Library,
    val serverUrl: String = BuildConfig.DEFAULT_SERVER_URL,
    val cards: List<VideoCard> = emptyList(),
    val page: Int = 0,
    val hasNext: Boolean = false,
    val total: Int = 0,
    val libraryLoading: Boolean = true,
    val loadingMore: Boolean = false,
    val libraryError: String? = null,
    val detail: VideoDetail? = null,
    val selectedVideoId: Int? = null,
    val detailLoading: Boolean = false,
    val detailError: String? = null,
    val tvCategories: List<TvCategoryCard> = emptyList(),
    val tvHomeLoading: Boolean = false,
    val tvHomeError: String? = null,
    val tvCategoryKey: String = "",
    val tvCategoryTitle: String = "",
    val tvSeries: List<TvSeriesCard> = emptyList(),
    val tvSeriesPage: Int = 0,
    val tvSeriesHasNext: Boolean = false,
    val tvSeriesTotal: Int = 0,
    val tvSeriesLoading: Boolean = false,
    val tvSeriesLoadingMore: Boolean = false,
    val tvSeriesError: String? = null,
    val tvSeriesDetail: TvSeriesCard? = null,
    val tvSeriesDetailLoading: Boolean = false,
    val tvSeriesDetailError: String? = null,
)

class AppViewModel(application: Application) : AndroidViewModel(application) {
    private val settings = ServerSettings(application)
    val progressStore = PlaybackProgressStore(application)

    private var repository = TvRepository(settings.serverUrl.value)
    private val _state = MutableStateFlow(
        TvUiState(
            serverUrl = settings.serverUrl.value,
            libraryLoading = true,
        )
    )
    val state: StateFlow<TvUiState> = _state.asStateFlow()
    private val navigationStack = ArrayDeque<AppScreen>()
    private var settingsReturnScreen: AppScreen? = null

    init {
        refreshLibrary()
    }

    fun assetUrl(path: String?): String? = repository.absoluteUrl(path)

    suspend fun playbackDetail(videoId: Int): Result<VideoDetail> =
        runCatching { repository.api.videoDetail(videoId) }

    fun selectVideo(videoId: Int) {
        _state.update { it.copy(selectedVideoId = videoId) }
    }

    suspend fun originalPlayback(
        videoId: Int,
        fallbackMimeType: String = "video/x-matroska",
        fallbackTitle: String = "",
    ): Result<ExternalPlaybackTarget> =
        runCatching {
            val info = try {
                repository.api.originalPlayback(videoId)
            } catch (error: HttpException) {
                if (error.code() !in setOf(404, 405)) throw error
                null
            }
            if (info != null) {
                check(info.available) { "原始视频当前不可用" }
                val url = repository.absoluteUrl(info.streamUrl)
                    ?: error("服务器没有返回原始视频地址")
                ExternalPlaybackTarget(
                    url = url,
                    mimeType = info.mimeType,
                    title = info.title,
                )
            } else {
                val url = repository.absoluteUrl("/media/{}".format(videoId))
                    ?: error("服务器地址无效")
                ExternalPlaybackTarget(
                    url = url,
                    mimeType = fallbackMimeType,
                    title = fallbackTitle,
                )
            }
        }

    fun refreshLibrary() {
        _state.update {
            it.copy(
                screen = AppScreen.Library,
                libraryLoading = true,
                libraryError = null,
                cards = emptyList(),
                page = 0,
                hasNext = false,
            )
        }
        viewModelScope.launch {
            runCatching { repository.api.videos(page = 1) }
                .onSuccess { response ->
                    _state.update {
                        it.copy(
                            cards = response.items,
                            page = response.page,
                            hasNext = response.hasNext,
                            total = response.total,
                            libraryLoading = false,
                            libraryError = null,
                        )
                    }
                }
                .onFailure { error ->
                    _state.update {
                        it.copy(
                            libraryLoading = false,
                            libraryError = error.userMessage(),
                        )
                    }
                }
        }
    }

    fun refreshTvHome() {
        _state.update {
            it.copy(tvHomeLoading = true, tvHomeError = null)
        }
        viewModelScope.launch {
            runCatching { repository.api.home() }
                .onSuccess { response ->
                    _state.update {
                        it.copy(
                            tvCategories = response.categories,
                            tvHomeLoading = false,
                            tvHomeError = null,
                        )
                    }
                }
                .onFailure { error ->
                    _state.update {
                        it.copy(
                            tvHomeLoading = false,
                            tvHomeError = error.userMessage(),
                        )
                    }
                }
        }
    }

    fun openTvCategory(categoryKey: String, title: String) {
        navigationStack.addLast(_state.value.screen)
        _state.update {
            it.copy(
                screen = AppScreen.Category(categoryKey, title),
                tvCategoryKey = categoryKey,
                tvCategoryTitle = title,
                tvSeries = emptyList(),
                tvSeriesPage = 0,
                tvSeriesHasNext = false,
                tvSeriesTotal = 0,
                tvSeriesLoading = true,
                tvSeriesLoadingMore = false,
                tvSeriesError = null,
            )
        }
        loadTvSeriesPage(1)
    }

    private fun loadTvSeriesPage(page: Int) {
        val current = _state.value
        viewModelScope.launch {
            runCatching {
                repository.api.series(
                    categoryKey = current.tvCategoryKey,
                    page = page,
                )
            }
                .onSuccess { response ->
                    _state.update {
                        it.copy(
                            tvSeries = if (page == 1) {
                                response.items
                            } else {
                                (it.tvSeries + response.items)
                                    .distinctBy(TvSeriesCard::seriesId)
                            },
                            tvSeriesPage = response.page,
                            tvSeriesHasNext = response.hasNext,
                            tvSeriesTotal = response.total,
                            tvSeriesLoading = false,
                            tvSeriesLoadingMore = false,
                            tvSeriesError = null,
                        )
                    }
                }
                .onFailure { error ->
                    _state.update {
                        it.copy(
                            tvSeriesLoading = false,
                            tvSeriesLoadingMore = false,
                            tvSeriesError = error.userMessage(),
                        )
                    }
                }
        }
    }

    fun loadMoreTvSeries() {
        val current = _state.value
        if (
            current.screen !is AppScreen.Category
            || current.tvSeriesLoading
            || current.tvSeriesLoadingMore
            || !current.tvSeriesHasNext
            || current.tvSeriesPage <= 0
        ) {
            return
        }
        _state.update { it.copy(tvSeriesLoadingMore = true) }
        loadTvSeriesPage(current.tvSeriesPage + 1)
    }

    fun retryTvCategory() {
        val screen = _state.value.screen as? AppScreen.Category ?: return
        _state.update {
            it.copy(
                tvCategoryKey = screen.categoryKey,
                tvCategoryTitle = screen.title,
                tvSeriesLoading = true,
                tvSeriesError = null,
            )
        }
        loadTvSeriesPage(1)
    }

    fun openTvSeries(card: TvSeriesCard) {
        val entryVideoId = card.singleSeasonEntryVideoId
        if (entryVideoId != null) {
            openTvDetail(entryVideoId)
            return
        }
        val category = _state.value.screen as? AppScreen.Category
        navigationStack.addLast(_state.value.screen)
        _state.update {
            it.copy(
                screen = AppScreen.Series(
                    seriesId = card.seriesId,
                    categoryKey = category?.categoryKey.orEmpty(),
                    categoryTitle = category?.title.orEmpty(),
                    title = card.title,
                ),
                tvSeriesDetail = null,
                tvSeriesDetailLoading = true,
                tvSeriesDetailError = null,
            )
        }
        viewModelScope.launch {
            runCatching { repository.api.seriesDetail(card.seriesId) }
                .onSuccess { detail ->
                    _state.update {
                        it.copy(
                            tvSeriesDetail = detail,
                            tvSeriesDetailLoading = false,
                            tvSeriesDetailError = null,
                        )
                    }
                }
                .onFailure { error ->
                    _state.update {
                        it.copy(
                            tvSeriesDetailLoading = false,
                            tvSeriesDetailError = error.userMessage(),
                        )
                    }
                }
        }
    }

    fun retryTvSeriesDetail() {
        val screen = _state.value.screen as? AppScreen.Series ?: return
        _state.update {
            it.copy(tvSeriesDetailLoading = true, tvSeriesDetailError = null)
        }
        viewModelScope.launch {
            runCatching { repository.api.seriesDetail(screen.seriesId) }
                .onSuccess { detail ->
                    _state.update {
                        it.copy(
                            tvSeriesDetail = detail,
                            tvSeriesDetailLoading = false,
                        )
                    }
                }
                .onFailure { error ->
                    _state.update {
                        it.copy(
                            tvSeriesDetailLoading = false,
                            tvSeriesDetailError = error.userMessage(),
                        )
                    }
                }
        }
    }

    fun openTvDetail(videoId: Int) {
        navigationStack.addLast(_state.value.screen)
        _state.update {
            it.copy(
                screen = AppScreen.Detail(videoId),
                detail = null,
                selectedVideoId = videoId,
                detailLoading = true,
                detailError = null,
            )
        }
        viewModelScope.launch {
            runCatching { repository.api.videoDetail(videoId) }
                .onSuccess { detail ->
                    _state.update {
                        it.copy(
                            detail = detail,
                            selectedVideoId = detail.id,
                            detailLoading = false,
                        )
                    }
                }
                .onFailure { error ->
                    _state.update {
                        it.copy(
                            detailLoading = false,
                            detailError = error.userMessage(),
                        )
                    }
                }
        }
    }

    fun loadMore() {
        val current = _state.value
        if (
            current.libraryLoading
            || current.loadingMore
            || !current.hasNext
            || current.page <= 0
        ) {
            return
        }
        _state.update { it.copy(loadingMore = true) }
        viewModelScope.launch {
            runCatching { repository.api.videos(page = current.page + 1) }
                .onSuccess { response ->
                    _state.update {
                        it.copy(
                            cards = (it.cards + response.items).distinctBy(VideoCard::id),
                            page = response.page,
                            hasNext = response.hasNext,
                            total = response.total,
                            loadingMore = false,
                        )
                    }
                }
                .onFailure {
                    _state.update { state -> state.copy(loadingMore = false) }
                }
        }
    }

    fun openDetail(videoId: Int) {
        _state.update {
            it.copy(
                screen = AppScreen.Detail(videoId),
                detail = null,
                selectedVideoId = videoId,
                detailLoading = true,
                detailError = null,
            )
        }
        viewModelScope.launch {
            runCatching { repository.api.videoDetail(videoId) }
                .onSuccess { detail ->
                    _state.update {
                        it.copy(
                            detail = detail,
                            selectedVideoId = detail.id,
                            detailLoading = false,
                            detailError = null,
                        )
                    }
                }
                .onFailure { error ->
                    _state.update {
                        it.copy(
                            detailLoading = false,
                            detailError = error.userMessage(),
                        )
                    }
                }
        }
    }

    fun openSettings() {
        if (_state.value.screen !is AppScreen.Settings) {
            settingsReturnScreen = _state.value.screen
        }
        _state.update { it.copy(screen = AppScreen.Settings) }
    }

    fun backFromSettings() {
        val previous = settingsReturnScreen ?: AppScreen.Library
        settingsReturnScreen = null
        _state.update { it.copy(screen = previous) }
    }

    fun navigateBack() {
        if (_state.value.screen is AppScreen.Settings) {
            backFromSettings()
            return
        }
        val previous = navigationStack.removeLastOrNull()
            ?: AppScreen.Library
        _state.update {
            it.copy(
                screen = previous,
                detailError = if (previous is AppScreen.Detail) {
                    it.detailError
                } else {
                    null
                },
            )
        }
    }

    fun backToLibrary() {
        navigationStack.clear()
        settingsReturnScreen = null
        _state.update {
            it.copy(
                screen = AppScreen.Library,
                detail = null,
                selectedVideoId = null,
                detailError = null,
            )
        }
    }

    fun saveServerUrl(value: String): String? {
        val result = settings.save(value)
        val normalized = result.getOrNull() ?: return result.exceptionOrNull()?.message
        repository = TvRepository(normalized)
        _state.update { it.copy(serverUrl = normalized) }
        refreshLibrary()
        return null
    }

    fun resetServerUrl(): String {
        val url = settings.reset()
        repository = TvRepository(url)
        _state.update { it.copy(serverUrl = url) }
        refreshLibrary()
        return url
    }

    private fun Throwable.userMessage(): String = when (this) {
        is HttpException -> when (code()) {
            404 -> "视频不存在或已被移除"
            409 -> "视频所在的外置存储当前不可用"
            500, 502, 503 -> "服务器暂时无法处理该视频"
            else -> "服务器返回错误 ${code()}"
        }
        is IOException -> "无法连接视频服务器，请检查局域网和服务地址"
        else -> message?.takeIf { it.isNotBlank() } ?: "发生未知错误"
    }
}
