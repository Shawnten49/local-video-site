from __future__ import annotations

from typing import Optional, Union

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from local_video_site import database
from local_video_site.episodes.restore import VideoRestoreError, browse_restore_entries, preview_restore_source, restore_video_online
from local_video_site.episodes.series_management import EpisodeEditError, SeriesEditError, get_season as get_managed_season, get_series as get_managed_series, list_season_videos, list_seasons as list_managed_seasons, list_series as list_managed_series, update_episode_title, update_season_display_name, update_series_display_name
from ..audit import audit_account_action
from ..templates import TEMPLATES


router = APIRouter()


class SeriesDisplayNameUpdate(BaseModel):
    display_name: str
    expected_updated_at: str = ""


class SeasonDisplayNameUpdate(BaseModel):
    display_name: str
    expected_updated_at: str = ""


class EpisodeTitleUpdate(BaseModel):
    episode_title: str
    expected_updated_at: str = ""


class VideoRestorePreview(BaseModel):
    episode_id: int
    source_path: str
    storage_scope: str = "auto"


class VideoRestoreOnline(VideoRestorePreview):
    expected_size: Optional[int] = None
    expected_mtime_ns: Optional[Union[int, str]] = None
    source_fingerprint: str = ""
    expected_updated_at: str = ""


def _download_counts(identity):
    return database.download_task_counts(
        owner_user_id=identity.user_id,
        owned_only=False,
    )


@router.get("/manage/series", response_class=HTMLResponse)
def manage_series(
    request: Request,
    q: str = "",
    page: int = Query(1, ge=1),
):
    identity = request.state.identity
    data = list_managed_series(
        query=q.strip(),
        page=page,
        page_size=30,
    )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="video_series.html",
        context={
            "identity": identity,
            "download_counts": _download_counts(identity),
            "active_page": "series",
            "data": data,
            "query": q.strip(),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/manage/series/{series_id}", response_class=HTMLResponse)
def manage_series_detail(
    request: Request,
    series_id: int,
    q: str = "",
    page: int = Query(1, ge=1),
):
    series = get_managed_series(series_id)
    if series is None:
        raise HTTPException(status_code=404, detail="视频集不存在")
    identity = request.state.identity
    seasons = list_managed_seasons(
        series_id,
        query=q.strip(),
        page=page,
        page_size=30,
    )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="video_series_detail.html",
        context={
            "identity": identity,
            "download_counts": _download_counts(identity),
            "active_page": "series",
            "series": series,
            "seasons": seasons,
            "query": q.strip(),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get(
    "/manage/series/{series_id}/seasons/{season_id}",
    response_class=HTMLResponse,
)
def manage_season_detail(
    request: Request,
    series_id: int,
    season_id: int,
    q: str = "",
    record_state: str = "all",
    page: int = Query(1, ge=1),
):
    season = get_managed_season(series_id, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail="视频季不存在")
    identity = request.state.identity
    data = list_season_videos(
        series_id,
        season_id,
        query=q.strip(),
        record_state=record_state,
        page=page,
        page_size=30,
    )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="video_season_detail.html",
        context={
            "identity": identity,
            "download_counts": _download_counts(identity),
            "active_page": "series",
            "series_id": int(series_id),
            "season": season,
            "data": data,
            "query": q.strip(),
            "record_state": data["record_state"],
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/manage/series")
def api_manage_series(
    q: str = "",
    page: int = Query(1, ge=1),
):
    return list_managed_series(
        query=q.strip(),
        page=page,
        page_size=30,
    )


@router.get("/api/manage/series/{series_id}/seasons")
def api_manage_seasons(
    series_id: int,
    q: str = "",
    page: int = Query(1, ge=1),
):
    return list_managed_seasons(
        series_id,
        query=q.strip(),
        page=page,
        page_size=30,
    )


@router.get("/api/manage/series/{series_id}/seasons/{season_id}/videos")
def api_manage_season_videos(
    series_id: int,
    season_id: int,
    q: str = "",
    record_state: str = "all",
    page: int = Query(1, ge=1),
):
    return list_season_videos(
        series_id,
        season_id,
        query=q.strip(),
        record_state=record_state,
        page=page,
        page_size=30,
    )


def _series_edit_http_error(exc: SeriesEditError) -> HTTPException:
    if exc.code in {"series_missing", "season_missing"}:
        status_code = 404
    elif exc.code in {
        "display_name_required",
        "display_name_too_long",
        "display_name_invalid",
    }:
        status_code = 422
    else:
        status_code = 409
    return HTTPException(
        status_code=status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


def _episode_edit_http_error(exc: EpisodeEditError) -> HTTPException:
    if exc.code == "episode_missing":
        status_code = 404
    elif exc.code in {
        "episode_title_required",
        "episode_title_too_long",
        "episode_title_invalid",
        "episode_title_generic",
    }:
        status_code = 422
    else:
        status_code = 409
    return HTTPException(
        status_code=status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


@router.patch("/api/manage/series/{series_id}")
def api_update_series_display_name(
    request: Request,
    series_id: int,
    selection: SeriesDisplayNameUpdate,
):
    try:
        result = update_series_display_name(
            series_id,
            selection.display_name,
            expected_updated_at=selection.expected_updated_at,
        )
    except SeriesEditError as exc:
        raise _series_edit_http_error(exc) from exc
    audit_account_action(
        request,
        "series.display_name.updated",
        "success",
        "video_series",
        str(series_id),
        details={
            "source_name": result["name"],
            "old_display_name": result["previous_display_name"],
            "new_display_name": result["display_name"],
            "path_changed": False,
        },
    )
    result["path_changed"] = False
    return result


@router.patch("/api/manage/series/{series_id}/seasons/{season_id}")
def api_update_season_display_name(
    request: Request,
    series_id: int,
    season_id: int,
    selection: SeasonDisplayNameUpdate,
):
    try:
        result = update_season_display_name(
            series_id,
            season_id,
            selection.display_name,
            expected_updated_at=selection.expected_updated_at,
        )
    except SeriesEditError as exc:
        raise _series_edit_http_error(exc) from exc
    audit_account_action(
        request,
        "season.display_name.updated",
        "success",
        "video_season",
        str(season_id),
        details={
            "series_id": int(series_id),
            "source_name": result["name"],
            "old_display_name": result["previous_display_name"],
            "new_display_name": result["display_name"],
            "path_changed": False,
        },
    )
    result["path_changed"] = False
    return result


@router.patch(
    "/api/manage/series/{series_id}/seasons/{season_id}"
    "/episodes/{episode_id}"
)
def api_update_episode_title(
    request: Request,
    series_id: int,
    season_id: int,
    episode_id: int,
    selection: EpisodeTitleUpdate,
):
    try:
        result = update_episode_title(
            series_id,
            season_id,
            episode_id,
            selection.episode_title,
            expected_updated_at=selection.expected_updated_at,
        )
    except EpisodeEditError as exc:
        raise _episode_edit_http_error(exc) from exc
    audit_account_action(
        request,
        "episode.title_changed",
        "success",
        "video_episode",
        str(episode_id),
        details={
            "series_id": int(series_id),
            "season_id": int(season_id),
            "video_id": result["video_id"],
            "episode_number": result["episode_number"],
            "old_episode_title": result["old_episode_title"],
            "new_episode_title": result["episode_title"],
        },
    )
    return result


@router.post("/api/manage/videos/{video_id}/restore/preview")
def api_preview_video_restore(
    video_id: int,
    selection: VideoRestorePreview,
):
    try:
        return preview_restore_source(
            video_id=video_id,
            episode_id=selection.episode_id,
            source_path=selection.source_path,
            storage_scope=selection.storage_scope,
        )
    except VideoRestoreError as exc:
        raise _video_restore_http_error(exc) from exc


@router.post("/api/manage/videos/{video_id}/restore-online")
def api_restore_video_online(
    request: Request,
    video_id: int,
    selection: VideoRestoreOnline,
):
    try:
        result = restore_video_online(
            video_id=video_id,
            episode_id=selection.episode_id,
            source_path=selection.source_path,
            storage_scope=selection.storage_scope,
            expected_size=selection.expected_size,
            expected_mtime_ns=selection.expected_mtime_ns,
            source_fingerprint=selection.source_fingerprint,
            expected_updated_at=selection.expected_updated_at,
        )
    except VideoRestoreError as exc:
        raise _video_restore_http_error(exc) from exc
    audit_account_action(
        request,
        "video.restored_online",
        "success",
        "video",
        str(video_id),
        details={
            "episode_id": result["episode_id"],
            "old_path": result["old_path"],
            "new_path": result["path"],
            "storage_kind": result["storage_kind"],
            "storage_volume_id": result["storage_volume_id"],
            "access_level": result["access_level"],
        },
    )
    return result


def _video_restore_http_error(exc: VideoRestoreError) -> HTTPException:
    if exc.code == "episode_mismatch":
        status_code = 404
    elif exc.code in {
        "video_not_deleted",
        "target_deleting",
        "active_migration",
        "source_already_bound",
        "source_changed",
        "preview_required",
        "data_changed",
        "library_busy",
    }:
        status_code = 409
    else:
        status_code = 422
    return HTTPException(
        status_code=status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


@router.get("/api/manage/videos/restore/browse")
def api_restore_video_browse(
    scope: str = "local",
    path: str = "",
):
    try:
        return browse_restore_entries(scope, path)
    except VideoRestoreError as exc:
        raise _video_restore_http_error(exc) from exc
