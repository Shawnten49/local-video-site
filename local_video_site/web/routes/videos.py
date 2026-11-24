from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from local_video_site.core import config
from local_video_site import database
from local_video_site.identity.permissions.content import ACCESS_LEVEL_ADMIN, ACCESS_LEVEL_USER, ACCESS_LEVELS, ACCESS_LEVEL_LABELS, access_level_label, max_level_for, validate_access_level
from local_video_site.media.editing.rename import VideoRenameError, rename_video, update_video_display_title
from local_video_site.storage.volumes import active_migration_video_ids, decorate_video, decorate_video_cached
from local_video_site.media.editing.trim import VideoTrimError, cancel_trim_job, create_trim_job, delete_trim_backup, get_trim_job, probe_trim_options, restore_trim_backup
from ..formatting import format_bytes, format_duration, format_resolution
from ..audit import audit_account_action as _audit_account_action
from ..models.playback import (
    VideoAccessLevelUpdate,
    VideoDisplayTitleUpdate,
    VideoFileNameUpdate,
    VideoTrimCreate,
    VideoTrimPreviewRequest,
)
from ..templates import TEMPLATES
from ..urls import current_return_path, path_without_query_params


router = APIRouter()
LOGGER = logging.getLogger("local_video_site")


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    q: str = "",
    sort: str = "recent",
    page: int = Query(1, ge=1),
):
    identity = request.state.identity
    max_access_level = min(
        max_level_for(identity),
        ACCESS_LEVEL_USER,
    )
    favorite_access_level = max_level_for(identity)
    data = database.list_videos(
        query=q.strip(),
        directory="",
        sort=sort,
        page=page,
        page_size=36,
        group_seasons=True,
        max_access_level=max_access_level,
    )
    stats = database.library_stats(max_access_level=max_access_level)
    admin_only_stats = (
        database.library_stats(exact_access_level=ACCESS_LEVEL_ADMIN)
        if identity.is_admin
        else {"total": 0, "total_bytes": 0, "pending": 0}
    )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "data": data,
            "stats": stats,
            "identity": identity,
            "favorite_count": (
                database.user_favorite_count(
                    identity.user_id,
                    max_access_level=favorite_access_level,
                )
                if identity.can_favorite
                else 0
            ),
            "actor_count": database.actor_count(),
            "query": q,
            "sort": sort,
            "return_to": current_return_path(request),
            "page_mode": "home",
            "page_title": "本地视频库",
            "page_search_action": "/",
            "collection_title": "",
            "empty_title": "没有找到视频",
            "empty_message": "调整搜索条件，或重新扫描视频目录。",
            "pager_action": "/",
            "pager_input_id": "home-page-jump",
            "show_admin_only_entry": (
                identity.is_admin
                and int(admin_only_stats["total"] or 0) > 0
            ),
            "admin_only_stats": admin_only_stats,
            "format_duration": format_duration,
            "format_bytes": format_bytes,
            "format_resolution": format_resolution,
        },
    )


@router.get("/admin-videos", response_class=HTMLResponse)
def admin_only_videos(
    request: Request,
    q: str = "",
    sort: str = "recent",
    page: int = Query(1, ge=1),
):
    identity = request.state.identity
    if not identity.is_admin:
        raise HTTPException(status_code=403, detail="仅管理员可访问")
    data = database.list_videos(
        query=q.strip(),
        directory="",
        sort=sort,
        page=page,
        page_size=36,
        group_seasons=True,
        max_access_level=ACCESS_LEVEL_ADMIN,
        exact_access_level=ACCESS_LEVEL_ADMIN,
    )
    stats = database.library_stats(
        exact_access_level=ACCESS_LEVEL_ADMIN
    )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "data": data,
            "stats": stats,
            "identity": identity,
            "favorite_count": database.user_favorite_count(
                identity.user_id,
                max_access_level=max_level_for(identity),
            ),
            "actor_count": database.actor_count(),
            "query": q,
            "sort": sort,
            "return_to": current_return_path(request),
            "page_mode": "admin_only",
            "page_title": "专属视频 · 本地视频库",
            "page_search_action": "/admin-videos",
            "collection_title": "专属视频",
            "empty_title": "暂无专属视频",
            "empty_message": "当前没有访问级别为管理员的视频。",
            "pager_action": "/admin-videos",
            "pager_input_id": "admin-videos-page-jump",
            "show_admin_only_entry": False,
            "admin_only_stats": stats,
            "format_duration": format_duration,
            "format_bytes": format_bytes,
            "format_resolution": format_resolution,
        },
        headers={"Cache-Control": "no-store"},
    )


def _admin_video_page(
    query: str = "",
    storage_state: str = "",
    record_state: str = "all",
    access_level: str = "",
    page: int = 1,
) -> Dict:
    record_state = str(record_state or "").strip().lower()
    if record_state not in {"active", "deleted", "all"}:
        record_state = "all"
    selected_access_level = None
    if access_level:
        try:
            selected_access_level = validate_access_level(access_level)
        except ValueError:
            access_level = ""
    data = database.admin_video_page(
        query=query,
        record_state=record_state,
        access_level=selected_access_level,
        storage_state=storage_state,
        page=page,
        page_size=config.video_management_page_size(),
    )
    active_ids = active_migration_video_ids()
    items = [
        decorate_video_cached(item, active_ids)
        for item in data["items"]
    ]
    for item in items:
        item["quality_label"] = format_resolution(
            item.get("width"),
            item.get("height"),
        )
        video_path = Path(str(item.get("path") or ""))
        item["file_name"] = video_path.name
        item["file_stem"] = video_path.stem
    return {
        **data,
        "items": items,
        "record_state": record_state,
        "access_level": access_level,
    }


VIDEO_TRIM_STATUS_LABELS = {
    "pending": "等待中",
    "running": "进行中",
    "success": "成功",
    "failed": "失败",
    "cancelled": "已取消",
}
VIDEO_TRIM_STAGE_LABELS = {
    "waiting": "等待",
    "preflight": "检查文件",
    "trimming": "正在剪辑",
    "verifying": "验证输出",
    "committing": "提交文件",
    "finished": "完成",
}


@router.get("/manage/videos/trims", response_class=HTMLResponse)
def video_trim_jobs_page(
    request: Request,
    q: str = "",
    status: str = "",
    page: int = Query(1, ge=1),
):
    identity = request.state.identity
    data = database.list_video_trim_jobs(
        query=q.strip(),
        status=status.strip(),
        page=page,
        page_size=30,
    )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="video_trims.html",
        context={
            "data": data,
            "query": q,
            "status": status,
            "status_labels": VIDEO_TRIM_STATUS_LABELS,
            "stage_labels": VIDEO_TRIM_STAGE_LABELS,
            "identity": identity,
            "download_counts": database.download_task_counts(
                owner_user_id=identity.user_id,
                owned_only=False,
            ),
            "active_page": "videos",
            "pager_action": "/manage/videos/trims",
            "pager_params": {
                "q": q,
                "status": status,
            },
            "format_bytes": format_bytes,
            "format_duration": format_duration,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/manage/videos", response_class=HTMLResponse)
def manage_videos(
    request: Request,
    q: str = "",
    storage: str = "",
    record_state: str = "all",
    access_level: str = "",
    page: int = Query(1, ge=1),
):
    if "directory" in request.query_params:
        return RedirectResponse(
            path_without_query_params(request, "directory"),
            status_code=302,
        )
    identity = request.state.identity
    data = _admin_video_page(
        query=q.strip(),
        storage_state=storage.strip(),
        record_state=record_state.strip(),
        access_level=access_level.strip(),
        page=page,
    )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="manage_videos.html",
        headers={"Cache-Control": "no-store"},
        context={
            "data": data,
            "query": q,
            "storage": storage,
            "record_state": data["record_state"],
            "access_level": data["access_level"],
            "access_levels": ACCESS_LEVELS,
            "access_level_labels": ACCESS_LEVEL_LABELS,
            "identity": identity,
            "download_counts": database.download_task_counts(
                owner_user_id=identity.user_id,
                owned_only=False,
            ),
            "active_page": "videos",
            "pager_action": "/manage/videos",
            "pager_params": {
                "q": q,
                "storage": storage,
                "record_state": data["record_state"],
                "access_level": data["access_level"],
            },
        },
    )


@router.get("/api/manage/videos")
def api_manage_videos(
    q: str = "",
    storage: str = "",
    record_state: str = "all",
    access_level: str = "",
    page: int = Query(1, ge=1),
):
    return _admin_video_page(
        query=q.strip(),
        storage_state=storage.strip(),
        record_state=record_state.strip(),
        access_level=access_level.strip(),
        page=page,
    )


@router.get("/api/manage/videos/{video_id}")
def api_manage_video(video_id: int):
    video = database.get_video_admin(video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="视频不存在")
    video = decorate_video(video, active_migration_video_ids())
    video["access_level_label"] = access_level_label(
        video.get("access_level")
    )
    with database.connect() as connection:
        categories = [
            dict(row)
            for row in connection.execute(
                """
                SELECT c.id, c.name
                FROM categories c
                JOIN video_categories vc ON vc.category_id = c.id
                WHERE vc.video_id = ?
                ORDER BY c.name COLLATE NOCASE
                """,
                (int(video_id),),
            ).fetchall()
        ]
        actors = [
            dict(row)
            for row in connection.execute(
                """
                SELECT a.id, a.name
                FROM actors a
                JOIN video_actors va ON va.actor_id = a.id
                WHERE va.video_id = ?
                ORDER BY a.name COLLATE NOCASE
                """,
                (int(video_id),),
            ).fetchall()
        ]
        manual_categories = [
            dict(row)
            for row in connection.execute(
                """
                SELECT mc.id, mc.name
                FROM manual_categories mc
                JOIN video_manual_categories vmc
                  ON vmc.manual_category_id = mc.id
                WHERE vmc.video_id = ?
                ORDER BY mc.name COLLATE NOCASE
                """,
                (int(video_id),),
            ).fetchall()
        ]
        download_tasks = [
            dict(row)
            for row in connection.execute(
                """
                SELECT id, title, status, stage, output_dir, file_path,
                       created_at, finished_at
                FROM download_tasks
                WHERE video_id = ?
                ORDER BY id DESC
                """,
                (int(video_id),),
            ).fetchall()
        ]
        migrations = [
            dict(row)
            for row in connection.execute(
                """
                SELECT id, batch_id, direction, mode, status, stage,
                       source_path, destination_path, created_at,
                       finished_at, error_code, error_detail
                FROM video_migrations
                WHERE video_id = ?
                ORDER BY id DESC
                """,
                (int(video_id),),
            ).fetchall()
        ]
    return {
        "video": video,
        "display": {
            "size": format_bytes(video.get("size")),
            "duration": format_duration(video.get("duration")),
            "resolution": format_resolution(
                video.get("width"),
                video.get("height"),
            ),
            "file_name": Path(video.get("path") or "").name,
            "file_stem": Path(video.get("path") or "").stem,
            "default_title": video.get("default_title") or video.get("title"),
        },
        "favorite": database.is_favorite(video_id),
        "categories": categories,
        "actors": actors,
        "manual_categories": manual_categories,
        "download_tasks": download_tasks,
        "migrations": migrations,
    }


@router.patch("/api/manage/videos/{video_id}/access-level")
def api_update_video_access_level(
    request: Request,
    video_id: int,
    selection: VideoAccessLevelUpdate,
):
    try:
        result = database.set_video_access_level(
            video_id,
            validate_access_level(selection.access_level),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit_account_action(
        request,
        "video.access_level_changed",
        "success",
        "video",
        str(video_id),
        details={
            "old_level": result["old_access_level"],
            "new_level": result["access_level"],
            "affected_video_ids": result["affected_video_ids"],
        },
    )
    result["access_level_label"] = access_level_label(
        result["access_level"]
    )
    return result


@router.patch("/api/manage/videos/{video_id}/display-title")
def api_update_video_display_title(
    request: Request,
    video_id: int,
    selection: VideoDisplayTitleUpdate,
):
    try:
        result = update_video_display_title(
            video_id,
            selection.display_title,
            expected_updated_at=selection.expected_updated_at,
        )
    except VideoRenameError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
        ) from exc
    _audit_account_action(
        request,
        "video.display_title_changed",
        "success",
        "video",
        str(video_id),
        details={
            "old_title": result.get("old_title"),
            "new_title": result.get("title"),
            "display_title_override": result.get(
                "display_title_override"
            ),
        },
    )
    result["title_changed"] = True
    result["path_changed"] = False
    return result


@router.patch("/api/manage/videos/{video_id}/file-name")
def api_update_video_file_name(
    request: Request,
    video_id: int,
    selection: VideoFileNameUpdate,
):
    if database.get_video_admin(video_id) is None:
        raise HTTPException(status_code=404, detail="视频不存在")
    try:
        result = rename_video(
            video_id=video_id,
            requested_title=selection.file_stem,
            expected_path=selection.expected_path,
        )
    except VideoRenameError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
        ) from exc
    _audit_account_action(
        request,
        "video.file_name_changed",
        "success",
        "video",
        str(video_id),
        details={
            "old_path": selection.expected_path,
            "new_path": result.get("path"),
            "title_changed": False,
        },
    )
    result["title_changed"] = False
    return result


def _video_trim_http_error(exc: VideoTrimError) -> HTTPException:
    if exc.code in {"video_not_found", "job_not_found"}:
        status_code = 404
    elif exc.code in {
        "trim_busy",
        "restore_busy",
        "stale_video",
        "storage_offline",
        "video_file_missing",
        "backup_unavailable",
        "backup_missing",
        "backup_corrupt",
        "backup_invalid",
    }:
        status_code = 409
    else:
        status_code = 422
    return HTTPException(status_code=status_code, detail=str(exc))


@router.post("/api/manage/videos/{video_id}/trim/preview")
def api_video_trim_preview(
    video_id: int,
    selection: VideoTrimPreviewRequest,
):
    try:
        return probe_trim_options(video_id, selection.start_seconds)
    except VideoTrimError as exc:
        raise _video_trim_http_error(exc) from exc


@router.post("/api/manage/videos/{video_id}/trim")
def api_create_video_trim(
    request: Request,
    video_id: int,
    selection: VideoTrimCreate,
):
    identity = request.state.identity
    try:
        job = create_trim_job(
            video_id=video_id,
            start_seconds=selection.start_seconds,
            mode=selection.mode,
            expected_path=selection.expected_path,
            expected_mtime_ns=selection.expected_mtime_ns,
            created_by_user_id=identity.user_id,
            created_by_username=identity.username,
        )
    except VideoTrimError as exc:
        raise _video_trim_http_error(exc) from exc
    _audit_account_action(
        request,
        "video.trim_created",
        "success",
        "video_trim_job",
        job["job_id"],
        details={
            "video_id": video_id,
            "start_seconds": selection.start_seconds,
            "mode": selection.mode,
            "actual_cut_seconds": job.get("actual_cut_seconds"),
        },
    )
    return job


@router.get("/api/video-trim-jobs/{job_id}")
def api_video_trim_job(request: Request, job_id: str):
    if not request.state.identity.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    job = get_trim_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="剪辑任务不存在")
    return job


@router.post("/api/video-trim-jobs/{job_id}/cancel")
def api_cancel_video_trim(request: Request, job_id: str):
    if not request.state.identity.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    if not cancel_trim_job(job_id):
        raise HTTPException(status_code=409, detail="当前剪辑任务不能取消")
    return {"status": "cancelled"}


@router.post("/api/video-trim-jobs/{job_id}/backup/delete")
def api_delete_video_trim_backup(request: Request, job_id: str):
    identity = request.state.identity
    if not identity.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    try:
        job = delete_trim_backup(
            job_id,
            actor_user_id=identity.user_id,
            actor_username=identity.username,
        )
    except VideoTrimError as exc:
        raise _video_trim_http_error(exc) from exc
    _audit_account_action(
        request,
        "video.trim_backup_deleted",
        "success",
        "video_trim_job",
        job_id,
        details={"video_id": job.get("video_id")},
    )
    return job


@router.post("/api/video-trim-jobs/{job_id}/restore")
def api_restore_video_trim_backup(request: Request, job_id: str):
    identity = request.state.identity
    if not identity.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    try:
        job = restore_trim_backup(
            job_id,
            actor_user_id=identity.user_id,
            actor_username=identity.username,
        )
    except VideoTrimError as exc:
        raise _video_trim_http_error(exc) from exc
    _audit_account_action(
        request,
        "video.trim_backup_restored",
        "success",
        "video_trim_job",
        job_id,
        details={
            "video_id": job.get("video_id"),
            "restore_count": job.get("restore_count"),
        },
    )
    return job


DOWNLOAD_STATUS_LABELS = {
    "pending": "待下载",
    "running": "下载中",
    "pausing": "暂停中",
    "paused": "已暂停",
    "success": "成功",
    "failed": "失败",
    "cancelled": "已取消",
    "deleting": "删除中",
}
DOWNLOAD_STAGE_LABELS = {
    "waiting": "等待",
    "opening_page": "打开网页",
    "downloading": "下载视频",
    "recovering": "自动恢复",
    "refreshing_media": "刷新媒体地址",
    "validating_resume": "校验断点",
    "resuming": "断点续传",
    "pausing": "保存断点",
    "paused": "已暂停",
    "normalizing": "视频标准化",
    "registering": "写入视频库",
    "metadata": "元数据",
    "categories": "分类",
    "actors": "演员",
    "finished": "完成",
    "deleting": "正在删除",
    "delete_failed": "删除失败",
    "deleted": "已删除",
}


DOWNLOAD_LIVE_STATUSES = {
    "pending",
    "running",
    "pausing",
    "deleting",
}


@router.get("/api/videos")
def api_videos(
    q: str = "",
    directory: str = "",
    category_id: Optional[int] = None,
    actor_id: Optional[int] = None,
    sort: str = "recent",
    page: int = Query(1, ge=1),
    page_size: int = Query(36, ge=1, le=120),
):
    return database.list_videos(
        query=q,
        directory=directory,
        category_id=category_id,
        actor_id=actor_id,
        sort=sort,
        page=page,
        page_size=page_size,
    )
