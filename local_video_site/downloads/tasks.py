from __future__ import annotations

import uuid
from datetime import datetime
from typing import Dict, List, Optional

from local_video_site import database
from local_video_site.core import config
from local_video_site.identity.permissions.content import validate_access_level
from .progress import (
    DOWNLOAD_QUALITY_OPTIONS,
    normalise_requested_quality,
    normalize_url,
    validate_output_dir,
)
from .worker import *
from .worker import (
    DownloadTaskWorker,
    _apply_task_access_level,
    _delete_control_marker,
    _hls_fields_from_line,
    _is_cross_site_task,
    _keep_awake_command,
    _media_fields_from_line,
    _media_fields_from_summary,
    _metadata_completion_result,
    _parse_progress_bytes,
    _parse_progress_metrics,
    _prepare_cross_site_metadata,
    _refresh_video_metadata_in_subprocess,
    _resume_media_url,
    _run_download_process,
    _sync_video_metadata,
    _task_files_for_deletion,
)
from .progress import (
    backfill_download_task_media_info,
    backfill_download_task_transport_info,
    decorate_download_task_metrics,
)


def create_tasks(
    output_dir: str,
    urls: Optional[List[str]] = None,
    items: Optional[List[Dict]] = None,
    max_attempts: Optional[int] = None,
    preferred_quality: str = "site",
    batch_id: Optional[str] = None,
    access_level: int = 0,
    index_into_library: bool = True,
    created_by_user_id: Optional[int] = None,
    created_by_username: Optional[str] = None,
) -> Dict:
    directory = validate_output_dir(output_dir)
    requested_quality = normalise_requested_quality(preferred_quality)
    access_level = validate_access_level(access_level)
    if items is not None:
        task_items = list(items)
    elif urls is not None:
        task_items = [
            {"metadata_url": value, "download_url": value}
            for value in urls
        ]
    else:
        task_items = []
    if not task_items:
        raise ValueError("请至少输入一个数据来源地址")
    maximum_urls = config.download_max_urls_per_batch()
    if len(task_items) > maximum_urls:
        raise ValueError(
            "单次最多提交 {} 个 URL".format(maximum_urls)
        )
    batch_id = (
        str(batch_id)
        if batch_id
        else datetime.now().strftime("%Y%m%d%H%M%S")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    created: List[int] = []
    duplicates: List[str] = []
    duplicate_items: List[Dict] = []
    invalid: List[Dict] = []
    seen = set()
    attempts = max_attempts or config.download_task_attempts()
    for item in task_items:
        metadata_raw = str(item.get("metadata_url") or "").strip()
        download_raw = str(item.get("download_url") or "").strip()
        if not download_raw:
            download_raw = metadata_raw
        try:
            metadata_url = normalize_url(metadata_raw)
            download_url = normalize_url(download_raw)
        except ValueError as exc:
            invalid.append(
                {
                    "metadata_url": metadata_raw,
                    "download_url": download_raw,
                    "error": str(exc),
                }
            )
            continue
        if metadata_url in seen:
            duplicates.append(metadata_url)
            duplicate_items.append(
                {
                    "metadata_url": metadata_url,
                    "existing_task_id": None,
                    "reason": "duplicate in request",
                }
            )
            continue
        seen.add(metadata_url)
        transport_policy = ensure_site_for_url(download_url)
        media_policy = policy_for_task(
            transport_policy["site_key"],
            requested_quality,
        )
        task_id, existing = database.create_download_task_if_metadata_available(
            batch_id=batch_id,
            created_by_user_id=created_by_user_id,
            created_by_username=created_by_username,
            url=download_raw,
            normalized_url=download_url,
            metadata_url=metadata_raw,
            normalized_metadata_url=metadata_url,
            output_dir=str(directory),
            max_attempts=attempts,
            priority=int(item.get("priority") or 0),
            access_level=access_level,
            index_into_library=index_into_library,
            requested_quality=requested_quality,
            transport_site_key=transport_policy["site_key"],
            media_strategy_key=media_policy["preset_key"],
            media_strategy_version=media_policy["version"],
            media_policy_snapshot_json=json.dumps(
                media_policy,
                ensure_ascii=False,
                sort_keys=True,
            ),
            episode_id=item.get("episode_id"),
            episode_batch_id=item.get("episode_batch_id"),
            output_stem=str(item.get("output_stem") or "").strip() or None,
            display_title=(
                str(item.get("display_title") or "").strip() or None
            ),
            title=(
                str(item.get("title") or item.get("display_title") or "").strip()
                or None
            ),
        )
        if existing:
            if (
                bool(int(existing.get("index_into_library", 1)))
                != bool(index_into_library)
            ):
                invalid.append({
                    "metadata_url": metadata_url,
                    "download_url": download_url,
                    "error": (
                        "相同地址已有任务 #{}，但其入库模式为“{}”。"
                        "请先处理原任务，或不要混用下载模式。"
                    ).format(
                        existing["id"],
                        (
                            "仅下载"
                            if not int(existing.get("index_into_library", 1))
                            else "下载并入库"
                        ),
                    ),
                })
                continue
            duplicates.append(metadata_url)
            duplicate_items.append(
                {
                    "metadata_url": metadata_url,
                    "existing_task_id": int(existing["id"]),
                    "existing_status": existing["status"],
                    "existing_download_url": existing["url"],
                    "existing_output_dir": existing["output_dir"],
                }
            )
            continue
        created.append(task_id)
        record_task_created(transport_policy["site_key"], task_id)
    return {
        "batch_id": batch_id,
        "created": len(created),
        "task_ids": created,
        "duplicates": len(duplicates),
        "duplicate_urls": duplicates,
        "duplicate_items": duplicate_items,
        "invalid": invalid,
    }

def request_task_deletion(task_id: int) -> Optional[str]:
    status = database.request_delete_download_task(task_id)
    if status != "deleting":
        return status
    config.ensure_data_directories()
    _delete_control_marker(task_id).write_text("delete", encoding="utf-8")
    return status
