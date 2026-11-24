from __future__ import annotations

import json
import sqlite3
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from local_video_site.core import config
from ..db.connection import connect
from ..db.transaction import now_iso as _now
from local_video_site.episodes.naming import is_generic_episode_title, meaningful_episode_title, season_number_from_label
from local_video_site.core.search import parse_search_query
from .videos import video_repository, video_with_preview_state


def create_download_task(
    batch_id: str,
    url: str,
    normalized_url: str,
    metadata_url: str,
    normalized_metadata_url: str,
    output_dir: str,
    max_attempts: int,
    priority: int = 0,
    access_level: int = 0,
    requested_quality: str = "auto",
    index_into_library: bool = True,
    transport_site_key: Optional[str] = None,
    media_strategy_key: Optional[str] = None,
    media_strategy_version: Optional[int] = None,
    media_policy_snapshot_json: Optional[str] = None,
    episode_id: Optional[int] = None,
    episode_batch_id: Optional[str] = None,
    output_stem: Optional[str] = None,
    display_title: Optional[str] = None,
    title: Optional[str] = None,
    created_by_user_id: Optional[int] = None,
    created_by_username: Optional[str] = None,
) -> int:
    now = _now()
    with connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO download_tasks (
                batch_id, created_by_user_id, created_by_username,
                url, normalized_url,
                metadata_url, normalized_metadata_url, output_dir, title,
                status, stage, max_attempts, priority, access_level,
                requested_quality, index_into_library,
                transport_site_key, media_strategy_key,
                media_strategy_version, media_policy_snapshot_json,
                episode_id, episode_batch_id, output_stem, display_title,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'waiting',
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                batch_id,
                created_by_user_id,
                created_by_username,
                url,
                normalized_url,
                metadata_url,
                normalized_metadata_url,
                output_dir,
                title,
                max(1, int(max_attempts)),
                int(priority),
                int(access_level),
                str(requested_quality or "auto"),
                1 if index_into_library else 0,
                transport_site_key,
                media_strategy_key,
                media_strategy_version,
                media_policy_snapshot_json,
                episode_id,
                episode_batch_id,
                output_stem,
                display_title,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid or 0)
def create_download_task_if_metadata_available(
    batch_id: str,
    url: str,
    normalized_url: str,
    metadata_url: str,
    normalized_metadata_url: str,
    output_dir: str,
    max_attempts: int,
    priority: int = 0,
    access_level: int = 0,
    requested_quality: str = "auto",
    index_into_library: bool = True,
    transport_site_key: Optional[str] = None,
    media_strategy_key: Optional[str] = None,
    media_strategy_version: Optional[int] = None,
    media_policy_snapshot_json: Optional[str] = None,
    episode_id: Optional[int] = None,
    episode_batch_id: Optional[str] = None,
    output_stem: Optional[str] = None,
    display_title: Optional[str] = None,
    title: Optional[str] = None,
    created_by_user_id: Optional[int] = None,
    created_by_username: Optional[str] = None,
) -> tuple:
    now = _now()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            """
            SELECT * FROM download_tasks
            WHERE normalized_metadata_url = ?
              AND deleted_at IS NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            (normalized_metadata_url,),
        ).fetchone()
        if existing:
            connection.commit()
            return int(existing["id"]), dict(existing)
        cursor = connection.execute(
            """
            INSERT INTO download_tasks (
                batch_id, created_by_user_id, created_by_username,
                url, normalized_url,
                metadata_url, normalized_metadata_url, output_dir, title,
                status, stage, max_attempts, priority, access_level,
                requested_quality, index_into_library,
                transport_site_key, media_strategy_key,
                media_strategy_version, media_policy_snapshot_json,
                episode_id, episode_batch_id, output_stem, display_title,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'waiting',
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                batch_id,
                created_by_user_id,
                created_by_username,
                url,
                normalized_url,
                metadata_url,
                normalized_metadata_url,
                output_dir,
                title,
                max(1, int(max_attempts)),
                int(priority),
                int(access_level),
                str(requested_quality or "auto"),
                1 if index_into_library else 0,
                transport_site_key,
                media_strategy_key,
                media_strategy_version,
                media_policy_snapshot_json,
                episode_id,
                episode_batch_id,
                output_stem,
                display_title,
                now,
                now,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid or 0), None
def find_recent_download_task(
    normalized_url: str,
    output_dir: str,
) -> Optional[Dict]:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM download_tasks
            WHERE normalized_url = ?
              AND output_dir = ?
              AND deleted_at IS NULL
              AND status IN ('pending', 'running', 'success')
            ORDER BY id DESC
            LIMIT 1
            """,
            (normalized_url, output_dir),
        ).fetchone()
    return dict(row) if row else None
def find_download_task_by_metadata_url(
    normalized_metadata_url: str,
) -> Optional[Dict]:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM download_tasks
            WHERE normalized_metadata_url = ?
              AND deleted_at IS NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            (normalized_metadata_url,),
        ).fetchone()
    return dict(row) if row else None
def get_download_task(task_id: int) -> Optional[Dict]:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT dt.*,
                   v.width AS video_width,
                   v.height AS video_height,
                   v.title AS video_display_title,
                   EXISTS (
                       SELECT 1 FROM videos v
                       WHERE v.id = dt.video_id
                         AND v.missing = 0
                         AND v.is_deleted = 0
                   ) AS video_available
            FROM download_tasks dt
            LEFT JOIN video_catalog v ON v.id = dt.video_id
            WHERE dt.id = ?
            """,
            (int(task_id),),
        ).fetchone()
    return dict(row) if row else None
def get_download_tasks_by_ids(task_ids: List[int]) -> List[Dict]:
    ids = sorted({int(task_id) for task_id in task_ids})
    if not ids:
        return []
    placeholders = ", ".join("?" for _ in ids)
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT dt.*,
                   v.width AS video_width,
                   v.height AS video_height,
                   v.title AS video_display_title,
                   EXISTS (
                       SELECT 1 FROM videos v
                       WHERE v.id = dt.video_id
                         AND v.missing = 0
                         AND v.is_deleted = 0
                   ) AS video_available
            FROM download_tasks dt
            LEFT JOIN video_catalog v ON v.id = dt.video_id
            WHERE dt.id IN ({})
              AND dt.deleted_at IS NULL
            """.format(placeholders),
            ids,
        ).fetchall()
    return [dict(row) for row in rows]
def list_download_tasks(
    query: str = "",
    status: str = "",
    owner_user_id: Optional[int] = None,
    owned_only: bool = False,
    owner_username: str = "",
    page: int = 1,
    page_size: int = 15,
) -> Dict:
    clauses = ["dt.deleted_at IS NULL"]
    params: List = []
    parsed_query = parse_search_query(query)
    if parsed_query.is_id:
        if parsed_query.id_value is None:
            clauses.append("0 = 1")
        else:
            clauses.append("dt.id = ?")
            params.append(parsed_query.id_value)
    elif parsed_query.raw:
        query = parsed_query.raw
        escaped = (
            query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        clauses.append(
            """
            (
                url LIKE ? ESCAPE '\\'
                OR COALESCE(metadata_url, '') LIKE ? ESCAPE '\\'
                OR COALESCE(dt.title, '') LIKE ? ESCAPE '\\'
                OR COALESCE(dt.display_title, '') LIKE ? ESCAPE '\\'
                OR COALESCE(
                    (
                        SELECT linked.title
                        FROM video_catalog linked
                        WHERE linked.id = dt.video_id
                    ),
                    ''
                ) LIKE ? ESCAPE '\\'
                OR COALESCE(dt.episode_batch_id, '') LIKE ? ESCAPE '\\'
                OR COALESCE(file_name, '') LIKE ? ESCAPE '\\'
            )
            """
        )
        value = "%{}%".format(escaped)
        params.extend([value, value, value, value, value, value, value])
    if status:
        clauses.append("status = ?")
        params.append(status)
    if owned_only:
        clauses.append("dt.created_by_user_id = ?")
        params.append(int(owner_user_id or -1))
    elif owner_username:
        if owner_username == "历史任务":
            clauses.append("dt.created_by_user_id IS NULL")
        else:
            clauses.append(
                "COALESCE(dt.created_by_username, '') LIKE ? ESCAPE '\\'"
            )
            params.append(
                "%{}%".format(
                    owner_username.replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                )
            )
    where = " AND ".join(clauses)
    page = max(1, int(page))
    page_size = max(1, min(100, int(page_size)))
    with connect() as connection:
        total = connection.execute(
            "SELECT COUNT(*) FROM download_tasks dt WHERE {}".format(where),
            params,
        ).fetchone()[0]
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        rows = connection.execute(
            """
            SELECT dt.*,
                   v.width AS video_width,
                   v.height AS video_height,
                   EXISTS (
                       SELECT 1 FROM videos v
                       WHERE v.id = dt.video_id
                         AND v.missing = 0
                         AND v.is_deleted = 0
                   ) AS video_available
            FROM download_tasks dt
            LEFT JOIN video_catalog v ON v.id = dt.video_id
            WHERE {}
            ORDER BY
                CASE status
                    WHEN 'running' THEN 0
                    WHEN 'pending' THEN 1
                    WHEN 'failed' THEN 2
                    ELSE 3
                END,
                dt.created_at DESC,
                dt.id DESC
            LIMIT ? OFFSET ?
            """.format(where),
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    return {
        "items": [dict(row) for row in rows],
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }
def backfill_download_media_from_videos() -> int:
    """Copy resolution metadata from linked library videos into download tasks."""

    with connect() as connection:
        cursor = connection.execute(
            """
            UPDATE download_tasks AS dt
            SET media_width = COALESCE(
                    dt.media_width,
                    (SELECT v.width FROM video_catalog v WHERE v.id = dt.video_id)
                ),
                media_height = COALESCE(
                    dt.media_height,
                    (SELECT v.height FROM video_catalog v WHERE v.id = dt.video_id)
                ),
                updated_at = ?
            WHERE dt.video_id IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM video_catalog v
                  WHERE v.id = dt.video_id
                    AND (v.width IS NOT NULL OR v.height IS NOT NULL)
              )
              AND (
                  dt.media_width IS NULL
                  OR dt.media_height IS NULL
              )
            """,
            (_now(),),
        )
        connection.commit()
        return max(0, cursor.rowcount)
def download_tasks_missing_media_info() -> List[Dict]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT dt.*,
                   v.width AS video_width,
                   v.height AS video_height
            FROM download_tasks dt
            LEFT JOIN video_catalog v ON v.id = dt.video_id
            WHERE dt.deleted_at IS NULL
              AND dt.media_quality IS NULL
              AND (
                  dt.media_width IS NOT NULL
                  OR dt.media_height IS NOT NULL
                  OR dt.video_id IS NOT NULL
                  OR dt.log_path IS NOT NULL
                  OR dt.file_path IS NOT NULL
              )
            ORDER BY dt.id ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]
def download_tasks_missing_transport_info() -> List[Dict]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM download_tasks
            WHERE deleted_at IS NULL
              AND transport_kind IS NULL
              AND log_path IS NOT NULL
            ORDER BY id ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]
def download_tasks_missing_hls_progress_info() -> List[Dict]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM download_tasks
            WHERE deleted_at IS NULL
              AND transport_kind = 'hls'
              AND hls_total_segments = 0
              AND log_path IS NOT NULL
            ORDER BY id ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]
def download_task_counts(
    owner_user_id: Optional[int] = None,
    owned_only: bool = False,
) -> Dict:
    where = "deleted_at IS NULL"
    params = []
    if owned_only:
        where += " AND created_by_user_id = ?"
        params.append(int(owner_user_id or -1))
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM download_tasks
            WHERE {}
            GROUP BY status
            """.format(where),
            params,
        ).fetchall()
    result = {
        "pending": 0,
        "running": 0,
        "pausing": 0,
        "paused": 0,
        "success": 0,
        "failed": 0,
        "cancelled": 0,
        "deleting": 0,
    }
    for row in rows:
        result[row["status"]] = int(row["count"])
    result["active"] = (
        result["pending"]
        + result["running"]
        + result["pausing"]
        + result["deleting"]
    )
    return result
def download_task_owners() -> List[Dict]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT created_by_user_id, created_by_username,
                   COUNT(*) AS task_count
            FROM download_tasks
            WHERE deleted_at IS NULL
            GROUP BY created_by_user_id, created_by_username
            ORDER BY created_by_username COLLATE NOCASE
            """
        ).fetchall()
    return [dict(row) for row in rows]
def _settle_download_run(
    connection: sqlite3.Connection,
    task_id: int,
    now: str,
) -> Optional[int]:
    row = connection.execute(
        """
        SELECT run_started_at, elapsed_seconds
        FROM download_tasks
        WHERE id = ?
        """,
        (int(task_id),),
    ).fetchone()
    if not row:
        return None
    elapsed = int(row["elapsed_seconds"] or 0)
    started_at = row["run_started_at"]
    if started_at:
        try:
            started = datetime.fromisoformat(str(started_at))
            ended = datetime.fromisoformat(str(now))
            if started.tzinfo is None:
                started = started.astimezone()
            elapsed += max(0, int((ended - started).total_seconds()))
        except (TypeError, ValueError):
            pass
    connection.execute(
        """
        UPDATE download_tasks
        SET elapsed_seconds = ?,
            run_started_at = NULL,
            speed_bytes_per_sec = 0,
            eta_seconds = NULL
        WHERE id = ?
        """,
        (elapsed, int(task_id)),
    )
    return elapsed
def claim_download_task() -> Optional[Dict]:
    now = _now()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT * FROM download_tasks
            WHERE status = 'pending'
              AND deleted_at IS NULL
              AND delete_requested = 0
              AND (
                  next_retry_at IS NULL
                  OR next_retry_at = ''
                  OR next_retry_at <= ?
              )
            ORDER BY priority DESC, id ASC
            LIMIT 1
            """,
            (now,),
        ).fetchone()
        if row is None:
            connection.commit()
            return None
        connection.execute(
            """
            UPDATE download_tasks
            SET status = 'running',
                stage = 'waiting',
                pause_requested = 0,
                message = CASE
                    WHEN resume_count > 0 OR progress_percent > 0
                    THEN '继续下载'
                    ELSE '任务已开始'
                END,
                error_detail = NULL,
                run_count = run_count + 1,
                started_at = COALESCE(started_at, ?),
                run_started_at = ?,
                speed_bytes_per_sec = 0,
                eta_seconds = NULL,
                heartbeat_at = ?,
                next_retry_at = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (now, now, now, now, row["id"]),
        )
        connection.commit()
    task = dict(row)
    task["status"] = "running"
    task["stage"] = "waiting"
    task["run_count"] = int(task.get("run_count") or 0) + 1
    task["started_at"] = task.get("started_at") or now
    task["run_started_at"] = now
    task["speed_bytes_per_sec"] = 0
    task["eta_seconds"] = None
    return task


_DOWNLOAD_TASK_UPDATES = {
    "title",
    "file_path",
    "file_name",
    "status",
    "stage",
    "progress_percent",
    "downloaded_bytes",
    "total_bytes",
    "media_width",
    "media_height",
    "media_quality",
    "requested_quality",
    "index_into_library",
    "media_url_expires_at",
    "media_refresh_count",
    "last_media_refresh_at",
    "media_identity",
    "resume_validation",
    "resume_total_bytes",
    "transport_kind",
    "transport_site_key",
    "media_strategy_key",
    "media_strategy_version",
    "media_policy_snapshot_json",
    "selection_reason",
    "selected_candidate_source",
    "selected_candidate_kind",
    "fallback_trace_json",
    "range_connection_count",
    "range_completed_chunks",
    "range_total_chunks",
    "hls_connection_count",
    "hls_completed_segments",
    "hls_total_segments",
    "speed_bytes_per_sec",
    "eta_seconds",
    "elapsed_seconds",
    "run_started_at",
    "pause_requested",
    "paused_at",
    "resume_count",
    "run_count",
    "filename_hint",
    "partial_path",
    "checkpoint_path",
    "delete_requested",
    "last_progress_at",
    "stall_detected_at",
    "stall_count",
    "attempts",
    "message",
    "error_detail",
    "video_id",
    "metadata_status",
    "category_count",
    "actor_count",
    "log_path",
    "next_retry_at",
    "finished_at",
    "deleted_at",
    "heartbeat_at",
    "episode_id",
    "episode_batch_id",
    "output_stem",
    "display_title",
    "access_level",
}
def update_download_task(task_id: int, **fields) -> None:
    values = {
        key: value
        for key, value in fields.items()
        if key in _DOWNLOAD_TASK_UPDATES
    }
    if not values:
        return
    values["updated_at"] = _now()
    assignments = ", ".join("{} = ?".format(key) for key in values)
    params = list(values.values())
    params.append(int(task_id))
    with connect() as connection:
        if values.get("status") not in (None, "running"):
            _settle_download_run(connection, int(task_id), _now())
        connection.execute(
            """
            UPDATE download_tasks
            SET {}
            WHERE id = ? AND deleted_at IS NULL
            """.format(assignments),
            params,
        )
def repair_inherited_metadata_task_messages() -> int:
    with connect() as connection:
        cursor = connection.execute(
            """
            UPDATE download_tasks
            SET message = 'success',
                error_detail = NULL
            WHERE status = 'success'
              AND metadata_status = 'inherited'
              AND message = 'success; metadata: failed'
              AND COALESCE(error_detail, '') = ''
            """
        )
    return max(0, cursor.rowcount)
def _remove_task_partial_files(task: Dict) -> int:
    removed = 0
    output_dir = Path(task["output_dir"]).resolve()
    for value in (task.get("partial_path"), task.get("checkpoint_path")):
        if not value:
            continue
        path = Path(value).resolve()
        if output_dir not in path.parents:
            continue
        try:
            path.unlink()
            removed += 1
        except FileNotFoundError:
            pass
        except OSError:
            pass
    return removed
def cancel_download_task(task_id: int, delete_partial: bool = True) -> bool:
    now = _now()
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM download_tasks WHERE id = ?",
            (int(task_id),),
        ).fetchone()
        if not row:
            return False
        task = dict(row)
        cursor = connection.execute(
            """
            UPDATE download_tasks
            SET status = 'cancelled',
                stage = 'finished',
                pause_requested = 0,
                partial_path = CASE WHEN ? THEN NULL ELSE partial_path END,
                checkpoint_path = CASE WHEN ? THEN NULL ELSE checkpoint_path END,
                message = '用户已取消',
                finished_at = ?,
                updated_at = ?
            WHERE id = ?
              AND deleted_at IS NULL
              AND delete_requested = 0
              AND status IN ('pending', 'paused')
            """,
            (
                1 if delete_partial else 0,
                1 if delete_partial else 0,
                now,
                now,
                int(task_id),
            ),
        )
        cancelled = cursor.rowcount > 0
    if cancelled and delete_partial:
        _remove_task_partial_files(task)
    return cancelled
def request_delete_download_task(task_id: int) -> Optional[str]:
    now = _now()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM download_tasks WHERE id = ?",
            (int(task_id),),
        ).fetchone()
        if row is None or row["deleted_at"]:
            connection.commit()
            return None
        status = str(row["status"])
        if status == "success":
            video_exists = bool(
                row["video_id"]
                and connection.execute(
                    """
                    SELECT 1 FROM videos
                    WHERE id = ?
                      AND missing = 0
                      AND is_deleted = 0
                    """,
                    (int(row["video_id"]),),
                ).fetchone()
            )
            if video_exists:
                connection.commit()
                return None
        if status == "deleting" or row["delete_requested"]:
            connection.commit()
            return "deleting"
        if status not in {
            "pending",
            "running",
            "pausing",
            "paused",
            "failed",
            "cancelled",
            "success",
        }:
            connection.commit()
            return None
        connection.execute(
            """
            UPDATE download_tasks
            SET status = 'deleting',
                stage = 'deleting',
                delete_requested = 1,
                pause_requested = 0,
                speed_bytes_per_sec = 0,
                eta_seconds = NULL,
                message = '正在停止下载并删除任务文件',
                heartbeat_at = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (now, int(task_id)),
        )
        connection.commit()
    return "deleting"
def next_pending_download_deletion() -> Optional[Dict]:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM download_tasks
            WHERE status = 'deleting'
              AND delete_requested = 1
              AND deleted_at IS NULL
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()
    return dict(row) if row else None
def complete_download_task_deletion(
    task_id: int,
    message: str = "任务和下载文件已删除",
) -> bool:
    now = _now()
    with connect() as connection:
        _settle_download_run(connection, int(task_id), now)
        cursor = connection.execute(
            """
            UPDATE download_tasks
            SET status = 'deleted',
                stage = 'deleted',
                delete_requested = 0,
                pause_requested = 0,
                speed_bytes_per_sec = 0,
                eta_seconds = NULL,
                partial_path = NULL,
                checkpoint_path = NULL,
                message = ?,
                finished_at = COALESCE(finished_at, ?),
                heartbeat_at = NULL,
                deleted_at = ?,
                updated_at = ?
            WHERE id = ?
              AND deleted_at IS NULL
            """,
            (message[:1000], now, now, now, int(task_id)),
        )
        return cursor.rowcount > 0
def delete_success_download_task_record(task_id: int) -> str:
    """Physically remove only a successful download-task row."""

    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT status FROM download_tasks WHERE id = ?",
            (int(task_id),),
        ).fetchone()
        if row is None:
            connection.rollback()
            return "not_found"
        if row["status"] != "success":
            connection.rollback()
            return "not_success"
        cursor = connection.execute(
            "DELETE FROM download_tasks WHERE id = ?",
            (int(task_id),),
        )
        connection.commit()
        return "deleted" if cursor.rowcount > 0 else "not_found"
def fail_download_task_deletion(task_id: int, error: str) -> None:
    now = _now()
    with connect() as connection:
        _settle_download_run(connection, int(task_id), now)
        connection.execute(
            """
            UPDATE download_tasks
            SET status = 'failed',
                stage = 'delete_failed',
                delete_requested = 0,
                pause_requested = 0,
                speed_bytes_per_sec = 0,
                eta_seconds = NULL,
                message = ?,
                error_detail = ?,
                heartbeat_at = NULL,
                updated_at = ?
            WHERE id = ?
              AND deleted_at IS NULL
            """,
            (
                "删除任务失败: {}".format(error)[:1000],
                error[:4000],
                now,
                int(task_id),
            ),
        )
def request_pause_download_task(task_id: int) -> Optional[str]:
    now = _now()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT status, stage
            FROM download_tasks
            WHERE id = ?
              AND deleted_at IS NULL
              AND delete_requested = 0
            """,
            (int(task_id),),
        ).fetchone()
        if row is None:
            connection.commit()
            return None
        status = row["status"]
        if status == "paused":
            connection.commit()
            return "paused"
        if status == "pausing":
            connection.commit()
            return "pausing"
        if status == "pending":
            connection.execute(
                """
                UPDATE download_tasks
                SET status = 'paused',
                    stage = 'paused',
                    paused_at = ?,
                    pause_requested = 0,
                    message = '任务已暂停',
                    updated_at = ?
                WHERE id = ?
                """,
                (now, now, int(task_id)),
            )
            connection.commit()
            return "paused"
        if status != "running":
            connection.commit()
            return None
        if row["stage"] not in {"waiting", "opening_page", "downloading"}:
            connection.commit()
            return None
        connection.execute(
            """
            UPDATE download_tasks
            SET status = 'pausing',
                stage = 'pausing',
                pause_requested = 1,
                message = '正在保存断点',
                updated_at = ?
            WHERE id = ?
            """,
            (now, int(task_id)),
        )
        connection.commit()
    config.ensure_data_directories()
    marker = config.DOWNLOAD_CONTROL_DIR / "task-{}.pause".format(int(task_id))
    marker.write_text("pause", encoding="utf-8")
    return "pausing"
def finish_pause_download_task(
    task_id: int,
    partial_path: Optional[str] = None,
    checkpoint_path: Optional[str] = None,
    message: str = "已暂停，断点已保留",
) -> None:
    now = _now()
    with connect() as connection:
        _settle_download_run(connection, int(task_id), now)
        row = connection.execute(
            "SELECT progress_percent, downloaded_bytes, total_bytes "
            "FROM download_tasks WHERE id = ?",
            (int(task_id),),
        ).fetchone()
        if not row:
            return
        partial_size = 0
        if partial_path:
            try:
                partial_size = Path(partial_path).stat().st_size
            except OSError:
                partial_size = 0
        downloaded_bytes = max(
            int(row["downloaded_bytes"] or 0),
            partial_size,
        )
        total_bytes = int(row["total_bytes"] or 0)
        progress = float(row["progress_percent"] or 0)
        if not total_bytes and downloaded_bytes and progress > 0:
            total_bytes = int(downloaded_bytes * 100.0 / progress)
        connection.execute(
            """
            UPDATE download_tasks
            SET status = 'paused',
                stage = 'paused',
                pause_requested = 0,
                paused_at = ?,
                downloaded_bytes = ?,
                total_bytes = ?,
                partial_path = COALESCE(?, partial_path),
                checkpoint_path = COALESCE(?, checkpoint_path),
                message = ?,
                heartbeat_at = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (
                now,
                downloaded_bytes,
                total_bytes,
                partial_path,
                checkpoint_path,
                message[:1000],
                now,
                int(task_id),
            ),
        )
def resume_download_task(task_id: int) -> bool:
    now = _now()
    with connect() as connection:
        cursor = connection.execute(
            """
            UPDATE download_tasks
            SET status = 'pending',
                stage = 'waiting',
                pause_requested = 0,
                resume_count = resume_count + 1,
                speed_bytes_per_sec = 0,
                eta_seconds = NULL,
                message = '等待从断点继续',
                error_detail = NULL,
                next_retry_at = NULL,
                finished_at = NULL,
                updated_at = ?
            WHERE id = ?
              AND deleted_at IS NULL
              AND delete_requested = 0
              AND (
                  status = 'paused'
                  OR (
                      status IN ('failed', 'cancelled')
                      AND (
                          COALESCE(partial_path, '') <> ''
                          OR COALESCE(checkpoint_path, '') <> ''
                      )
                  )
              )
            """,
            (now, int(task_id)),
        )
        resumed = cursor.rowcount > 0
    if resumed:
        marker = config.DOWNLOAD_CONTROL_DIR / "task-{}.pause".format(
            int(task_id)
        )
        try:
            marker.unlink()
        except FileNotFoundError:
            pass
    return resumed
def record_download_stall(
    task_id: int,
    error: str,
    partial_path: Optional[str] = None,
    checkpoint_path: Optional[str] = None,
    max_stalls: int = 3,
) -> str:
    now = _now()
    with connect() as connection:
        _settle_download_run(connection, int(task_id), now)
        row = connection.execute(
            """
            SELECT stall_count, attempts
            FROM download_tasks
            WHERE id = ?
            """,
            (int(task_id),),
        ).fetchone()
        if not row:
            return "missing"
        stall_count = int(row["stall_count"] or 0) + 1
        if stall_count < max(1, int(max_stalls)):
            connection.execute(
                """
                UPDATE download_tasks
                SET status = 'pending',
                    stage = 'waiting',
                    pause_requested = 0,
                    stall_count = ?,
                    stall_detected_at = ?,
                    partial_path = COALESCE(?, partial_path),
                    checkpoint_path = COALESCE(?, checkpoint_path),
                    message = ?,
                    error_detail = ?,
                    next_retry_at = ?,
                    heartbeat_at = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    stall_count,
                    now,
                    partial_path,
                    checkpoint_path,
                    "下载停滞，已保留断点并重新排队 ({}/{})".format(
                        stall_count,
                        max_stalls,
                    ),
                    error[:1000],
                    now,
                    now,
                    int(task_id),
                ),
            )
            return "pending"
        attempts = int(row["attempts"] or 0) + 1
        connection.execute(
            """
            UPDATE download_tasks
            SET status = 'failed',
                stage = 'finished',
                attempts = ?,
                stall_count = ?,
                stall_detected_at = ?,
                partial_path = COALESCE(?, partial_path),
                checkpoint_path = COALESCE(?, checkpoint_path),
                message = ?,
                error_detail = ?,
                finished_at = ?,
                heartbeat_at = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (
                attempts,
                stall_count,
                now,
                partial_path,
                checkpoint_path,
                "连续下载停滞，已达到最大自动恢复次数",
                error[:1000],
                now,
                now,
                int(task_id),
            ),
        )
        return "failed"
def download_task_pause_requested(task_id: int) -> bool:
    marker = config.DOWNLOAD_CONTROL_DIR / "task-{}.pause".format(int(task_id))
    if marker.is_file():
        return True
    with connect() as connection:
        row = connection.execute(
            "SELECT status, pause_requested FROM download_tasks WHERE id = ?",
            (int(task_id),),
        ).fetchone()
    return bool(row and (
        row["pause_requested"] or row["status"] == "pausing"
    ))
def download_task_delete_requested(task_id: int) -> bool:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT status, delete_requested, deleted_at
            FROM download_tasks
            WHERE id = ?
            """,
            (int(task_id),),
        ).fetchone()
    return bool(
        row
        and not row["deleted_at"]
        and (
            row["delete_requested"]
            or row["status"] == "deleting"
        )
    )
def retry_download_task(task_id: int) -> bool:
    now = _now()
    with connect() as connection:
        row = connection.execute(
            """
            SELECT partial_path, checkpoint_path
            FROM download_tasks
            WHERE id = ?
            """,
            (int(task_id),),
        ).fetchone()
        resume_existing = bool(
            row
            and (
                str(row["partial_path"] or "").strip()
                or str(row["checkpoint_path"] or "").strip()
            )
        )
        cursor = connection.execute(
            """
            UPDATE download_tasks
            SET status = 'pending',
                stage = 'waiting',
                progress_percent = CASE WHEN ? THEN progress_percent ELSE 0 END,
                downloaded_bytes = CASE WHEN ? THEN downloaded_bytes ELSE 0 END,
                total_bytes = CASE WHEN ? THEN total_bytes ELSE 0 END,
                speed_bytes_per_sec = 0,
                eta_seconds = NULL,
                elapsed_seconds = 0,
                run_started_at = NULL,
                attempts = 0,
                message = CASE
                    WHEN ? THEN '等待从断点继续'
                    ELSE '等待重试'
                END,
                error_detail = NULL,
                next_retry_at = NULL,
                started_at = NULL,
                finished_at = NULL,
                metadata_status = 'pending',
                updated_at = ?
            WHERE id = ?
              AND deleted_at IS NULL
              AND delete_requested = 0
              AND status IN ('failed', 'cancelled')
            """,
            (
                int(resume_existing),
                int(resume_existing),
                int(resume_existing),
                int(resume_existing),
                now,
                int(task_id),
            ),
        )
        return cursor.rowcount > 0
def recover_download_tasks() -> int:
    now = _now()
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT id, status, attempts, max_attempts, delete_requested
            FROM download_tasks
            WHERE status IN ('running', 'pausing')
            """
        ).fetchall()
        for row in rows:
            if row["delete_requested"]:
                connection.execute(
                    """
                    UPDATE download_tasks
                    SET status = 'deleting',
                        stage = 'deleting',
                        pause_requested = 0,
                        message = '服务重启后继续删除任务',
                        heartbeat_at = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now, row["id"]),
                )
                continue
            _settle_download_run(connection, int(row["id"]), now)
            if int(row["attempts"]) < int(row["max_attempts"]):
                connection.execute(
                    """
                    UPDATE download_tasks
                    SET status = 'pending',
                        stage = 'waiting',
                        pause_requested = 0,
                        message = '服务重启，等待继续',
                        heartbeat_at = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now, row["id"]),
                )
            else:
                connection.execute(
                    """
                    UPDATE download_tasks
                    SET status = 'failed',
                        stage = 'finished',
                        message = '服务重启时任务已中断，且达到最大尝试次数',
                        finished_at = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, row["id"]),
                )
    return len(rows)


PREVIEW_VERSION = 1
