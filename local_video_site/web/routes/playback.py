from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from local_video_site.core import config
from local_video_site import database
from local_video_site.identity.permissions.content import ACCESS_LEVEL_ADMIN, can_view, max_level_for
from local_video_site.episodes.naming import episode_display_title
from local_video_site.media.ffmpeg import FFmpegBusyError
from local_video_site.media.playback.engine import ensure_playback_technical_info, prepare_playback_cache, prepared_playback_path, requires_playback_cache, requires_transcode, touch_playback_cache
from local_video_site.media.playback.state import get_playback_state
from local_video_site.media.editing.format import (
    VideoFormatError,
    create_standardization_job,
    standardization_preview,
)
from local_video_site.storage.volumes import decorate_video, resolve_video_storage
from ..templates import TEMPLATES
from ..urls import (
    safe_return_path,
    watch_url as _watch_url,
)
from ..audit import audit_account_action as _audit_account_action
from ..models.playback import VideoStandardizeCreate


router = APIRouter()
LOGGER = logging.getLogger("local_video_site")
MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".ts": "video/mp2t",
    ".avi": "video/x-msvideo",
    ".rmvb": "video/vnd.rn-realvideo",
}


def _safe_return_path(value: str) -> str:
    return safe_return_path(value)


def _visible_video_or_404(request: Request, video_id: int) -> dict:
    video = database.get_video(int(video_id))
    if not video or not can_view(request.state.identity, video):
        raise HTTPException(status_code=404, detail="视频不存在")
    return video


def _listing_neighbors(
    video_id: int,
    return_to: str,
    favorite_user_id: Optional[int] = None,
    max_access_level: Optional[int] = None,
) -> tuple:
    if not return_to:
        return None, None
    parsed = urlsplit(safe_return_path(return_to))
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query = params.get("q", "")
    sort = params.get("sort", "")
    directory = params.get("directory", "")
    category_match = re.fullmatch(r"/categories/(\d+)", parsed.path)
    manual_category_match = re.fullmatch(
        r"/manual-categories/(\d+)",
        parsed.path,
    )
    actor_match = re.fullmatch(r"/actors/(\d+)", parsed.path)
    if parsed.path == "/":
        return database.get_video_neighbors(
            video_id,
            query=query,
            directory=directory,
            sort=sort or "recent",
            max_access_level=max_access_level,
        )
    if (
        parsed.path == "/admin-videos"
        and max_access_level == ACCESS_LEVEL_ADMIN
    ):
        return database.get_video_neighbors(
            video_id,
            query=query,
            sort=sort or "recent",
            max_access_level=ACCESS_LEVEL_ADMIN,
            exact_access_level=ACCESS_LEVEL_ADMIN,
        )
    if category_match:
        category_id = int(category_match.group(1))
        if database.get_category(category_id):
            return database.get_video_neighbors(
                video_id,
                category_id=category_id,
                max_access_level=max_access_level,
                sort=sort or "recent",
            )
    elif manual_category_match:
        manual_category_id = int(manual_category_match.group(1))
        if database.get_manual_category(manual_category_id):
            return database.get_video_neighbors(
                video_id,
                manual_category_id=manual_category_id,
                max_access_level=max_access_level,
                sort=sort or "recent",
            )
    elif actor_match:
        actor_id = int(actor_match.group(1))
        if database.get_actor(actor_id):
            return database.get_video_neighbors(
                video_id,
                actor_id=actor_id,
                max_access_level=max_access_level,
                sort=sort or "recent",
            )
    elif parsed.path == "/favorites":
        return database.get_video_neighbors(
            video_id,
            favorite_only=True,
            favorite_user_id=favorite_user_id,
            max_access_level=max_access_level,
            sort=sort or "favorited",
        )
    return None, None


@router.get("/manage/player-test/{video_id}", response_class=HTMLResponse)
def player_compatibility_test_page(request: Request, video_id: int):
    video = database.get_video(video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="视频不存在")
    return TEMPLATES.TemplateResponse(
        request=request,
        name="player_test.html",
        context={
            "video": video,
            "playback": get_playback_state(
                video_id,
                identity=request.state.identity,
            ),
            "movi_asset_base": config.movi_player_asset_base(),
            "movi_buffer_mb": config.movi_player_buffer_mb(),
            "movi_mode": config.movi_player_mode(),
        },
        headers={"Cache-Control": "no-store"},
    )


def _resolved_episode_title(episode: dict) -> str:
    return episode_display_title(
        str(episode.get("episode_title") or ""),
        relative_path=str(episode.get("relative_path") or ""),
        video_path=str(episode.get("video_path") or ""),
    )


def _navigation_episode_title(episode: dict) -> str:
    return (
        str(episode.get("video_title") or "").strip()
        or _resolved_episode_title(episode)
    )


@router.get("/watch/{video_id}", response_class=HTMLResponse)
def watch(request: Request, video_id: int, return_to: str = ""):
    identity = request.state.identity
    video = _visible_video_or_404(request, video_id)
    max_access_level = max_level_for(identity)
    playback_info = get_playback_state(video_id, identity=identity)
    safe_return = _safe_return_path(return_to)
    previous_video, next_video = _listing_neighbors(
        video_id,
        safe_return,
        favorite_user_id=identity.user_id,
        max_access_level=max_access_level,
    )
    season = database.season_context_for_video(
        video_id,
        max_access_level=max_access_level,
    )
    season_episodes = []
    current_episode = None
    current_episode_index = None
    season_previous = None
    season_next = None
    if season:
        for index, episode in enumerate(season["episodes"]):
            item = dict(episode)
            item["display_episode_number"] = index + 1
            item["episode_title"] = _resolved_episode_title(item)
            item["navigation_title"] = _navigation_episode_title(item)
            resolved_video_id = item.get("resolved_video_id")
            state = "planned"
            if resolved_video_id is not None:
                if int(item.get("video_is_deleted") or 0):
                    state = "deleted"
                elif int(item.get("video_missing") or 0):
                    state = "missing"
                else:
                    storage = decorate_video({
                        "id": int(resolved_video_id),
                        "path": item.get("video_path") or "",
                        "missing": 0,
                        "is_deleted": 0,
                        "storage_volume_id": item.get("storage_volume_id"),
                        "storage_relative_path": item.get(
                            "storage_relative_path"
                        ),
                        "storage_changed_at": item.get("storage_changed_at"),
                    })
                    state = storage["storage_state"]
            item["playback_state"] = state
            item["playable"] = state in {
                "local",
                "local_online",
                "external_online",
            }
            item["watch_url"] = (
                _watch_url(int(resolved_video_id), safe_return)
                if (
                    resolved_video_id is not None
                    and state not in {"deleted", "missing", "file_missing"}
                )
                else ""
            )
            season_episodes.append(item)
            if int(item["video_id"] or 0) == int(video_id):
                current_episode = item
                current_episode_index = index
        if current_episode_index is not None:
            if current_episode_index > 0:
                season_previous = season_episodes[
                    current_episode_index - 1
                ]
            if current_episode_index + 1 < len(season_episodes):
                season_next = season_episodes[
                    current_episode_index + 1
                ]
        metadata_video_id = int(
            season.get("metadata_video_id")
            or season.get("representative_video_id")
            or video_id
        )
    else:
        metadata_video_id = int(video_id)
    poster_video_id = int(video_id)
    video_manual_categories = database.manual_categories_for_video(video_id)
    video_path = resolve_video_storage(video)["path"]
    return TEMPLATES.TemplateResponse(
        request=request,
        name="watch.html",
        context={
            "video": video,
            "video_file_name": video_path.name,
            "video_file_stem": video_path.stem,
            "video_extension": video_path.suffix,
            "video_directory": str(video_path.parent),
            "playback": playback_info,
            "identity": identity,
            "is_favorite": (
                database.is_user_favorite(identity.user_id, video_id)
                if identity.can_favorite
                else False
            ),
            "return_to": safe_return,
            "previous_video": previous_video,
            "next_video": next_video,
            "previous_url": (
                _watch_url(previous_video["id"], safe_return)
                if previous_video
                else None
            ),
            "next_url": (
                _watch_url(next_video["id"], safe_return)
                if next_video
                else None
            ),
            "season": season,
            "season_episodes": season_episodes,
            "current_episode": current_episode,
            "season_previous": season_previous,
            "season_next": season_next,
            "poster_url": "/thumb/{}".format(poster_video_id),
            "movi_asset_base": config.movi_player_asset_base(),
            "movi_buffer_mb": config.movi_player_buffer_mb(),
            "movi_load_timeout_seconds": (
                config.movi_player_load_timeout_seconds()
            ),
            "categories": database.categories_for_video(metadata_video_id),
            "manual_categories": video_manual_categories,
            "manual_category_options": database.list_manual_categories(),
            "selected_manual_category_ids": {
                int(item["id"])
                for item in video_manual_categories
            },
            "actors": database.actors_for_video(metadata_video_id),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/playback-state/{video_id}")
def api_playback_state(request: Request, video_id: int):
    _visible_video_or_404(request, video_id)
    state = get_playback_state(
        video_id,
        identity=request.state.identity,
    )
    if state["state"] == "not_found":
        raise HTTPException(status_code=404, detail=state["message"])
    return JSONResponse(
        state,
        headers={"Cache-Control": "no-store"},
    )


@router.api_route("/media/{video_id}", methods=["GET", "HEAD"])
def media(request: Request, video_id: int):
    video = database.get_video(video_id)
    if video and not can_view(request.state.identity, video):
        raise HTTPException(status_code=404, detail="视频不存在")
    if not video:
        deleted = database.get_video_admin(video_id)
        if deleted and deleted.get("is_deleted"):
            raise HTTPException(status_code=410, detail="视频已删除")
        raise HTTPException(status_code=404, detail="视频不存在")
    resolution = resolve_video_storage(video)
    if resolution["state"] == "external_offline":
        playback_info = get_playback_state(
            video_id,
            identity=request.state.identity,
        )
        raise HTTPException(
            status_code=409,
            detail=playback_info["message"],
        )
    path = resolution["path"]
    if not path.is_file():
        database.mark_video_missing(video_id)
        raise HTTPException(status_code=404, detail="视频文件不存在")
    headers = {"Accept-Ranges": "bytes"}
    if int(video.get("access_level") or 0) > 0:
        headers["Cache-Control"] = "private, no-store"
    return FileResponse(
        path=path,
        media_type=MEDIA_TYPES.get(path.suffix.lower()),
        content_disposition_type="inline",
        filename=path.name,
        headers=headers,
    )


def _file_video_response(path: Path, private: bool = False) -> FileResponse:
    headers = {"Accept-Ranges": "bytes"}
    if private:
        headers["Cache-Control"] = "private, no-store"
    return FileResponse(
        path=path,
        media_type="video/mp4",
        content_disposition_type="inline",
        filename=path.name,
        headers=headers,
    )


@router.api_route("/playback/{video_id}", methods=["GET", "HEAD"])
def playback(request: Request, video_id: int):
    video = database.get_video(video_id)
    if video and not can_view(request.state.identity, video):
        raise HTTPException(status_code=404, detail="视频不存在")
    if not video:
        deleted = database.get_video_admin(video_id)
        if deleted and deleted.get("is_deleted"):
            raise HTTPException(status_code=410, detail="视频已删除")
        raise HTTPException(status_code=404, detail="视频不存在")
    resolution = resolve_video_storage(video)
    if resolution["state"] == "external_offline":
        playback_info = get_playback_state(
            video_id,
            identity=request.state.identity,
        )
        raise HTTPException(
            status_code=409,
            detail=playback_info["message"],
        )
    video = ensure_playback_technical_info({
        **video,
        "path": str(resolution["path"]),
    }, logger=LOGGER)
    if requires_transcode(video):
        raise HTTPException(
            status_code=422,
            detail="当前仅支持容器封装，系统不会自动转码视频或音频",
        )
    if not requires_playback_cache(video):
        return _file_video_response(
            resolution["path"],
            private=int(video.get("access_level") or 0) > 0,
        )
    cache = prepared_playback_path(video)
    if not cache:
        raise HTTPException(status_code=409, detail="播放缓存尚未准备")
    touch_playback_cache(cache)
    return _file_video_response(
        cache,
        private=int(video.get("access_level") or 0) > 0,
    )


@router.post("/api/prepare/{video_id}")
def prepare_video(request: Request, video_id: int):
    video = database.get_video(video_id)
    if video and not can_view(request.state.identity, video):
        raise HTTPException(status_code=404, detail="视频不存在")
    if not video:
        raise HTTPException(status_code=404, detail="视频不存在")
    playback_info = get_playback_state(
        video_id,
        identity=request.state.identity,
    )
    if playback_info["state"] == "external_offline":
        raise HTTPException(
            status_code=409,
            detail=playback_info["message"],
        )
    if playback_info["state"] == "file_missing":
        raise HTTPException(
            status_code=404,
            detail=playback_info["message"],
        )
    resolution = resolve_video_storage(video)
    if not resolution["path"].is_file():
        database.mark_video_missing(video_id)
        raise HTTPException(status_code=404, detail="视频文件不存在")
    video = ensure_playback_technical_info({
        **video,
        "path": str(resolution["path"]),
    }, logger=LOGGER)
    if requires_transcode(video):
        raise HTTPException(
            status_code=422,
            detail="当前仅支持容器封装，系统不会自动转码视频或音频",
        )
    if not requires_playback_cache(video):
        LOGGER.info(
            "Playback direct video_id=%s codec=%s",
            video_id,
            video.get("video_codec"),
            extra={"operation": "playback.direct"},
        )
        return {"ready": True, "playback_url": "/media/{}".format(video_id)}
    try:
        LOGGER.info(
            "Preparing playback video_id=%s codec=%s",
            video_id,
            video.get("video_codec"),
            extra={"operation": "playback.cache.start"},
        )
        prepare_playback_cache(video, logger=LOGGER)
    except FFmpegBusyError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
            headers={"Retry-After": "5"},
        ) from exc
    except RuntimeError as exc:
        LOGGER.exception(
            "Playback preparation failed video_id=%s",
            video_id,
            extra={"operation": "playback.cache.failed"},
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    LOGGER.info(
        "Playback ready video_id=%s",
        video_id,
        extra={"operation": "playback.cache.complete"},
    )
    return {"ready": True, "playback_url": "/playback/{}".format(video_id)}


@router.get("/api/videos/{video_id}/standardization-preview")
def api_standardization_preview(request: Request, video_id: int):
    identity = request.state.identity
    if not identity.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    video = database.get_video_admin(video_id)
    if video is None or not can_view(identity, video):
        raise HTTPException(status_code=404, detail="视频不存在")
    resolution = resolve_video_storage(video)
    if resolution["state"] not in {"external_offline", "file_missing"}:
        video = ensure_playback_technical_info({
            **video,
            "path": str(resolution["path"]),
        }, logger=LOGGER)
    return standardization_preview(video)


@router.post("/api/videos/{video_id}/standardize")
def api_standardize_video(
    request: Request,
    video_id: int,
    selection: VideoStandardizeCreate,
):
    identity = request.state.identity
    if not identity.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    video = database.get_video_admin(video_id)
    if video is None or not can_view(identity, video):
        raise HTTPException(status_code=404, detail="视频不存在")
    try:
        result = create_standardization_job(
            video_id,
            confirmed=selection.confirmed,
            confirm_lossy=selection.confirm_lossy,
            expected_path=selection.expected_path,
            expected_mtime_ns=(
                int(selection.expected_mtime_ns)
                if selection.expected_mtime_ns
                else None
            ),
            created_by_user_id=identity.user_id,
            created_by_username=identity.username,
        )
    except VideoFormatError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    _audit_account_action(
        request,
        "video.standardize.created",
        "success",
        "video_format_batch",
        result.get("batch_id") or result.get("job_id") or str(video_id),
        details=result,
    )
    return result
