from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from local_video_site.core import config
from local_video_site import database
from local_video_site.identity.permissions.content import ACCESS_LEVELS, ACCESS_LEVEL_LABELS, access_level_label, validate_access_level
from local_video_site.storage.deletion import delete_password_configured, verify_delete_password
from local_video_site.storage.migration_tasks import MIGRATION_STAGE_LABELS, MIGRATION_STATUS_LABELS, create_batch as create_migration_batch, list_candidates as list_migration_candidates, list_migrations, migration_counts, request_cancel as cancel_migration, retry_migration
from local_video_site.media.editing.format import STAGE_LABELS as FORMAT_STAGE_LABELS, STATUS_LABELS as FORMAT_STATUS_LABELS, VideoFormatError, create_jobs as create_format_jobs, delete_backup as delete_format_backup, get_jobs_by_ids as get_format_jobs_by_ids, job_counts as format_job_counts, list_jobs as list_format_jobs, request_cancel as cancel_format_job, restore_backup as restore_format_backup, retry_job as retry_format_job
from local_video_site.ingestion.library_scan import ScanAlreadyRunning
from local_video_site.ingestion.import_tasks import IMPORT_STATUS_LABELS, import_job_counts, list_import_jobs
from local_video_site.storage.volumes import StorageError, active_migration_video_ids, decorate_video_cached, list_directories as list_storage_directories, refresh_storage_volumes
from ..audit import audit_account_action as _audit_account_action
from ..delete_rate import (
    clear_delete_failures as _clear_delete_failures,
    delete_rate_allowed as _delete_rate_allowed,
    delete_rate_key as _delete_rate_key,
    record_delete_failure as _record_delete_failure,
)
from ..models.playback import VideoFormatJobCreate
from ..models.scans import MigrationBatchCreate
from ..templates import TEMPLATES


router = APIRouter()
LOGGER = logging.getLogger("local_video_site")


@router.get("/manage/migrations", response_class=HTMLResponse)
def manage_migrations(
    request: Request,
    tab: str = "migrations",
    q: str = "",
    status: str = "",
    mode: str = "",
    direction: str = "",
    page: int = Query(1, ge=1),
    import_q: str = "",
    import_status: str = "",
    import_access_level: str = "",
    import_page: int = Query(1, ge=1),
    format_q: str = "",
    format_status: str = "",
    format_page: int = Query(1, ge=1),
):
    active_tab = (
        tab if tab in {"migrations", "imports", "formats"} else "migrations"
    )
    migration_data = None
    import_jobs = None
    format_jobs = None
    selected_import_access_level = None
    if import_access_level:
        try:
            selected_import_access_level = validate_access_level(
                import_access_level
            )
        except ValueError:
            import_access_level = ""
    if active_tab == "migrations":
        migration_data = list_migrations(
            query=q.strip(),
            status=status.strip(),
            mode=mode.strip(),
            direction=direction.strip(),
            page=page,
            page_size=config.migrations_page_size(),
        )
    elif active_tab == "imports":
        import_jobs = list_import_jobs(
            query=import_q.strip(),
            status=import_status.strip(),
            access_level=selected_import_access_level,
            page=import_page,
            page_size=config.migrations_page_size(),
        )
    else:
        format_jobs = list_format_jobs(
            query=format_q.strip(),
            status=format_status.strip(),
            page=format_page,
            page_size=config.migrations_page_size(),
        )
    migration_status_counts = migration_counts()
    import_status_counts = import_job_counts()
    format_status_counts = format_job_counts()
    return TEMPLATES.TemplateResponse(
        request=request,
        name="migrations.html",
        context={
            "active_tab": active_tab,
            "migration_data": migration_data,
            "import_jobs": import_jobs,
            "format_jobs": format_jobs,
            "migration_counts": migration_status_counts,
            "import_counts": import_status_counts,
            "format_counts": format_status_counts,
            "query": q,
            "status": status,
            "mode": mode,
            "direction": direction,
            "import_q": import_q,
            "import_status": import_status,
            "import_access_level": import_access_level,
            "format_q": format_q,
            "format_status": format_status,
            "status_labels": MIGRATION_STATUS_LABELS,
            "stage_labels": MIGRATION_STAGE_LABELS,
            "import_status_labels": IMPORT_STATUS_LABELS,
            "format_status_labels": FORMAT_STATUS_LABELS,
            "format_stage_labels": FORMAT_STAGE_LABELS,
            "access_level_labels": ACCESS_LEVEL_LABELS,
            "access_levels": ACCESS_LEVELS,
        },
    )


@router.get("/api/manage/migrations")
def api_manage_migrations(
    q: str = "",
    status: str = "",
    mode: str = "",
    direction: str = "",
    page: int = Query(1, ge=1),
):
    return list_migrations(
        query=q.strip(),
        status=status.strip(),
        mode=mode.strip(),
        direction=direction.strip(),
        page=page,
        page_size=config.migrations_page_size(),
    )


@router.get("/api/manage/migrations/live")
def api_migrations_live(ids: str = Query(..., min_length=1)):
    try:
        migration_ids = sorted({
            int(value)
            for value in ids.split(",")
            if value.strip()
        })
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="迁移 ID 格式无效",
        ) from exc
    if not migration_ids or len(migration_ids) > 100:
        raise HTTPException(status_code=422, detail="迁移 ID 数量无效")
    placeholders = ",".join("?" for _ in migration_ids)
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT id, status, stage, copied_bytes, total_bytes,
                   progress_percent, error_code, error_detail,
                   updated_at
            FROM video_migrations
            WHERE id IN ({})
            """.format(placeholders),
            migration_ids,
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["status_label"] = MIGRATION_STATUS_LABELS.get(
            item["status"],
            item["status"],
        )
        item["stage_label"] = MIGRATION_STAGE_LABELS.get(
            item["stage"],
            item["stage"],
        )
        items.append(item)
    return {"items": items}


@router.get("/api/manage/format-jobs")
def api_format_jobs(
    q: str = "",
    status: str = "",
    page: int = Query(1, ge=1),
):
    return list_format_jobs(
        query=q.strip(),
        status=status.strip(),
        page=page,
        page_size=config.migrations_page_size(),
    )


@router.get("/api/manage/format-jobs/live")
def api_format_jobs_live(ids: str = Query(..., min_length=1)):
    try:
        job_ids = sorted({
            int(value)
            for value in ids.split(",")
            if value.strip()
        })
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="格式任务 ID 格式无效",
        ) from exc
    if not job_ids or len(job_ids) > 100:
        raise HTTPException(status_code=422, detail="格式任务 ID 数量无效")
    return {"items": get_format_jobs_by_ids(job_ids)}


@router.post("/api/manage/videos/{video_id}/format-job")
def api_create_format_jobs(
    request: Request,
    video_id: int,
    selection: VideoFormatJobCreate,
):
    if not selection.confirmed or selection.confirmation_version != 1:
        raise HTTPException(
            status_code=422,
            detail="请先确认格式处理方式",
        )
    if selection.operation == "standard_transcode" and not selection.confirm_lossy:
        raise HTTPException(
            status_code=422,
            detail="编码转译是有损操作，需要额外确认",
        )
    try:
        result = create_format_jobs(
            [video_id],
            operation=selection.operation,
            confirmed=selection.confirmed,
            confirm_lossy=selection.confirm_lossy,
            created_by_user_id=request.state.identity.user_id,
            created_by_username=request.state.identity.username,
        )
    except VideoFormatError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    _audit_account_action(
        request,
        "video.format.created",
        "success",
        "video_format_batch",
        result["batch_id"],
        details=result,
    )
    return result


@router.post("/api/manage/format-jobs/{job_id}/retry")
def api_retry_format_job(request: Request, job_id: int):
    if not retry_format_job(job_id):
        raise HTTPException(
            status_code=409,
            detail="当前格式任务不能重试",
        )
    _audit_account_action(
        request,
        "video.format.retried",
        "success",
        "video_format_job",
        job_id,
    )
    return {"status": "pending"}


@router.post("/api/manage/format-jobs/{job_id}/backup/delete")
def api_delete_format_backup(request: Request, job_id: int):
    try:
        deleted = delete_format_backup(job_id)
    except VideoFormatError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    if not deleted:
        raise HTTPException(
            status_code=409,
            detail="当前任务没有可删除的安全备份",
        )
    _audit_account_action(
        request,
        "video.format.backup_deleted",
        "success",
        "video_format_job",
        job_id,
    )
    return {"status": "deleted"}


@router.post("/api/manage/format-jobs/{job_id}/restore")
def api_restore_format_backup(request: Request, job_id: int):
    try:
        result = restore_format_backup(job_id)
    except (VideoFormatError, ScanAlreadyRunning) as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
        ) from exc
    _audit_account_action(
        request,
        "video.format.restored",
        "success",
        "video_format_job",
        job_id,
    )
    return result


@router.post("/api/manage/format-jobs/{job_id}/cancel")
def api_cancel_format_job(request: Request, job_id: int):
    if not cancel_format_job(job_id):
        raise HTTPException(
            status_code=409,
            detail="当前格式任务不能取消",
        )
    _audit_account_action(
        request,
        "video.format.cancelled",
        "success",
        "video_format_job",
        job_id,
    )
    return {"status": "cancelled"}


@router.get("/manage/migrations/new", response_class=HTMLResponse)
def new_migration(
    request: Request,
    mode: str = "manual",
    direction: str = "to_external",
    q: str = "",
    page: int = Query(1, ge=1),
):
    if mode not in {"manual", "auto"}:
        raise HTTPException(status_code=422, detail="不支持的迁移模式")
    if direction not in {"to_external", "to_local"}:
        raise HTTPException(status_code=422, detail="不支持的迁移方向")
    if mode == "auto" and direction != "to_external":
        raise HTTPException(
            status_code=422,
            detail="自动迁移只支持迁往外置盘",
        )
    candidates = list_migration_candidates(
        direction=direction,
        query=q.strip(),
        page=page,
        page_size=40,
    )
    selected_ids = [
        int(value)
        for value in request.query_params.getlist("video_id")
        if str(value).isdigit()
    ]
    if not selected_ids:
        selected_ids = [
            int(value)
            for value in request.query_params.get("video_ids", "").split(",")
            if value.isdigit()
        ]
    return TEMPLATES.TemplateResponse(
        request=request,
        name="migration_new.html",
        context={
            "mode": mode,
            "direction": direction,
            "candidates": candidates,
            "query": q,
            "selected_ids": selected_ids,
            "cutoff_days": config.migration_auto_age_days(),
            "default_target": (
                str(config.PROJECT_ROOT)
                if direction == "to_local"
                else "/Volumes"
            ),
        },
    )


@router.get("/api/manage/migrations/candidates")
def api_migration_candidates(
    direction: str = "to_external",
    q: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=100),
):
    try:
        return list_migration_candidates(
            direction=direction,
            query=q.strip(),
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/manage/storage/directories")
def api_storage_directories(path: str = "/Volumes"):
    try:
        return list_storage_directories(path)
    except StorageError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


@router.post("/api/manage/storage/refresh")
def api_refresh_storage_volumes():
    threading.Thread(
        target=refresh_storage_volumes,
        name="manual-storage-volume-refresh",
        daemon=True,
    ).start()
    return {"status": "refreshing"}


@router.post("/api/manage/migrations/batches")
def api_create_migration_batch(
    request: Request,
    selection: MigrationBatchCreate,
):
    key = _delete_rate_key(request, -1)
    if not _delete_rate_allowed(key):
        raise HTTPException(
            status_code=429,
            detail="密码尝试次数过多，请稍后再试",
        )
    if not delete_password_configured():
        raise HTTPException(
            status_code=409,
            detail="尚未配置删除校验码，请由管理员在参数配置中设置",
        )
    if not verify_delete_password(selection.password):
        _record_delete_failure(key)
        raise HTTPException(status_code=401, detail="删除校验码错误")
    _clear_delete_failures(key)
    try:
        result = create_migration_batch(
            mode=selection.mode,
            direction=selection.direction,
            destination_root=selection.destination_root,
            video_ids=selection.video_ids,
            cutoff_days=selection.cutoff_days,
            verification_mode=selection.verification_mode,
        )
    except (ValueError, StorageError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    LOGGER.info(
        "Video migration batch created batch_id=%s direction=%s mode=%s "
        "created=%s skipped=%s",
        result["batch_id"],
        selection.direction,
        selection.mode,
        result["created"],
        len(result["skipped"]),
        extra={"operation": "video.migration.batch.create"},
    )
    return result


@router.post("/api/manage/migrations/{migration_id}/retry")
def api_retry_migration(migration_id: int):
    if not retry_migration(migration_id):
        raise HTTPException(status_code=409, detail="当前迁移任务不能重试")
    return {"status": "pending"}


@router.post("/api/manage/migrations/{migration_id}/cancel")
def api_cancel_migration(migration_id: int):
    if not cancel_migration(migration_id):
        raise HTTPException(status_code=409, detail="当前迁移任务不能取消")
    return {"status": "cancelled"}
