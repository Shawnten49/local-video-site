package com.shawn.localvideotv.data

import android.content.Context

class PlaybackProgressStore(context: Context) {
    private val preferences = context.getSharedPreferences("playback_progress", Context.MODE_PRIVATE)

    fun position(videoId: Int): Long = preferences.getLong("video_$videoId", 0L)

    fun save(videoId: Int, positionMs: Long) {
        preferences.edit().putLong("video_$videoId", positionMs.coerceAtLeast(0L)).apply()
    }

    fun clear(videoId: Int) {
        preferences.edit().remove("video_$videoId").apply()
    }
}
