from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Literal, Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from local_video_site import database
from local_video_site.catalog.tv_home import (
    get_tv_series,
    list_tv_series,
    season_cover_file,
    series_cover_file,
    tv_home_categories,
)
from local_video_site.identity.permissions.account import GUEST_IDENTITY
from local_video_site.identity.permissions.content import can_view
from local_video_site.media.ffmpeg import FFmpegBusyError
from local_video_site.media.mime import media_type_for_path
from local_video_site.media.playback.engine import prepare_playback_cache, requires_playback_cache
from local_video_site.media.playback.state import get_playback_state
from local_video_site.storage.volumes import resolve_video_storage


LOGGER = logging.getLogger("local_video_site.web.routes.tv")
router = APIRouter(prefix="/api/tv/v1", tags=["Android TV"])


def _thumbnail_url(video: Dict) -> str:
    version = (
        video.get("cover_updated_at")
        or video.get("updated_at")
        or video.get("mtime_ns")
        or ""
    )
    return "/thumb/{}?v={}".format(
        int(video["id"]),
        quote(str(version), safe=""),
    )


def _video_card(video: Dict) -> Dict:
    kind = "season" if video.get("card_kind") == "season" else "video"
    title = str(
        video.get("display_title")
        or video.get("title")
        or "未命名视频"
    )
    payload = {
        "id": int(video["id"]),
        "kind": kind,
        "title": title,
        "duration_seconds": (
            round(float(video["duration"]), 3)
            if video.get("duration") is not None
            else None
        ),
        "width": video.get("width"),
        "height": video.get("height"),
        "thumbnail_url": _thumbnail_url(video),
    }
    if kind == "season":
        payload.update({
            "series_title": video.get("series_name") or "",
            "season_title": video.get("season_name") or "",
            "episode_count": int(video.get("episode_count") or 0),
            "available_episode_count": int(
                video.get("available_episode_count") or 0
            ),
        })
    return payload


def _visible_video_or_404(video_id: int) -> Dict:
    video = database.get_video(int(video_id))
    if not video or not can_view(GUEST_IDENTITY, video):
        raise HTTPException(status_code=404, detail="视频不存在")
    return video


def _subtitle_tracks(video_id: int) -> List[Dict]:
    tracks = []
    for asset in database.list_video_subtitle_assets(
        int(video_id),
        playback_only=True,
    ):
        if str(asset.get("extension") or "").lower() != "vtt":
            continue
        version = str(asset.get("updated_at") or "")
        tracks.append({
            "id": int(asset["id"]),
            "url": "/subtitle/{}?v={}".format(
                int(asset["id"]),
                quote(version, safe=""),
            ),
            "language": str(asset.get("language") or "und"),
            "label": str(
                asset.get("title")
                or asset.get("language")
                or "字幕"
            ),
            "mime_type": "text/vtt",
            "default": bool(asset.get("default_track")),
        })
    return tracks


def _safe_playback(video_id: int) -> Dict:
    video = database.get_video(int(video_id))
    state = get_playback_state(int(video_id))
    external_only = bool(
        video
        and str(
            video.get("extension")
            or Path(str(video.get("path") or "")).suffix
        ).lower() == ".mkv"
    )
    return {
        "video_id": int(video_id),
        "state": state.get("state") or "playback_failed",
        "title": str(
            state.get("title")
            or (video.get("title") if video else "")
            or ""
        ),
        "playable": bool(state.get("playable")),
        "message": str(state.get("message") or ""),
        "retryable": bool(state.get("retryable")),
        "stream_url": (
            state.get("playback_url")
            if state.get("playable")
            else None
        ),
        "mime_type": (
            media_type_for_path(str(video.get("path") or ""))
            if video
            else "video/mp4"
        ),
        "supports_range": True,
        "subtitles": _subtitle_tracks(video_id),
        "playback_mode": (
            "external_vlc" if external_only else "native"
        ),
        "external_player": "vlc" if external_only else "",
    }


def _season_episodes(video_id: int) -> Optional[Dict]:
    season = database.season_context_for_video(
        int(video_id),
        max_access_level=0,
    )
    if not season:
        return None

    episodes: List[Dict] = []
    for episode in season.get("episodes") or []:
        resolved_id = episode.get("resolved_video_id")
        playable = resolved_id is not None
        number = int(episode.get("episode_number") or 0)
        episodes.append({
            "video_id": (
                int(resolved_id) if resolved_id is not None else None
            ),
            "episode_number": number,
            "title": (
                str(episode.get("video_title") or "").strip()
                or str(episode.get("episode_title") or "").strip()
                or "第 {} 集".format(number)
            ),
            "playable": playable,
        })

    current_index = next(
        (
            index
            for index, item in enumerate(episodes)
            if item["video_id"] == int(video_id)
        ),
        -1,
    )
    previous_id = (
        episodes[current_index - 1]["video_id"]
        if current_index > 0
        else None
    )
    next_id = (
        episodes[current_index + 1]["video_id"]
        if 0 <= current_index < len(episodes) - 1
        else None
    )
    return {
        "id": int(season["id"]),
        "series_id": int(season.get("series_id") or 0),
        "season_id": int(season["id"]),
        "series_title": season.get("series_name") or "",
        "season_title": season.get("season_name") or "",
        "episodes": episodes,
        "previous_episode_video_id": previous_id,
        "next_episode_video_id": next_id,
    }


@router.get("/ping")
def ping() -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "service": "local-video-site",
            "api_version": 1,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/videos")
def videos(
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=60),
    sort: Literal[
        "recent",
        "name",
        "duration",
        "size",
        "oldest",
    ] = "recent",
) -> JSONResponse:
    data = database.list_videos(
        sort=sort,
        page=page,
        page_size=page_size,
        group_seasons=True,
        max_access_level=0,
    )
    payload = {
        "items": [_video_card(item) for item in data["items"]],
        "page": int(data["page"]),
        "page_size": int(data["page_size"]),
        "pages": int(data["pages"]),
        "total": int(data["total"]),
        "has_next": int(data["page"]) < int(data["pages"]),
    }
    return JSONResponse(
        payload,
        headers={"Cache-Control": "private, max-age=10"},
    )


@router.get("/home")
def category_home() -> JSONResponse:
    return JSONResponse(
        tv_home_categories(),
        headers={"Cache-Control": "private, max-age=10"},
    )


@router.get("/series")
def series_list(
    category_key: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=60),
) -> JSONResponse:
    try:
        payload = list_tv_series(
            category_key,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse(
        payload,
        headers={"Cache-Control": "private, max-age=10"},
    )


@router.get("/series/{series_id}")
def series_detail(series_id: int) -> JSONResponse:
    payload = get_tv_series(series_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="视频集不存在")
    return JSONResponse(
        payload,
        headers={"Cache-Control": "private, max-age=10"},
    )


@router.get("/series/{series_id}/cover")
def series_cover(series_id: int):
    path = series_cover_file(series_id)
    if path is None:
        raise HTTPException(status_code=404, detail="视频集封面不存在")
    return FileResponse(
        path=path,
        media_type="image/jpeg",
        content_disposition_type="inline",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/seasons/{season_id}/cover")
def season_cover(season_id: int):
    path = season_cover_file(season_id)
    if path is None:
        raise HTTPException(status_code=404, detail="视频季封面不存在")
    return FileResponse(
        path=path,
        media_type="image/jpeg",
        content_disposition_type="inline",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/videos/{video_id}")
def video_detail(video_id: int) -> JSONResponse:
    video = _visible_video_or_404(video_id)
    payload = {
        "id": int(video["id"]),
        "title": str(video.get("title") or "未命名视频"),
        "duration_seconds": (
            round(float(video["duration"]), 3)
            if video.get("duration") is not None
            else None
        ),
        "width": video.get("width"),
        "height": video.get("height"),
        "video_codec": video.get("video_codec") or "",
        "audio_codec": video.get("audio_codec") or "",
        "extension": video.get("extension") or "",
        "container_format": video.get("container_format") or "",
        "poster_url": _thumbnail_url(video),
        "series": _season_episodes(video_id),
        "playback": _safe_playback(video_id),
    }
    return JSONResponse(
        payload,
        headers={"Cache-Control": "private, max-age=10"},
    )


@router.get("/videos/{video_id}/playback")
def playback_state(video_id: int) -> JSONResponse:
    _visible_video_or_404(video_id)
    return JSONResponse(
        _safe_playback(video_id),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/videos/{video_id}/original-playback")
def original_playback(video_id: int) -> JSONResponse:
    video = _visible_video_or_404(video_id)
    resolution = resolve_video_storage(video)
    state = resolution["state"]
    if state == "external_offline":
        playback_info = get_playback_state(video_id)
        raise HTTPException(
            status_code=409,
            detail=playback_info["message"],
        )
    path = resolution["path"]
    if not path.is_file():
        database.mark_video_missing(video_id)
        raise HTTPException(status_code=404, detail="视频文件不存在")
    extension = path.suffix.lower()
    mime_type = media_type_for_path(path)
    if mime_type is None:
        raise HTTPException(
            status_code=415,
            detail="当前文件类型不支持外部播放",
        )
    return JSONResponse(
        {
            "video_id": int(video_id),
            "available": True,
            "title": str(video.get("title") or "未命名视频"),
            "stream_url": "/media/{}".format(video_id),
            "mime_type": mime_type,
            "container_format": video.get("container_format") or "",
            "extension": extension,
            "video_codec": video.get("video_codec") or "",
            "audio_codec": video.get("audio_codec") or "",
            "width": video.get("width"),
            "height": video.get("height"),
            "duration_seconds": video.get("duration"),
            "supports_range": True,
            "external_player": "vlc",
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/videos/{video_id}/prepare")
def prepare_video(video_id: int) -> JSONResponse:
    video = _visible_video_or_404(video_id)
    if Path(str(video.get("path") or "")).suffix.lower() == ".mkv":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "mkv_external_only",
                "message": "MKV 视频仅支持使用 VLC 外部播放",
            },
        )
    state = get_playback_state(video_id)
    if state["state"] == "external_offline":
        raise HTTPException(status_code=409, detail=state["message"])
    if state["state"] == "file_missing":
        raise HTTPException(status_code=404, detail=state["message"])

    resolution = resolve_video_storage(video)
    resolved_video = {
        **video,
        "path": str(resolution["path"]),
    }
    if not resolution["path"].is_file():
        database.mark_video_missing(video_id)
        raise HTTPException(status_code=404, detail="视频文件不存在")

    if requires_playback_cache(resolved_video):
        try:
            LOGGER.info(
                "TV playback preparation started video_id=%s",
                video_id,
                extra={"operation": "tv.playback.prepare.start"},
            )
            prepare_playback_cache(resolved_video, logger=LOGGER)
        except FFmpegBusyError as exc:
            raise HTTPException(
                status_code=503,
                detail=str(exc),
                headers={"Retry-After": "5"},
            ) from exc
        except RuntimeError as exc:
            LOGGER.exception(
                "TV playback preparation failed video_id=%s",
                video_id,
                extra={"operation": "tv.playback.prepare.failed"},
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        LOGGER.info(
            "TV playback preparation complete video_id=%s",
            video_id,
            extra={"operation": "tv.playback.prepare.complete"},
        )

    return JSONResponse(
        _safe_playback(video_id),
        headers={"Cache-Control": "no-store"},
    )
