from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse

from local_video_site.core import config
from local_video_site import database
from local_video_site.identity.permissions.content import ACCESS_LEVELS, ACCESS_LEVEL_LABELS, validate_access_level
from local_video_site.ingestion.external_scan import ExternalScanError, commit_scan as commit_external_scan, create_scan as create_external_scan, get_scan as get_external_scan, get_scan_asset as get_external_scan_asset, get_season_assignment_preview, get_series_assignment_preview, list_scans as list_external_scans, preview_scan_source, request_cancel as cancel_external_scan, retry_failed_scan
from local_video_site.storage.volumes import list_directories as list_storage_directories
from local_video_site.ingestion.import_tasks import ImportError as VideoImportError, list_import_entries
from local_video_site.ingestion.library_scan import SCAN_RUNTIME, ScanAlreadyRunning, process_pending_metadata, repair_missing_thumbnails
from ..audit import audit_account_action as _audit_account_action
from ..lifespan import _scan_index, _start_background_indexing
from ..models.scans import (
    ExternalScanCommit,
    ExternalScanCreate,
)
from ..templates import TEMPLATES


router = APIRouter()
LOGGER = logging.getLogger("local_video_site")


@router.get("/manage/videos/external-scan", response_class=HTMLResponse)
def external_scan_page(request: Request):
    return TEMPLATES.TemplateResponse(
        request=request,
        name="external_scan.html",
        context={
            "recent_scans": list_external_scans(limit=20),
            "default_source": "/Volumes",
            "access_levels": ACCESS_LEVELS,
            "access_level_labels": ACCESS_LEVEL_LABELS,
        },
    )


@router.get("/manage/videos/external-scan/{scan_id}", response_class=HTMLResponse)
def external_scan_detail_page(request: Request, scan_id: str):
    scan = get_external_scan(scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="扫描任务不存在")
    return TEMPLATES.TemplateResponse(
        request=request,
        name="external_scan_detail.html",
        context={
            "scan": scan,
            "access_levels": ACCESS_LEVELS,
            "access_level_labels": ACCESS_LEVEL_LABELS,
        },
    )


@router.get("/api/manage/external-scans/browse")
def api_external_scan_browse(path: str = "/Volumes"):
    try:
        return list_import_entries(path)
    except VideoImportError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


@router.get("/api/manage/external-scans/preview")
def api_external_scan_preview(
    source_path: str,
    scan_mode: str = "batch",
):
    try:
        return preview_scan_source(source_path, scan_mode=scan_mode)
    except ExternalScanError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


@router.get("/api/manage/external-scans")
def api_external_scans(
    limit: int = Query(30, ge=1, le=100),
):
    return list_external_scans(limit=limit)


@router.post("/api/manage/external-scans")
def api_create_external_scan(
    request: Request,
    selection: ExternalScanCreate,
):
    identity = request.state.identity
    try:
        result = create_external_scan(
            source_path=selection.source_path,
            scan_mode=selection.scan_mode,
            access_level=selection.access_level,
            series_name=selection.series_name,
            season_name=selection.season_name,
            season_number=selection.season_number,
            groups=[group.model_dump() for group in selection.groups],
            attach_to_series=selection.attach_to_series,
            target_series_id=selection.target_series_id,
            attach_to_season=selection.attach_to_season,
            target_season_id=selection.target_season_id,
            created_by_user_id=identity.user_id,
            created_by_username=identity.username,
        )
    except ExternalScanError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    _audit_account_action(
        request,
        "external_scan.created",
        "success",
        "external_scan",
        result["scan_id"],
        details={
            "scan_mode": result["scan_mode"],
            "source_path": result["source_path"],
            "access_level": result["access_level"],
            "target_series_id": result.get("target_series_id"),
            "target_season_id": result.get("target_season_id"),
        },
    )
    LOGGER.info(
        "External scan created scan_id=%s mode=%s files=%s",
        result["scan_id"],
        result["scan_mode"],
        result["total_files"],
        extra={"operation": "external.scan.created"},
    )
    return result


@router.get("/api/manage/series/{series_id}/assignment-preview")
def api_series_assignment_preview(series_id: int):
    try:
        return get_series_assignment_preview(series_id)
    except ExternalScanError as exc:
        status_code = (
            404 if exc.code == "target_series_not_found" else 409
        )
        raise HTTPException(
            status_code=status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


@router.get("/api/manage/seasons/{season_id}/assignment-preview")
def api_season_assignment_preview(season_id: int):
    try:
        return get_season_assignment_preview(season_id)
    except ExternalScanError as exc:
        status_code = (
            404 if exc.code == "target_season_not_found" else 409
        )
        raise HTTPException(
            status_code=status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


@router.get("/api/manage/external-scans/{scan_id}")
def api_external_scan_detail(scan_id: str):
    result = get_external_scan(scan_id)
    if result is None:
        raise HTTPException(status_code=404, detail="扫描任务不存在")
    return result


@router.post("/api/manage/external-scans/{scan_id}/commit")
def api_commit_external_scan(
    request: Request,
    scan_id: str,
    selection: ExternalScanCommit,
):
    levels = {
        int(item.item_id): int(item.access_level)
        for item in selection.items
    }
    episode_numbers = {
        int(item.item_id): item.episode_number
        for item in selection.items
    }
    try:
        result = commit_external_scan(
            scan_id=scan_id,
            item_access_levels=levels,
            selected_item_ids=list(levels),
            item_episode_numbers=episode_numbers,
            logger=LOGGER,
        )
    except ExternalScanError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    _audit_account_action(
        request,
        "external_scan.committed",
        "success",
        "external_scan",
        scan_id,
        details=result,
    )
    return result


@router.post("/api/manage/external-scans/{scan_id}/cancel")
def api_cancel_external_scan(request: Request, scan_id: str):
    if not cancel_external_scan(scan_id):
        raise HTTPException(
            status_code=409,
            detail="当前扫描任务不能取消",
        )
    _audit_account_action(
        request,
        "external_scan.cancelled",
        "success",
        "external_scan",
        scan_id,
    )
    return {"status": "cancelled"}


@router.post("/api/manage/external-scans/{scan_id}/retry")
def api_retry_external_scan(request: Request, scan_id: str):
    try:
        retry_failed_scan(scan_id)
    except ExternalScanError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    _audit_account_action(
        request,
        "external_scan.retried",
        "success",
        "external_scan",
        scan_id,
    )
    return {"status": "pending"}


@router.get(
    "/api/manage/external-scans/{scan_id}/items/{item_id}/thumbnail"
)
def api_external_scan_thumbnail(scan_id: str, item_id: int):
    path = get_external_scan_asset(scan_id, item_id, "thumbnail")
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="缩略图不存在")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/api/manage/external-scans/{scan_id}/items/{item_id}/preview")
def api_external_scan_preview_video(scan_id: str, item_id: int):
    path = get_external_scan_asset(scan_id, item_id, "preview")
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="预览片段不存在")
    return FileResponse(
        path,
        media_type="video/mp4",
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/api/scan/status")
def scan_status():
    return SCAN_RUNTIME.snapshot()


@router.post("/api/scan")
def api_scan(request: Request):
    LOGGER.info(
        "Manual scan requested",
        extra={"operation": "api.scan.request"},
    )
    try:
        _scan_index()
    except ScanAlreadyRunning as exc:
        LOGGER.warning(
            "Manual scan conflict",
            extra={"operation": "api.scan.conflict"},
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        LOGGER.error(
            "Manual scan failed",
            extra={"operation": "api.scan.failed"},
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    stop_event = getattr(
        request.app.state,
        "metadata_stop_event",
        threading.Event(),
    )
    if config.scan_wait_for_resources():
        process_pending_metadata(logger=LOGGER, max_workers=2)
        repair_missing_thumbnails(logger=LOGGER)
        SCAN_RUNTIME.resources_complete()
    else:
        _start_background_indexing(stop_event)
    result = SCAN_RUNTIME.snapshot()
    LOGGER.info(
        "Manual scan accepted state=%s",
        result.get("state"),
        extra={"operation": "api.scan.complete"},
    )
    return result
