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

    @GET("api/tv/v1/videos/{videoId}")
    suspend fun videoDetail(@Path("videoId") videoId: Int): VideoDetail

    @GET("api/tv/v1/videos/{videoId}/playback")
    suspend fun playback(@Path("videoId") videoId: Int): PlaybackInfo

    @POST("api/tv/v1/videos/{videoId}/prepare")
    suspend fun prepare(@Path("videoId") videoId: Int): PlaybackInfo
}
