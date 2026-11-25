package com.shawn.localvideotv.data

import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query

interface TvApi {
    @GET("api/tv/v1/ping")
    suspend fun ping(): PingResponse

    @GET("api/tv/v1/videos")
    suspend fun videos(
        @Query("page") page: Int,
        @Query("page_size") pageSize: Int = 24,
        @Query("sort") sort: String = "recent",
    ): VideoPage

    @GET("api/tv/v1/home")
    suspend fun home(): TvHomeResponse

    @GET("api/tv/v1/series")
    suspend fun series(
        @Query("category_key") categoryKey: String,
        @Query("page") page: Int,
        @Query("page_size") pageSize: Int = 20,
    ): TvSeriesPage

    @GET("api/tv/v1/series/{seriesId}")
    suspend fun seriesDetail(
        @Path("seriesId") seriesId: Int,
    ): TvSeriesCard

    @GET("api/tv/v1/videos/{videoId}")
    suspend fun videoDetail(@Path("videoId") videoId: Int): VideoDetail

    @GET("api/tv/v1/videos/{videoId}/playback")
    suspend fun playback(@Path("videoId") videoId: Int): PlaybackInfo

    @GET("api/tv/v1/videos/{videoId}/original-playback")
    suspend fun originalPlayback(
        @Path("videoId") videoId: Int,
    ): OriginalPlaybackInfo

    @POST("api/tv/v1/videos/{videoId}/prepare")
    suspend fun prepare(@Path("videoId") videoId: Int): PlaybackInfo
}
