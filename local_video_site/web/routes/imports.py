from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from local_video_site.core import config
from local_video_site import database
from local_video_site.identity.permissions.content import ACCESS_LEVELS, ACCESS_LEVEL_LABELS, validate_access_level
from local_video_site.storage.deletion import delete_password_configured, verify_delete_password
from local_video_site.ingestion.import_tasks import IMPORT_STATUS_LABELS, ImportError as VideoImportError, create_import_batch, get_import_jobs_by_ids, import_job_counts, list_import_entries, list_import_jobs, preview_import
from local_video_site.storage.volumes import list_directories as list_storage_directories
from ..delete_rate import (
    clear_delete_failures as _clear_delete_failures,
    delete_rate_allowed as _delete_rate_allowed,
    delete_rate_key as _delete_rate_key,
    record_delete_failure as _record_delete_failure,
)
from ..models.scans import ExternalImportCreate
from ..templates import TEMPLATES


router = APIRouter()
LOGGER = logging.getLogger("local_video_site")


@router.get("/manage/migrations/import", response_class=HTMLResponse)
def external_video_import(request: Request):
    default_destination = config.PROJECT_ROOT / "download-import"
    default_destination.mkdir(parents=True, exist_ok=True)
    return TEMPLATES.TemplateResponse(
        request=request,
        name="import_external.html",
        context={
            "default_destination": str(default_destination),
            "default_source": "",
            "access_levels": ACCESS_LEVELS,
            "access_level_labels": ACCESS_LEVEL_LABELS,
        },
    )


@router.get("/api/manage/import/browse")
def api_import_browse(path: str = "/Volumes"):
    try:
        return list_import_entries(path)
    except VideoImportError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


@router.get("/api/manage/import/preview")
def api_import_preview(
    source_path: str,
    destination_root: str,
):
    try:
        return preview_import(source_path, destination_root)
    except VideoImportError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


@router.get("/api/manage/import/jobs")
def api_import_jobs(
    q: str = "",
    status: str = "",
    access_level: Optional[int] = Query(None, ge=0, le=2),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
):
    return list_import_jobs(
        query=q.strip(),
        status=status.strip(),
        access_level=access_level,
        page=page,
        page_size=page_size,
    )


@router.get("/api/manage/import/jobs/live")
def api_import_jobs_live(ids: str = Query(..., min_length=1)):
    try:
        job_ids = sorted({
            int(value)
            for value in ids.split(",")
            if value.strip()
        })
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="导入任务 ID 格式无效",
        ) from exc
    if not job_ids or len(job_ids) > 100:
        raise HTTPException(status_code=422, detail="导入任务 ID 数量无效")
    return {"items": get_import_jobs_by_ids(job_ids)}


@router.post("/api/manage/import/batches")
def api_create_import_batch(
    request: Request,
    selection: ExternalImportCreate,
):
    if selection.delete_source:
        key = _delete_rate_key(request, -2)
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
        result = create_import_batch(
            source_path=selection.source_path,
            destination_root=selection.destination_root,
            delete_source=selection.delete_source,
            access_level=selection.access_level,
            verification_mode=selection.verification_mode,
        )
    except VideoImportError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    LOGGER.info(
        "External import batch created batch_id=%s created=%s skipped=%s "
        "delete_source=%s access_level=%s",
        result["batch_id"],
        result["created"],
        result["skipped"],
        selection.delete_source,
        selection.access_level,
        extra={"operation": "video.import.batch.create"},
    )
    return result
