package com.shawn.localvideotv

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.shawn.localvideotv.data.PlaybackProgressStore
import com.shawn.localvideotv.data.ServerSettings
import com.shawn.localvideotv.data.TvRepository
import java.io.IOException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import retrofit2.HttpException

class PlayerViewModel(application: Application) : AndroidViewModel(application) {
    private val settings = ServerSettings(application)
    private var repository = TvRepository(settings.serverUrl.value)
    val progressStore = PlaybackProgressStore(application)

    private val _state = MutableStateFlow(PlayerUiState())
    val state: StateFlow<PlayerUiState> = _state.asStateFlow()

    fun start(videoId: Int) {
        _state.value = PlayerUiState(loading = true)
        viewModelScope.launch {
            runCatching {
                val detail = repository.api.videoDetail(videoId)
                val playback = if (detail.playback.playable) {
                    detail.playback
                } else {
                    repository.api.prepare(videoId)
                }
                val streamUrl = repository.absoluteUrl(playback.streamUrl)
                    ?: throw IllegalStateException(playback.message.ifBlank {
                        "服务器没有返回播放地址"
                    })
                Triple(detail, streamUrl, playback)
            }.onSuccess { (detail, streamUrl, playback) ->
                val currentEpisode = detail.series
                    ?.episodes
                    ?.firstOrNull { it.videoId == detail.id }
                val episodeNumber = currentEpisode?.episodeNumber
                val episodeTitle = currentEpisode?.title?.takeIf { it.isNotBlank() }
                val displayTitle = if (episodeNumber != null) {
                    "%02d %s".format(
                        episodeNumber,
                        episodeTitle ?: "第 $episodeNumber 集",
                    )
                } else {
                    detail.title
                }
                if (!playback.playable) {
                    _state.value = PlayerUiState(
                        loading = false,
                        title = displayTitle,
                        episodeNumber = episodeNumber,
                        episodeTitle = episodeTitle,
                        error = playback.message.ifBlank {
                            "当前视频无法播放"
                        },
                    )
                    return@onSuccess
                }

                val nextId = detail.series?.nextEpisodeVideoId
                val nextEpisode = nextId?.let { id ->
                    detail.series
                        ?.episodes
                        ?.firstOrNull { it.videoId == id }
                        ?.let {
                            NextEpisodeUi(
                                videoId = id,
                                episodeNumber = it.episodeNumber,
                                title = it.title,
                            )
                        }
                        ?: NextEpisodeUi(
                            videoId = id,
                            episodeNumber = 0,
                            title = "下一集",
                        )
                }
                _state.value = PlayerUiState(
                    loading = false,
                    title = displayTitle,
                    episodeNumber = episodeNumber,
                    episodeTitle = episodeTitle,
                    streamUrl = streamUrl,
                    nextEpisode = nextEpisode,
                )
            }.onFailure { error ->
                _state.value = PlayerUiState(
                    loading = false,
                    error = error.userMessage(),
                )
            }
        }
    }

    fun playNext() {
        val next = state.value.nextEpisode?.videoId ?: return
        start(next)
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
