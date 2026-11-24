package com.shawn.localvideotv

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.shawn.localvideotv.data.PlaybackProgressStore
import com.shawn.localvideotv.data.ServerSettings
import com.shawn.localvideotv.data.TvRepository
import com.shawn.localvideotv.data.VideoCard
import com.shawn.localvideotv.data.VideoDetail
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
    data class Detail(val videoId: Int) : AppScreen
}

data class PlayerUiState(
    val loading: Boolean = true,
    val title: String = "",
    val episodeNumber: Int? = null,
    val episodeTitle: String? = null,
    val streamUrl: String? = null,
    val nextEpisode: NextEpisodeUi? = null,
    val error: String? = null,
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
    val detailLoading: Boolean = false,
    val detailError: String? = null,
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

    init {
        refreshLibrary()
    }

    fun assetUrl(path: String?): String? = repository.absoluteUrl(path)

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
        _state.update { it.copy(screen = AppScreen.Settings) }
    }

    fun backToLibrary() {
        _state.update {
            it.copy(
                screen = AppScreen.Library,
                detail = null,
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
