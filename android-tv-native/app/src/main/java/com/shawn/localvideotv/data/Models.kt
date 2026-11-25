package com.shawn.localvideotv.data

import com.google.gson.annotations.SerializedName

data class PingResponse(
    val status: String,
    @SerializedName("api_version") val apiVersion: Int,
)

data class VideoCard(
    val id: Int,
    val kind: String,
    val title: String,
    @SerializedName("duration_seconds") val durationSeconds: Double?,
    val width: Int?,
    val height: Int?,
    @SerializedName("thumbnail_url") val thumbnailUrl: String,
    @SerializedName("series_title") val seriesTitle: String? = null,
    @SerializedName("season_title") val seasonTitle: String? = null,
    @SerializedName("episode_count") val episodeCount: Int? = null,
    @SerializedName("available_episode_count") val availableEpisodeCount: Int? = null,
)

data class VideoPage(
    val items: List<VideoCard>,
    val page: Int,
    @SerializedName("page_size") val pageSize: Int,
    val pages: Int,
    val total: Int,
    @SerializedName("has_next") val hasNext: Boolean,
)

data class PlaybackInfo(
    @SerializedName("video_id") val videoId: Int,
    val state: String,
    val playable: Boolean,
    val message: String,
    val retryable: Boolean,
    @SerializedName("stream_url") val streamUrl: String?,
    @SerializedName("mime_type") val mimeType: String?,
    @SerializedName("supports_range") val supportsRange: Boolean,
    val subtitles: List<SubtitleTrack>? = null,
    @SerializedName("playback_mode") val playbackMode: String? = "native",
    @SerializedName("external_player") val externalPlayer: String? = "",
)

data class SubtitleTrack(
    val id: Int,
    val url: String,
    val language: String,
    val label: String,
    @SerializedName("mime_type") val mimeType: String,
    @SerializedName("default") val isDefault: Boolean = false,
)

data class OriginalPlaybackInfo(
    @SerializedName("video_id") val videoId: Int,
    val available: Boolean,
    val title: String,
    @SerializedName("stream_url") val streamUrl: String,
    @SerializedName("mime_type") val mimeType: String,
    @SerializedName("container_format") val containerFormat: String,
    val extension: String,
    @SerializedName("video_codec") val videoCodec: String,
    @SerializedName("audio_codec") val audioCodec: String,
    val width: Int?,
    val height: Int?,
    @SerializedName("supports_range") val supportsRange: Boolean,
    @SerializedName("external_player") val externalPlayer: String,
)

data class EpisodeInfo(
    @SerializedName("video_id") val videoId: Int?,
    @SerializedName("episode_number") val episodeNumber: Int,
    val title: String,
    val playable: Boolean,
)

data class SeriesInfo(
    val id: Int,
    @SerializedName("series_title") val seriesTitle: String,
    @SerializedName("season_title") val seasonTitle: String,
    val episodes: List<EpisodeInfo>,
    @SerializedName("previous_episode_video_id") val previousEpisodeVideoId: Int?,
    @SerializedName("next_episode_video_id") val nextEpisodeVideoId: Int?,
)

data class VideoDetail(
    val id: Int,
    val title: String,
    @SerializedName("duration_seconds") val durationSeconds: Double?,
    val width: Int?,
    val height: Int?,
    @SerializedName("video_codec") val videoCodec: String,
    @SerializedName("audio_codec") val audioCodec: String,
    val extension: String? = null,
    @SerializedName("container_format") val containerFormat: String? = null,
    @SerializedName("poster_url") val posterUrl: String,
    val series: SeriesInfo?,
    val playback: PlaybackInfo,
)
