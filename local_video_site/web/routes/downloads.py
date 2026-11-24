from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)

from local_video_site.core import config
from local_video_site import database
from local_video_site.identity.permissions.content import ACCESS_LEVELS, ACCESS_LEVEL_LABELS
from local_video_site.identity.permissions.download import allowed_download_access_levels, can_delete_task_record, can_view_batch, can_view_task
from local_video_site.storage.deletion import delete_password_configured, verify_delete_password
from local_video_site.downloads.tasks import create_tasks as create_download_tasks, decorate_download_task_metrics, request_task_deletion
from local_video_site.episodes.batches import EpisodeBatchError, confirm_batch as confirm_episode_batch, confirm_episode_discovery, discover_batch as discover_episode_batch
from local_video_site.core.search import parse_search_query
from ..delete_rate import (
    clear_delete_failures as _clear_delete_failures,
    delete_rate_allowed as _delete_rate_allowed,
    record_delete_failure as _record_delete_failure,
)
from ..models.downloads import (
    DownloadTaskCreate,
    DownloadTaskDeleteSelection,
    EpisodeBatchConfirm,
    EpisodeBatchDiscover,
)
from ..templates import TEMPLATES
from ..urls import path_without_query_params


router = APIRouter()
LOGGER = logging.getLogger("local_video_site")


DOWNLOAD_STATUS_LABELS = {
    "pending": "等待下载",
    "running": "下载中",
    "pausing": "正在暂停",
    "paused": "已暂停",
    "success": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
    "deleting": "正在删除",
    "deleted": "已删除",
}
DOWNLOAD_STAGE_LABELS = {
    "waiting": "排队",
    "opening_page": "打开页面",
    "metadata_source": "获取来源信息",
    "discovering_media": "查找媒体",
    "downloading": "下载媒体",
    "finalizing": "整理文件",
    "finished": "完成",
    "paused": "暂停",
}
DOWNLOAD_LIVE_STATUSES = {
    "pending",
    "running",
    "pausing",
    "paused",
    "deleting",
}


def _default_download_dir() -> Path:
    roots = config.configured_roots()
    if roots:
        return Path(roots[0]).resolve()
    return (config.PROJECT_ROOT / "download-hub").resolve()


def _owned_output(identity, raw: str) -> str:
    path = Path(str(raw or "")).expanduser()
    if not path.is_absolute():
        raise HTTPException(status_code=422, detail="下载路径必须是绝对路径")
    path = path.resolve()
    if not identity.is_admin and path != _default_download_dir():
        raise HTTPException(
            status_code=403,
            detail="普通用户只能使用默认下载目录",
        )
    return str(path)


def _owned_access_level(identity, value) -> int:
    try:
        level = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="访问级别必须是 0、1 或 2",
        ) from exc
    if level not in ACCESS_LEVELS:
        raise HTTPException(status_code=422, detail="访问级别无效")
    if level not in allowed_download_access_levels(identity):
        raise HTTPException(
            status_code=403,
            detail="当前账号不能设置该视频访问级别",
        )
    return level


@router.get("/downloads", response_class=HTMLResponse)
def downloads(
    request: Request,
    q: str = "",
    status: str = "",
    owner: str = "",
    batch_notice: str = "",
    page: int = Query(1, ge=1),
):
    if "output_dir" in request.query_params:
        return RedirectResponse(
            path_without_query_params(request, "output_dir"),
            status_code=302,
        )
    identity = request.state.identity
    owned_only = not identity.is_admin
    owner_filter = owner.strip() if identity.is_admin else ""
    data = database.list_download_tasks(
        query=q.strip(),
        status=status.strip(),
        owner_user_id=identity.user_id,
        owned_only=owned_only,
        owner_username=owner_filter,
        page=page,
        page_size=config.downloads_page_size(),
    )
    data = {
        **data,
        "items": [
            decorate_download_task_metrics(item)
            for item in data["items"]
        ],
    }
    counts = database.download_task_counts(
        owner_user_id=identity.user_id,
        owned_only=owned_only,
    )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="downloads.html",
        context={
            "data": data,
            "counts": counts,
            "download_counts": counts,
            "active_page": "downloads",
            "query": q,
            "status": status,
            "owner": owner_filter,
            "owners": (
                database.download_task_owners()
                if identity.is_admin
                else []
            ),
            "status_labels": DOWNLOAD_STATUS_LABELS,
            "stage_labels": DOWNLOAD_STAGE_LABELS,
            "default_download_dir": str(_default_download_dir()),
            "max_attempts": config.download_task_attempts(),
            "batch_notice": batch_notice.strip(),
            "access_levels": ACCESS_LEVELS,
            "access_level_labels": ACCESS_LEVEL_LABELS,
            "allowed_access_levels": allowed_download_access_levels(
                identity
            ),
            "identity": identity,
            "is_admin": identity.is_admin,
        },
    )


@router.get("/downloads/batch", response_class=HTMLResponse)
def batch_downloads(request: Request):
    identity = request.state.identity
    return TEMPLATES.TemplateResponse(
        request=request,
        name="batch_download.html",
        context={
            "default_download_dir": str(_default_download_dir()),
            "access_levels": ACCESS_LEVELS,
            "access_level_labels": ACCESS_LEVEL_LABELS,
            "allowed_access_levels": allowed_download_access_levels(
                identity
            ),
            "identity": identity,
            "is_admin": identity.is_admin,
        },
    )


@router.post("/api/download-tasks")
def api_create_download_tasks(
    request: Request,
    selection: DownloadTaskCreate,
):
    identity = request.state.identity
    if selection.mode not in (None, "normal", "special"):
        raise HTTPException(status_code=422, detail="不支持的下载模式")
    if selection.mode == "normal" and not selection.urls:
        raise HTTPException(status_code=422, detail="普通下载缺少网页地址")
    if selection.mode == "special" and not selection.items:
        raise HTTPException(status_code=422, detail="特殊下载缺少任务行")
    output_dir = _owned_output(identity, selection.output_dir)
    access_level = _owned_access_level(
        identity,
        selection.access_level if selection.index_into_library else 0,
    )
    try:
        return create_download_tasks(
            output_dir=output_dir,
            preferred_quality=selection.quality,
            access_level=access_level,
            index_into_library=selection.index_into_library,
            created_by_user_id=identity.user_id,
            created_by_username=identity.username,
            urls=selection.urls or [],
            items=(
                [item.model_dump() for item in selection.items]
                if selection.items is not None
                else None
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/download-tasks")
def api_download_tasks(
    request: Request,
    q: str = "",
    status: str = "",
    owner: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(
        config.downloads_page_size(),
        ge=1,
        le=100,
    ),
):
    identity = request.state.identity
    owned_only = not identity.is_admin
    data = database.list_download_tasks(
        query=q,
        status=status,
        owner_user_id=identity.user_id,
        owned_only=owned_only,
        owner_username=owner if identity.is_admin else "",
        page=page,
        page_size=page_size,
    )
    return {
        **data,
        "items": [
            decorate_download_task_metrics(item)
            for item in data["items"]
        ],
    }


def _task_or_404(request: Request, task_id: int) -> dict:
    task = database.get_download_task(task_id)
    if task is None or not can_view_task(request.state.identity, task):
        raise HTTPException(status_code=404, detail="下载任务不存在")
    return task


def _episode_batch_or_404(request: Request, batch_id: str) -> dict:
    batch = database.get_episode_download_batch(batch_id)
    if batch is None or not can_view_batch(request.state.identity, batch):
        raise HTTPException(status_code=404, detail="批量任务不存在")
    return batch


def _episode_discovery_or_404(
    request: Request,
    discovery_id: str,
) -> dict:
    discovery = database.get_episode_download_discovery(discovery_id)
    if (
        discovery is None
        or not can_view_batch(request.state.identity, discovery)
    ):
        raise HTTPException(status_code=404, detail="连续批量任务不存在")
    return discovery


def _task_matches_filters(
    task: dict,
    query: str,
    status: str,
) -> bool:
    if status and task.get("status") != status:
        return False
    parsed = parse_search_query(query)
    if not parsed.raw:
        return True
    if parsed.is_id:
        return (
            parsed.id_value is not None
            and int(task.get("id") or 0) == parsed.id_value
        )
    needle = parsed.raw.casefold()
    return any(
        needle in str(value or "").casefold()
        for value in (
            task.get("url"),
            task.get("metadata_url"),
            task.get("title"),
            task.get("file_name"),
        )
    )


def _render_task_row(task: dict, is_admin: bool) -> str:
    template = TEMPLATES.env.get_template(
        "partials/_download_task_row.html"
    )
    return template.render(
        task=task,
        is_admin=is_admin,
        status_labels=DOWNLOAD_STATUS_LABELS,
        stage_labels=DOWNLOAD_STAGE_LABELS,
    )


@router.get("/api/download-tasks/live")
def api_download_tasks_live(
    request: Request,
    ids: str = Query(..., min_length=1),
    q: str = "",
    status: str = "",
):
    try:
        task_ids = sorted({
            int(value) for value in ids.split(",") if value.strip()
        })
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="任务 ID 格式无效") from exc
    if not task_ids or len(task_ids) > 50:
        raise HTTPException(
            status_code=422,
            detail="实时刷新任务数量必须为 1-50 个",
        )
    identity = request.state.identity
    counts = database.download_task_counts(
        owner_user_id=identity.user_id,
        owned_only=not identity.is_admin,
    )
    tasks = [
        decorate_download_task_metrics(task)
        for task in database.get_download_tasks_by_ids(task_ids)
        if can_view_task(identity, task)
    ]
    tasks.sort(key=lambda task: task_ids.index(int(task["id"])))
    revision_payload = {
        "ids": task_ids,
        "tasks": [
            {
                "id": task["id"],
                "updated_at": task.get("updated_at"),
                "status": task.get("status"),
                "stage": task.get("stage"),
                "message": task.get("message"),
            }
            for task in tasks
        ],
    }
    etag = '"{}"'.format(
        hashlib.sha1(
            json.dumps(
                revision_payload,
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
    )
    headers = {"ETag": etag, "Cache-Control": "no-store"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    found_ids = {int(task["id"]) for task in tasks}
    rows = []
    full_refresh_required = False
    for task in tasks:
        matches = _task_matches_filters(
            task,
            q.strip(),
            status.strip(),
        )
        full_refresh_required = full_refresh_required or not matches
        rows.append({
            "id": int(task["id"]),
            "status": task.get("status"),
            "stage": task.get("stage"),
            "updated_at": task.get("updated_at"),
            "active": task.get("status") in DOWNLOAD_LIVE_STATUSES,
            "matches_filter": matches,
            "html": _render_task_row(task, identity.is_admin),
        })
    missing_ids = [
        task_id for task_id in task_ids if task_id not in found_ids
    ]
    return JSONResponse(
        {
            "revision": etag.strip('"'),
            "summary": counts,
            "rows": rows,
            "missing_ids": missing_ids,
            "full_refresh_required": (
                full_refresh_required or bool(missing_ids)
            ),
        },
        headers=headers,
    )


@router.get("/api/download-tasks/{task_id}")
def api_download_task(request: Request, task_id: int):
    return decorate_download_task_metrics(_task_or_404(request, task_id))


@router.post("/api/download-tasks/{task_id}/retry")
def api_retry_download_task(request: Request, task_id: int):
    _task_or_404(request, task_id)
    if not database.retry_download_task(task_id):
        raise HTTPException(
            status_code=409,
            detail="只有失败或已取消的任务可以重试",
        )
    return {"status": "pending"}


@router.post("/api/download-tasks/{task_id}/pause")
def api_pause_download_task(request: Request, task_id: int):
    _task_or_404(request, task_id)
    status = database.request_pause_download_task(task_id)
    if status is None:
        raise HTTPException(status_code=409, detail="当前任务状态不能暂停")
    return {"status": status}


@router.post("/api/download-tasks/{task_id}/resume")
def api_resume_download_task(request: Request, task_id: int):
    _task_or_404(request, task_id)
    if not database.resume_download_task(task_id):
        raise HTTPException(
            status_code=409,
            detail="只有已暂停或存在断点的失败任务可以继续下载",
        )
    return {"status": "pending"}


def _episode_batch_http_error(exc: EpisodeBatchError) -> HTTPException:
    status_code = 422
    if exc.code == "batch_not_found":
        status_code = 404
    elif exc.code in {
        "invalid_batch_status",
        "revision_conflict",
        "invalid_selection",
    }:
        status_code = 409
    return HTTPException(status_code=status_code, detail=str(exc))


@router.post("/api/episode-batches/discover")
def api_discover_episode_batch(
    request: Request,
    selection: EpisodeBatchDiscover,
):
    identity = request.state.identity
    if (
        selection.allow_source_series_mismatch
        and not identity.is_admin
    ):
        raise HTTPException(
            status_code=403,
            detail="只有管理员可以允许来源与目标视频集不一致",
        )
    output_root = _owned_output(
        identity,
        selection.output_root,
    )
    access_level = _owned_access_level(
        identity,
        selection.access_level,
    )
    try:
        batch = discover_episode_batch(
            series_name=selection.series_name,
            season_name=selection.season_name,
            season_number=selection.season_number,
            first_episode_url=selection.first_episode_url,
            output_root=output_root,
            quality=selection.quality,
            access_level=access_level,
            created_by_user_id=identity.user_id,
            created_by_username=identity.username,
            continuous_parse=selection.continuous_parse,
            aggregate_single_season=selection.aggregate_single_season,
            preserve_original_name=selection.preserve_original_name,
            allow_source_series_mismatch=(
                selection.allow_source_series_mismatch
            ),
        )
    except EpisodeBatchError as exc:
        raise _episode_batch_http_error(exc) from exc
    LOGGER.info(
        "Episode batch preview created id=%s items=%s mode=%s",
        batch.get("discovery_id") or batch["batch_id"],
        batch["item_count"],
        batch.get("mode") or "single_tab",
        extra={"operation": "episode.batch.preview"},
    )
    return batch


@router.get("/api/episode-discoveries/{discovery_id}")
def api_episode_discovery(request: Request, discovery_id: str):
    return _episode_discovery_or_404(request, discovery_id)


@router.post("/api/episode-discoveries/{discovery_id}/confirm")
def api_confirm_episode_discovery(
    request: Request,
    discovery_id: str,
    selection: EpisodeBatchConfirm,
):
    _episode_discovery_or_404(request, discovery_id)
    try:
        report = confirm_episode_discovery(
            discovery_id=discovery_id,
            selected_item_ids=selection.selected_item_ids,
            revision=selection.revision,
        )
    except EpisodeBatchError as exc:
        raise _episode_batch_http_error(exc) from exc
    LOGGER.info(
        "Episode discovery confirmed discovery=%s selected=%s created=%s",
        discovery_id,
        report["selected"],
        report["created"],
        extra={"operation": "episode.discovery.confirm"},
    )
    return report


@router.get("/api/episode-batches/{batch_id}")
def api_episode_batch(request: Request, batch_id: str):
    return _episode_batch_or_404(request, batch_id)


@router.post("/api/episode-batches/{batch_id}/confirm")
def api_confirm_episode_batch(
    request: Request,
    batch_id: str,
    selection: EpisodeBatchConfirm,
):
    _episode_batch_or_404(request, batch_id)
    try:
        report = confirm_episode_batch(
            batch_id=batch_id,
            selected_item_ids=selection.selected_item_ids,
            revision=selection.revision,
        )
    except EpisodeBatchError as exc:
        raise _episode_batch_http_error(exc) from exc
    LOGGER.info(
        "Episode batch confirmed batch=%s selected=%s created=%s",
        batch_id,
        report["selected"],
        report["created"],
        extra={"operation": "episode.batch.confirm"},
    )
    return report


@router.delete("/api/download-tasks/{task_id}", status_code=202)
def api_delete_download_task(
    request: Request,
    task_id: int,
    selection: DownloadTaskDeleteSelection,
):
    if not request.state.identity.is_admin:
        raise HTTPException(
            status_code=403,
            detail="普通用户不能删除已完成视频文件",
        )
    task = _task_or_404(request, task_id)
    if task["status"] == "success" and task.get("video_available"):
        raise HTTPException(
            status_code=409,
            detail="已成功完成的任务不能在下载列表中删除",
        )
    if task.get("video_id") or task.get("file_path"):
        client = request.client.host if request.client else ""
        key = ("download-task", client, int(task_id))
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
            LOGGER.warning(
                "Download task delete password rejected task_id=%s client=%s",
                task_id,
                client,
                extra={"operation": "download.task.delete.denied"},
            )
            raise HTTPException(status_code=401, detail="删除校验码错误")
        _clear_delete_failures(key)
    status = request_task_deletion(task_id)
    if status != "deleting":
        raise HTTPException(
            status_code=409,
            detail="当前任务不能删除",
        )
    LOGGER.info(
        "Download task deletion requested task_id=%s",
        task_id,
        extra={"operation": "download.task.delete.requested"},
    )
    return {"status": "deleting"}


@router.delete("/api/download-tasks/{task_id}/record")
def api_delete_download_task_record(request: Request, task_id: int):
    task = _task_or_404(request, task_id)
    if not can_delete_task_record(request.state.identity, task):
        raise HTTPException(
            status_code=409,
            detail="只有下载成功的任务可以仅删除记录",
        )
    result = database.delete_success_download_task_record(task_id)
    if result == "not_found":
        raise HTTPException(status_code=404, detail="下载任务不存在")
    if result == "not_success":
        raise HTTPException(
            status_code=409,
            detail="只有下载成功的任务可以仅删除记录",
        )
    LOGGER.info(
        "Download task record physically deleted task_id=%s",
        task_id,
        extra={"operation": "download.task.record.deleted"},
    )
    return {"status": "record_deleted", "task_id": int(task_id)}


@router.post("/api/download-tasks/{task_id}/cancel")
def api_cancel_download_task(
    request: Request,
    task_id: int,
    delete_partial: bool = True,
):
    _task_or_404(request, task_id)
    if not database.cancel_download_task(
        task_id,
        delete_partial=delete_partial,
    ):
        raise HTTPException(
            status_code=409,
            detail="只能取消尚未开始的任务",
        )
    return {"status": "cancelled"}
