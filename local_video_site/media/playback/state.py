from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from local_video_site.core import config
from local_video_site import database
from local_video_site.media.playback.engine import ensure_playback_technical_info, playback_cache_reason, prepared_playback_path, requires_playback_cache, requires_transcode
from local_video_site.storage.volumes import resolve_video_storage


def _unavailable_state(
    video_id: int,
    state: str,
    title: str,
    message: str,
    action_text: str = "",
    retryable: bool = False,
    storage_label: str = "",
) -> Dict:
    return {
        "video_id": int(video_id),
        "state": state,
        "playable": False,
        "reason_code": state,
        "storage_kind": "",
        "storage_label": storage_label,
        "title": title,
        "message": message,
        "action_text": action_text,
        "retryable": retryable,
        "needs_cache": False,
        "cache_ready": False,
        "playback_url": None,
        "engine": "",
        "engine_reason": "",
    }


def _movi_enabled(identity=None) -> bool:
    mode = config.movi_player_mode()
    if mode == "off":
        return False
    if mode == "auto":
        return True
    return bool(identity is not None and identity.is_admin)


def _movi_candidate(video: Dict) -> str:
    extension = str(video.get("extension") or "").lower()
    video_codec = str(video.get("video_codec") or "").lower()
    audio_codec = str(video.get("audio_codec") or "").lower()
    if requires_transcode(video):
        return ""
    if extension == ".mkv":
        return "matroska_container"
    if extension in {".avi", ".ts", ".mts", ".m2ts"}:
        return "non_native_container"
    if audio_codec in {"ac3", "eac3", "dts", "truehd", "mlp"}:
        return "unsupported_audio_codec"
    if not video_codec:
        return "unknown_codec"
    return ""


def _movi_needed_for_codecs(video: Dict) -> bool:
    video_codec = str(video.get("video_codec") or "").lower()
    audio_codec = str(video.get("audio_codec") or "").lower()
    return (
        video_codec in {"hevc", "h265", "vvc", "h266"}
        or audio_codec not in {"", "aac", "mp3"}
    )


def get_playback_state(
    video_id: int,
    identity: Optional[object] = None,
) -> Dict:
    video = database.get_video(video_id)
    if video is None:
        admin_video = database.get_video_admin(video_id)
        if admin_video and admin_video.get("is_deleted"):
            return _unavailable_state(
                video_id,
                "deleted",
                "视频已删除",
                "该视频已从视频库中删除，无法播放。",
            )
        return _unavailable_state(
            video_id,
            "not_found",
            "视频不存在",
            "视频库中没有找到该视频记录。",
        )

    resolution = resolve_video_storage(video)
    storage_label = str(
        (resolution.get("volume") or {}).get("label") or ""
    )
    if resolution["state"] == "external_offline":
        label = storage_label or "外置硬盘"
        return {
            **_unavailable_state(
                video_id,
                "external_offline",
                "视频位于当前离线的外置硬盘",
                "该视频保存在外置硬盘“{}”，当前未连接，因此无法播放。".format(
                    label
                ),
                "请连接“{}”后点击“重新检测”".format(label),
                retryable=True,
                storage_label=storage_label,
            ),
            "storage_kind": "external",
        }
    if resolution["state"] == "file_missing":
        if resolution["storage_kind"] == "external":
            title = "外置硬盘已连接，但视频文件缺失"
            message = (
                "外置硬盘“{}”已连接，但没有找到该视频文件。".format(
                    storage_label or "外置硬盘"
                )
            )
        else:
            title = "视频文件不存在"
            message = "视频记录仍存在，但本地存储中没有找到对应文件。"
        return {
            **_unavailable_state(
                video_id,
                "file_missing",
                title,
                message,
                "请检查存储目录后点击“重新检测”",
                retryable=True,
                storage_label=storage_label,
            ),
            "storage_kind": resolution["storage_kind"],
        }

    resolved_video = {
        **video,
        "path": str(resolution["path"]),
    }
    if Path(resolved_video["path"]).suffix.lower() == ".mkv":
        return {
            "video_id": int(video_id),
            "state": "external_player_required",
            "playable": False,
            "reason_code": "mkv_external_only",
            "storage_kind": resolution["storage_kind"],
            "storage_label": storage_label,
            "title": "MKV 视频需要使用 VLC 播放",
            "message": (
                "MKV 将直接使用 VLC 打开，不再生成浏览器播放缓存。"
                "外部播放不会同步应用内播放进度和自动下一集。"
            ),
            "action_text": "请使用 VLC 打开原始视频",
            "retryable": False,
            "needs_cache": False,
            "cache_ready": False,
            "playback_url": None,
            "transcode": False,
            "engine": "vlc",
            "engine_reason": "mkv_external_only",
            "playback_mode": "external_vlc",
            "external_player": "vlc",
        }
    resolved_video = ensure_playback_technical_info(resolved_video)
    if requires_transcode(resolved_video):
        return {
            **_unavailable_state(
                video_id,
                "unsupported_codec",
                "当前仅支持不重新编码的容器转换",
                "该视频编码无法直接封装为浏览器支持的格式。系统不会自动转码视频或音频。",
                "请使用支持该编码的外部播放器打开原文件",
                retryable=False,
                storage_label=storage_label,
            ),
            "storage_kind": resolution["storage_kind"],
        }
    movi_reason = _movi_candidate(resolved_video)
    extension = str(resolved_video.get("extension") or "").lower()
    movi_enabled = _movi_enabled(identity)
    container_mismatch = (
        playback_cache_reason(resolved_video) == "container_mismatch"
    )
    if (
        movi_reason
        and movi_enabled
        and extension != ".mkv"
        and not container_mismatch
    ):
        return {
            "video_id": int(video_id),
            "state": "direct_ready",
            "playable": True,
            "reason_code": "",
            "storage_kind": resolution["storage_kind"],
            "storage_label": storage_label,
            "title": "",
            "message": "",
            "action_text": "",
            "retryable": False,
            "needs_cache": False,
            "cache_ready": False,
            "playback_url": "/media/{}".format(video_id),
            "transcode": False,
            "engine": "movi",
            "engine_reason": movi_reason,
            "playback_mode": "native",
        }
    needs_cache = requires_playback_cache(resolved_video)
    cache_ready = bool(needs_cache and prepared_playback_path(resolved_video))
    engine = (
        "movi"
        if movi_reason and movi_enabled and _movi_needed_for_codecs(resolved_video)
        else "native"
    )
    if needs_cache and not cache_ready:
        transcode = requires_transcode(resolved_video)
        cache_reason = playback_cache_reason(resolved_video)
        container_mismatch = cache_reason == "container_mismatch"
        return {
            "video_id": int(video_id),
            "state": (
                "transcode_required"
                if transcode
                else "cache_required"
            ),
            "playable": False,
            "reason_code": (
                "rmvb_transcode_required"
                if transcode
                else cache_reason
            ),
            "storage_kind": resolution["storage_kind"],
            "storage_label": storage_label,
            "title": (
                "正在将 RMVB 转换为浏览器可播放格式"
                if transcode
                else "正在修复非标准视频容器"
                if container_mismatch
                else "正在准备播放缓存"
            ),
            "message": (
                "该视频为 RealVideo 编码，首次播放需要兼容转码，可能需要一些时间。"
                if transcode
                else "视频扩展名与实际容器不一致，正在无损重封装为标准 MP4。"
                if container_mismatch
                else "视频文件在线，正在生成仅更换容器的 MP4 播放缓存。"
            ),
            "action_text": (
                "转码完成后会自动开始播放"
                if transcode
                else "首次准备可能需要一些时间"
            ),
            "retryable": False,
            "needs_cache": True,
            "cache_ready": False,
            "playback_url": None,
            "transcode": transcode,
            "engine": engine,
            "engine_reason": movi_reason,
            "playback_mode": "native",
        }
    return {
        "video_id": int(video_id),
        "state": "cache_ready" if needs_cache else "direct_ready",
        "playable": True,
        "reason_code": "",
        "storage_kind": resolution["storage_kind"],
        "storage_label": storage_label,
        "title": "",
        "message": "",
        "action_text": "",
        "retryable": False,
        "needs_cache": bool(needs_cache),
        "cache_ready": bool(cache_ready),
        "playback_url": (
            "/playback/{}".format(video_id)
            if needs_cache
            else "/media/{}".format(video_id)
        ),
        "transcode": False,
        "engine": engine,
        "engine_reason": movi_reason,
        "playback_mode": "native",
    }
