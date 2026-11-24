from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from local_video_site import database
from local_video_site.core import config
from local_video_site.storage.deletion import delete_password_configured, verify_delete_password
from local_video_site.storage.deletion_tasks import DeleteTaskError, create_delete_batch, delete_job_counts, get_delete_jobs_by_ids, list_delete_jobs, retry_failed_batch, retry_delete_job
from ..audit import audit_account_action
from ..delete_rate import (
    clear_delete_failures,
    delete_rate_allowed,
    delete_rate_key,
    record_delete_failure,
)
from ..templates import TEMPLATES


router = APIRouter()


class DeleteBatchCreate(BaseModel):
    scope_type: str
    target_id: int
    verification_code: str
    expected_updated_at: str = ""


@router.get("/manage/delete-tasks", response_class=HTMLResponse)
def manage_delete_tasks(
    request: Request,
    q: str = "",
    scope_type: str = "",
    status: str = "",
    source_action: str = "",
    page: int = Query(1, ge=1),
):
    identity = request.state.identity
    data = list_delete_jobs(
        query=q.strip(),
        scope_type=scope_type.strip(),
        status=status.strip(),
        source_action=source_action.strip(),
        page=page,
        page_size=config.delete_tasks_page_size(),
    )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="video_delete_tasks.html",
        context={
            "identity": identity,
            "download_counts": database.download_task_counts(
                owner_user_id=identity.user_id,
                owned_only=False,
            ),
            "active_page": "deletes",
            "data": data,
            "counts": delete_job_counts(),
            "query": q.strip(),
            "scope_type": scope_type.strip(),
            "status": status.strip(),
            "source_action": source_action.strip(),
            "scope_labels": {
                "video": "删除视频",
                "season": "删除视频季",
                "series": "删除视频集",
            },
            "status_labels": {
                "pending": "等待执行",
                "blocked": "等待下级任务",
                "running": "执行中",
                "success": "成功",
                "failed": "失败",
                "cancelled": "已取消",
            },
            "source_labels": {
                "video_management": "视频管理",
                "season_management": "视频季管理",
                "series_management": "视频集管理",
            },
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/manage/delete-jobs")
def api_delete_jobs(
    q: str = "",
    scope_type: str = "",
    status: str = "",
    source_action: str = "",
    page: int = Query(1, ge=1),
):
    return list_delete_jobs(
        query=q.strip(),
        scope_type=scope_type.strip(),
        status=status.strip(),
        source_action=source_action.strip(),
        page=page,
        page_size=config.delete_tasks_page_size(),
    )


@router.get("/api/manage/delete-jobs/live")
def api_delete_jobs_live(ids: str = Query(..., min_length=1)):
    try:
        job_ids = sorted({
            int(value) for value in ids.split(",") if value.strip()
        })
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="任务 ID 无效") from exc
    if not job_ids or len(job_ids) > 100:
        raise HTTPException(status_code=422, detail="任务 ID 数量无效")
    return {"items": get_delete_jobs_by_ids(job_ids)}


@router.post("/api/manage/delete-jobs", status_code=202)
def api_create_delete_jobs(
    request: Request,
    selection: DeleteBatchCreate,
):
    identity = request.state.identity
    key = delete_rate_key(request, selection.target_id)
    if not delete_rate_allowed(key):
        raise HTTPException(status_code=429, detail="请稍后再试")
    if not delete_password_configured():
        raise HTTPException(
            status_code=409,
            detail="尚未配置删除校验码，请由管理员在参数配置中设置",
        )
    if not verify_delete_password(selection.verification_code):
        record_delete_failure(key)
        raise HTTPException(status_code=401, detail="删除校验码错误")
    try:
        result = create_delete_batch(
            scope_type=selection.scope_type,
            target_id=selection.target_id,
            source_action=(
                "series_management"
                if selection.scope_type == "series"
                else "season_management"
                if selection.scope_type == "season"
                else "video_management"
            ),
            requested_by_user_id=identity.user_id,
            requested_by_username=identity.username,
            expected_updated_at=selection.expected_updated_at,
        )
    except DeleteTaskError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    clear_delete_failures(key)
    audit_account_action(
        request,
        "video.delete.batch_created",
        "success",
        "video_delete_batch",
        result["batch_id"],
        details=result,
    )
    return result


@router.post("/api/manage/delete-jobs/{job_id}/retry")
def api_retry_delete_job(request: Request, job_id: int):
    if not retry_delete_job(job_id):
        raise HTTPException(status_code=409, detail="当前删除任务不能重试")
    audit_account_action(
        request,
        "video.delete.retried",
        "success",
        "video_delete_job",
        str(job_id),
    )
    return {"status": "pending"}


@router.post("/api/manage/delete-batches/{batch_id}/retry-failed")
def api_retry_delete_batch(request: Request, batch_id: str):
    retried = retry_failed_batch(batch_id)
    if retried == 0:
        raise HTTPException(
            status_code=409,
            detail="该批次没有可重试的失败任务",
        )
    audit_account_action(
        request,
        "video.delete.batch_retried",
        "success",
        "video_delete_batch",
        batch_id,
        details={"retried": retried},
    )
    return {"status": "pending", "retried": retried}
