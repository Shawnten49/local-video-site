from __future__ import annotations

import math
from pathlib import Path
from typing import Literal, Optional, Union

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from local_video_site import database
from local_video_site.core import config
from local_video_site.catalog.tv_home import (
    series_cover_editor,
    season_cover_file,
    season_cover_url,
)
from local_video_site.episodes.restore import VideoRestoreError, browse_restore_entries, preview_restore_source, restore_video_online
from local_video_site.episodes.series_management import EpisodeEditError, SeriesEditError, get_season as get_managed_season, get_series as get_managed_series, list_season_videos, list_seasons as list_managed_seasons, list_series as list_managed_series, update_episode_title, update_season_display_name, update_series_display_name
from local_video_site.media.assets.series_covers import (
    generate_season_cover_from_video,
    generate_series_cover_from_image,
    generate_series_cover_from_video,
)
from local_video_site.media.ffmpeg import FFmpegBusyError
from local_video_site.storage.volumes import resolve_video_storage
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


class SeriesManualCategorySelection(BaseModel):
    manual_category_ids: list[int]
    expected_updated_at: str = ""


class SeriesCoverUpdate(BaseModel):
    source: Literal["auto", "season", "video_frame"]
    expected_updated_at: str = ""
    season_id: Optional[int] = None
    video_id: Optional[int] = None
    timestamp: float = 0


class SeasonCoverUpdate(BaseModel):
    source: Literal["inherited", "video_frame"]
    expected_updated_at: str = ""
    video_id: Optional[int] = None
    timestamp: float = 0


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


@router.get("/api/manage/series/{series_id}/manual-category-options")
def api_series_manual_category_options(series_id: int):
    series = get_managed_series(series_id)
    if series is None:
        raise HTTPException(status_code=404, detail="视频集不存在")
    selected = {
        int(item["id"])
        for item in database.manual_categories_for_series(series_id)
    }
    items = database.list_manual_categories()
    for item in items:
        item["selected"] = int(item["id"]) in selected
    return {
        "series_id": int(series_id),
        "title": series.get("display_name") or series.get("name"),
        "updated_at": series.get("updated_at") or "",
        "items": items,
    }


@router.put("/api/manage/series/{series_id}/manual-categories")
def api_update_series_manual_categories(
    request: Request,
    series_id: int,
    selection: SeriesManualCategorySelection,
):
    try:
        items = database.set_series_manual_categories(
            series_id,
            selection.manual_category_ids,
            expected_updated_at=selection.expected_updated_at,
        )
    except ValueError as exc:
        status_code = 404 if "不存在" in str(exc) else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    audit_account_action(
        request,
        "series.manual_categories.updated",
        "success",
        "video_series",
        str(series_id),
        details={"manual_category_ids": [int(item["id"]) for item in items]},
    )
    return {"items": items}


@router.get("/api/manage/series/{series_id}/cover-editor")
def api_series_cover_editor(series_id: int):
    editor = series_cover_editor(series_id)
    if editor is None:
        raise HTTPException(status_code=404, detail="视频集不存在")
    return editor


def _series_video_for_cover(series_id: int, video_id: int) -> dict:
    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT v.id, v.title, v.path, v.size, v.mtime_ns, v.duration
            FROM video_episodes e
            JOIN video_seasons s ON s.id = e.season_id
            JOIN video_catalog v ON v.id = e.video_id
            WHERE s.series_id = ?
              AND s.is_deleted = 0
              AND e.video_id = ?
              AND v.missing = 0
              AND v.is_deleted = 0
            LIMIT 1
            """,
            (int(series_id), int(video_id)),
        ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=422,
            detail="所选视频不属于当前视频集或已不可用",
        )
    return dict(row)


def _series_season_video_for_cover(series_id: int, season_id: int) -> dict:
    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT v.id, v.title, v.path, v.size, v.mtime_ns, v.duration
            FROM video_episodes e
            JOIN video_seasons s ON s.id = e.season_id
            JOIN video_catalog v ON v.id = e.video_id
            WHERE s.series_id = ?
              AND s.id = ?
              AND s.is_deleted = 0
              AND v.missing = 0
              AND v.is_deleted = 0
            ORDER BY e.episode_number, v.id
            LIMIT 1
            """,
            (int(series_id), int(season_id)),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=422, detail="所选视频季没有可用视频")
    return dict(row)


def _remove_previous_series_cover(path_value: str) -> None:
    if not path_value:
        return
    path = Path(path_value)
    try:
        path.resolve().relative_to(config.SERIES_COVER_DIR.resolve())
    except (OSError, ValueError):
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


@router.put("/api/manage/series/{series_id}/cover")
def api_update_series_cover(
    request: Request,
    series_id: int,
    selection: SeriesCoverUpdate,
):
    series = get_managed_series(series_id)
    if series is None:
        raise HTTPException(status_code=404, detail="视频集不存在")
    if series.get("active_delete_job_id"):
        raise HTTPException(status_code=409, detail="存在活动删除任务，不能修改封面")
    if (
        selection.expected_updated_at
        and str(series.get("updated_at") or "")
        != str(selection.expected_updated_at)
    ):
        raise HTTPException(status_code=409, detail="视频集已发生变化，请刷新后重试")

    cover_path = None
    season_id = None
    video_id = None
    timestamp = None
    try:
        if selection.source == "season":
            if selection.season_id is None:
                raise HTTPException(status_code=422, detail="请选择视频季")
            season_id = int(selection.season_id)
            image_path = season_cover_file(season_id)
            if image_path is not None:
                cover_path = generate_series_cover_from_image(
                    series_id,
                    image_path,
                    season_id,
                )
            else:
                video = _series_season_video_for_cover(
                    series_id,
                    season_id,
                )
                video_id = int(video["id"])
                duration = float(video.get("duration") or 0)
                timestamp = min(
                    max(duration * 0.1, 0.5) if duration > 0 else 0.5,
                    max(duration - 0.1, 0.5),
                )
                resolution = resolve_video_storage(video)
                if resolution["state"] == "external_offline":
                    raise HTTPException(
                        status_code=409,
                        detail="视频所在外置硬盘当前未连接",
                    )
                cover_path = generate_series_cover_from_video(
                    series_id,
                    resolution["path"],
                    video_id,
                    timestamp,
                )
        elif selection.source == "video_frame":
            if selection.video_id is None:
                raise HTTPException(status_code=422, detail="请选择剧集")
            if not math.isfinite(selection.timestamp) or selection.timestamp < 0:
                raise HTTPException(status_code=422, detail="封面时间点无效")
            video_id = int(selection.video_id)
            video = _series_video_for_cover(series_id, video_id)
            duration = float(video.get("duration") or 0)
            timestamp = (
                min(selection.timestamp, max(0.0, duration - 0.1))
                if duration > 0
                else selection.timestamp
            )
            resolution = resolve_video_storage(video)
            if resolution["state"] == "external_offline":
                raise HTTPException(
                    status_code=409,
                    detail="视频所在外置硬盘当前未连接",
                )
            cover_path = generate_series_cover_from_video(
                series_id,
                resolution["path"],
                video_id,
                timestamp,
            )
        elif selection.source == "auto":
            pass
        if selection.source != "auto" and cover_path is None:
            raise HTTPException(status_code=500, detail="视频集封面生成失败")
    except FFmpegBusyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    now = database._now()
    previous_cover = str(series.get("cover_path") or "")
    with database.connect() as connection:
        connection.execute(
            """
            UPDATE video_series
            SET cover_path = ?, cover_source = ?, cover_source_season_id = ?,
                cover_source_video_id = ?, cover_timestamp = ?,
                cover_updated_at = ?, updated_at = ?
            WHERE id = ? AND is_deleted = 0
            """,
            (
                str(cover_path) if cover_path is not None else None,
                selection.source,
                season_id,
                video_id,
                timestamp,
                now if cover_path is not None else None,
                now,
                int(series_id),
            ),
        )
    if previous_cover and previous_cover != str(cover_path or ""):
        _remove_previous_series_cover(previous_cover)
    audit_account_action(
        request,
        "series.cover.updated",
        "success",
        "video_series",
        str(series_id),
        details={
            "source": selection.source,
            "season_id": season_id,
            "video_id": video_id,
            "timestamp": timestamp,
        },
    )
    return {
        "status": "ok",
        "source": selection.source,
        "cover_updated_at": now if cover_path is not None else None,
    }


@router.delete("/api/manage/series/{series_id}/cover")
def api_restore_series_cover(request: Request, series_id: int):
    series = get_managed_series(series_id)
    if series is None:
        raise HTTPException(status_code=404, detail="视频集不存在")
    previous_cover = str(series.get("cover_path") or "")
    now = database._now()
    with database.connect() as connection:
        connection.execute(
            """
            UPDATE video_series
            SET cover_path = NULL, cover_source = 'auto',
                cover_source_season_id = NULL,
                cover_source_video_id = NULL,
                cover_timestamp = NULL, cover_updated_at = NULL,
                updated_at = ?
            WHERE id = ? AND is_deleted = 0
            """,
            (now, int(series_id)),
        )
    _remove_previous_series_cover(previous_cover)
    audit_account_action(
        request,
        "series.cover.restored_auto",
        "success",
        "video_series",
        str(series_id),
    )
    return {"status": "ok", "source": "auto"}


@router.get(
    "/api/manage/series/{series_id}/seasons/{season_id}/cover-editor"
)
def api_season_cover_editor(series_id: int, season_id: int):
    season = get_managed_season(series_id, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail="视频季不存在")
    data = list_season_videos(
        series_id,
        season_id,
        page=1,
        page_size=100,
    )
    episodes = []
    entry_video_id = None
    for item in data["items"]:
        video_id = item.get("video_id")
        if video_id is None:
            continue
        video_id = int(video_id)
        entry_video_id = entry_video_id or video_id
        episodes.append({
            "episode_id": int(item["episode_id"]),
            "episode_number": int(item["episode_number"]),
            "title": str(
                item.get("video_title")
                or item.get("episode_title")
                or item.get("display_title")
                or ""
            ),
            "video_id": video_id,
            "duration": item.get("duration"),
        })
    preview_row = {
        **season,
        "entry_video_id": entry_video_id,
    }
    return {
        "series_id": int(series_id),
        "season_id": int(season_id),
        "title": season.get("display_name") or season.get("name"),
        "cover_source": season.get("cover_source") or "inherited",
        "cover_url": season_cover_url(preview_row),
        "updated_at": season.get("updated_at") or "",
        "episodes": episodes,
    }


def _season_video_for_cover(series_id: int, season_id: int, video_id: int) -> dict:
    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT v.id, v.title, v.path, v.size, v.mtime_ns, v.duration,
                   v.thumbnail_path
            FROM video_episodes e
            JOIN video_seasons s ON s.id = e.season_id
            JOIN video_catalog v ON v.id = e.video_id
            WHERE s.series_id = ?
              AND s.id = ?
              AND s.is_deleted = 0
              AND e.video_id = ?
              AND v.missing = 0
              AND v.is_deleted = 0
            LIMIT 1
            """,
            (int(series_id), int(season_id), int(video_id)),
        ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=422,
            detail="所选视频不属于当前视频季或已不可用",
        )
    return dict(row)


def _season_representative_video(series_id: int, season_id: int) -> dict:
    season = get_managed_season(series_id, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail="视频季不存在")
    representative_id = season.get("representative_video_id")
    if representative_id is not None:
        video = database.get_video(int(representative_id))
        if video is not None and not int(video.get("is_deleted") or 0):
            return video
    data = list_season_videos(
        series_id,
        season_id,
        page=1,
        page_size=100,
    )
    for item in data["items"]:
        if item.get("video_id") is not None:
            video = database.get_video(int(item["video_id"]))
            if video is not None:
                return video
    raise HTTPException(status_code=422, detail="当前视频季没有可用视频")


def _remove_previous_season_cover(path_value: str) -> None:
    if not path_value:
        return
    path = Path(path_value)
    try:
        path.resolve().relative_to(config.SEASON_COVER_DIR.resolve())
    except (OSError, ValueError):
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _save_season_cover(
    request: Request,
    series_id: int,
    season_id: int,
    selection: SeasonCoverUpdate,
):
    season = get_managed_season(series_id, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail="视频季不存在")
    if season.get("active_delete_job_id"):
        raise HTTPException(status_code=409, detail="存在活动删除任务，不能修改封面")
    if (
        selection.expected_updated_at
        and str(season.get("updated_at") or "")
        != str(selection.expected_updated_at)
    ):
        raise HTTPException(status_code=409, detail="视频季已发生变化，请刷新后重试")

    cover_path = None
    source_video_id = None
    timestamp = None
    try:
        if selection.source == "inherited":
            video = _season_representative_video(series_id, season_id)
            source_video_id = int(video["id"])
            cover_path = Path(str(video.get("thumbnail_path") or ""))
            if not cover_path.is_file():
                cover_path = None
        else:
            if selection.video_id is None:
                raise HTTPException(status_code=422, detail="请选择剧集")
            if not math.isfinite(selection.timestamp) or selection.timestamp < 0:
                raise HTTPException(status_code=422, detail="封面时间点无效")
            source_video_id = int(selection.video_id)
            video = _season_video_for_cover(
                series_id,
                season_id,
                source_video_id,
            )
            duration = float(video.get("duration") or 0)
            timestamp = (
                min(selection.timestamp, max(0.0, duration - 0.1))
                if duration > 0
                else selection.timestamp
            )
            resolution = resolve_video_storage(video)
            if resolution["state"] == "external_offline":
                raise HTTPException(
                    status_code=409,
                    detail="视频所在外置硬盘当前未连接",
                )
            cover_path = generate_season_cover_from_video(
                series_id,
                season_id,
                resolution["path"],
                source_video_id,
                timestamp,
            )
    except FFmpegBusyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if selection.source == "video_frame" and cover_path is None:
        raise HTTPException(status_code=500, detail="视频季封面生成失败")

    now = database._now()
    previous_cover = str(season.get("cover_path") or "")
    with database.connect() as connection:
        connection.execute(
            """
            UPDATE video_seasons
            SET cover_path = ?, cover_source = ?,
                cover_source_video_id = ?, cover_timestamp = ?,
                cover_updated_at = ?, updated_at = ?
            WHERE id = ? AND series_id = ? AND is_deleted = 0
            """,
            (
                str(cover_path) if cover_path is not None else None,
                selection.source,
                source_video_id,
                timestamp,
                now,
                now,
                int(season_id),
                int(series_id),
            ),
        )
    if previous_cover and previous_cover != str(cover_path or ""):
        _remove_previous_season_cover(previous_cover)
    audit_account_action(
        request,
        "season.cover.updated",
        "success",
        "video_season",
        str(season_id),
        details={
            "series_id": int(series_id),
            "source": selection.source,
            "video_id": source_video_id,
            "timestamp": timestamp,
        },
    )
    return {
        "status": "ok",
        "source": selection.source,
        "cover_updated_at": now,
    }


@router.put(
    "/api/manage/series/{series_id}/seasons/{season_id}/cover"
)
def api_update_season_cover(
    request: Request,
    series_id: int,
    season_id: int,
    selection: SeasonCoverUpdate,
):
    return _save_season_cover(request, series_id, season_id, selection)


@router.delete(
    "/api/manage/series/{series_id}/seasons/{season_id}/cover"
)
def api_restore_season_cover(
    request: Request,
    series_id: int,
    season_id: int,
):
    season = get_managed_season(series_id, season_id)
    if season is None:
        raise HTTPException(status_code=404, detail="视频季不存在")
    return _save_season_cover(
        request,
        series_id,
        season_id,
        SeasonCoverUpdate(
            source="inherited",
            expected_updated_at=str(season.get("updated_at") or ""),
        ),
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
