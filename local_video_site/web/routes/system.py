from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from local_video_site.core import config
from local_video_site.jobs.integrity import latest_integrity_report
from local_video_site.jobs.integrity_tasks import create_integrity_job, get_integrity_job, list_integrity_jobs
from local_video_site.jobs.readiness import readiness_snapshot
from ..audit import audit_account_action


router = APIRouter()


@router.get("/api/health")
def health():
    return {
        "status": "ok",
        "service": "local-video-site",
        "mode": "lan" if config.lan_mode() else "local",
    }


@router.get("/api/ready")
def readiness(request: Request):
    payload = readiness_snapshot(request.app.state)
    return JSONResponse(
        payload,
        status_code=200 if payload["ready"] else 503,
        headers={
            "Cache-Control": "no-store",
            **({} if payload["ready"] else {"Retry-After": "5"}),
        },
    )


@router.get("/api/manage/integrity")
def integrity_status():
    return {
        "latest": latest_integrity_report(),
        "jobs": list_integrity_jobs(),
    }


@router.post("/api/manage/integrity/run", status_code=202)
def integrity_run(request: Request):
    job = create_integrity_job(request.state.identity.username)
    audit_account_action(
        request,
        "runtime.integrity.queued",
        "success",
        "runtime_integrity_job",
        job["job_id"],
    )
    return job


@router.get("/api/manage/integrity/jobs/{job_id}")
def integrity_job(job_id: str):
    job = get_integrity_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="完整性检查任务不存在")
    return job
