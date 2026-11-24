from __future__ import annotations

import fcntl
import hashlib
import logging
import os
import shutil
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from local_video_site.core import config
from local_video_site.storage import volumes as video_storage
from local_video_site import database
from local_video_site.jobs.runtime import JobResult, JobRunner, JobStateMachine
from local_video_site.ingestion.library_scan import ScanAlreadyRunning, acquire_library_write_lock, release_library_write_lock
from local_video_site.core.search import parse_search_query
from local_video_site.ingestion.import_tasks import claim_import_job, get_import_job, run_import_job


LOGGER = logging.getLogger(__name__)
MIGRATION_STATUSES = {
    "pending",
    "running",
    "success",
    "failed",
    "cancelled",
    "needs_cleanup",
}
MIGRATION_STATUS_LABELS = {
    "pending": "等待迁移",
    "running": "迁移中",
    "success": "迁移成功",
    "failed": "迁移失败",
    "cancelled": "已取消",
    "needs_cleanup": "等待清理旧副本",
}
MIGRATION_STAGE_LABELS = {
    "waiting": "等待",
    "preflight": "检查环境",
    "copying": "复制视频",
    "verifying": "校验完整性",
    "committing": "更新视频位置",
    "cleanup": "清理旧副本",
    "finished": "完成",
}
ACTIVE_DOWNLOAD_STATUSES = {
    "pending",
    "running",
    "pausing",
    "paused",
    "deleting",
}


class MigrationError(RuntimeError):
    def __init__(self, message: str, code: str = "migration_failed"):
        self.code = code
        super().__init__(message)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_batch_id(direction: str) -> str:
    prefix = "restore" if direction == "to_local" else "archive"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return "{}-{}-{}".format(prefix, timestamp, uuid.uuid4().hex[:8])


def _active_download_video_ids() -> set:
    placeholders = ",".join("?" for _ in ACTIVE_DOWNLOAD_STATUSES)
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT video_id
            FROM download_tasks
            WHERE video_id IS NOT NULL
              AND status IN ({})
            """.format(placeholders),
            sorted(ACTIVE_DOWNLOAD_STATUSES),
        ).fetchall()
    return {int(row["video_id"]) for row in rows}


def _active_rename_video_ids() -> set:
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT DISTINCT video_id FROM video_rename_operations"
        ).fetchall()
    return {int(row["video_id"]) for row in rows}


def _candidate_eligibility(
    video: Dict,
    direction: str,
    active_migrations: set,
    active_downloads: set,
    active_renames: set,
) -> Optional[str]:
    if video.get("missing"):
        return "文件已被标记为缺失"
    if video.get("is_deleted"):
        return "视频已删除"
    if int(video["id"]) in active_migrations:
        return "已有活动迁移任务"
    if int(video["id"]) in active_downloads:
        return "存在活动下载任务"
    if int(video["id"]) in active_renames:
        return "视频正在重命名"
    storage = video_storage.resolve_video_storage(video)
    if direction == "to_external":
        if video.get("storage_volume_id") is not None:
            return "视频已经位于外置盘"
        if storage["state"] != "local_online":
            return "本地视频文件不存在"
        if video_storage.local_root_for(storage["path"]) is None:
            return "视频不在受管理的本地目录中"
    elif direction == "to_local":
        if video.get("storage_volume_id") is None:
            return "视频已经位于本地"
        if storage["state"] == "external_offline":
            return "外置盘未连接"
        if storage["state"] != "external_online":
            return "外置盘视频文件不存在"
    else:
        return "不支持的迁移方向"
    if video.get("preview_status") in {"pending", "processing"}:
        return "预览文件仍在生成"
    if video.get("metadata_status") in {"pending", "processing"}:
        return "视频元数据仍在生成"
    return None


def list_candidates(
    direction: str,
    query: str = "",
    page: int = 1,
    page_size: int = 40,
) -> Dict:
    if direction not in {"to_external", "to_local"}:
        raise ValueError("不支持的迁移方向")
    result = database.migration_candidate_page(
        direction=direction,
        query=query,
        page=page,
        page_size=page_size,
    )
    for item in result["items"]:
        if item.get("storage_volume_id") is None:
            item["resolved_path"] = str(item.get("path") or "")
        else:
            item["resolved_path"] = str(
                Path(str(item.get("storage_mount_path") or ""))
                / str(item.get("storage_root_relative_path") or "")
                / str(item.get("storage_relative_path") or "")
            )
    return result


def _video_map(video_ids: List[int]) -> Dict[int, Dict]:
    return {
        int(item["id"]): item
        for item in database.get_videos_by_ids(video_ids)
    }


def _auto_video_ids(cutoff_days: int) -> List[int]:
    cutoff = datetime.now().astimezone() - timedelta(
        days=max(1, int(cutoff_days))
    )
    return database.auto_migration_video_ids(cutoff.isoformat())


def create_batch(
    mode: str,
    direction: str,
    destination_root: str,
    video_ids: Optional[List[int]] = None,
    cutoff_days: int = 30,
    verification_mode: Optional[str] = None,
) -> Dict:
    if mode not in {"manual", "auto"}:
        raise ValueError("不支持的迁移模式")
    if direction not in {"to_external", "to_local"}:
        raise ValueError("不支持的迁移方向")
    if mode == "auto" and direction != "to_external":
        raise ValueError("自动迁移只支持迁往外置盘")
    if direction == "to_external":
        volume = video_storage.register_external_target(destination_root)
        target_root = Path(
            volume.get("selected_path")
            or video_storage.volume_root(volume)
        )
        destination_volume_id = int(volume["id"])
    else:
        target_root = video_storage.validate_target(
            "to_local",
            destination_root,
        )
        volume = None
        destination_volume_id = None
    video_ids = (
        _auto_video_ids(cutoff_days)
        if mode == "auto"
        else sorted({int(value) for value in (video_ids or [])})
    )
    if not video_ids:
        raise ValueError("没有可迁移的视频")
    maximum = config.migration_batch_max()
    if len(video_ids) > maximum:
        raise ValueError(
            "单次最多迁移 {} 个视频".format(maximum)
        )
    videos = _video_map(video_ids)
    active_migrations = video_storage.active_migration_video_ids()
    active_downloads = _active_download_video_ids()
    active_renames = _active_rename_video_ids()
    batch_id = new_batch_id(direction)
    verification_mode = (
        verification_mode
        or config.migration_verification_mode()
    )
    if verification_mode not in {"sha256", "size_mtime"}:
        verification_mode = "sha256"
    now = _now()
    skipped = []
    prepared = []
    for video_id in video_ids:
        video = videos.get(video_id)
        if video is None:
            skipped.append({"video_id": video_id, "reason": "视频不存在"})
            continue
        decorated = video_storage.decorate_video(
            video,
            active_migrations,
        )
        reason = _candidate_eligibility(
            decorated,
            direction,
            active_migrations,
            active_downloads,
            active_renames,
        )
        if reason is not None:
            skipped.append({"video_id": video_id, "reason": reason})
            continue
        source = video_storage.resolve_video_storage(decorated)
        source_path = source["path"]
        if direction == "to_local" and source["volume"] is not None:
            target_root = video_storage.validate_target(
                "to_local",
                str(target_root),
                source["volume"],
            )
        destination, relative = video_storage.destination_path_for(
            direction,
            decorated,
            target_root,
        )
        source_relative, _ = video_storage.logical_relative_path(decorated)
        source_root = (
            video_storage.volume_root(source["volume"])
            if source["volume"] is not None
            else video_storage.local_root_for(source_path)
        )
        if source_root is None:
            skipped.append({
                "video_id": video_id,
                "reason": "无法确定源目录",
            })
            continue
        prepared.append({
            "batch_id": batch_id,
            "video_id": video_id,
            "video_title": decorated["title"],
            "mode": mode,
            "direction": direction,
            "status": "pending",
            "stage": "waiting",
            "source_storage_kind": source["storage_kind"],
            "source_volume_id": (
                int(decorated["storage_volume_id"])
                if decorated.get("storage_volume_id") is not None
                else None
            ),
            "source_root": str(source_root),
            "source_relative_path": source_relative,
            "source_path": str(source_path),
            "destination_storage_kind": (
                "external" if direction == "to_external" else "local"
            ),
            "destination_volume_id": destination_volume_id,
            "destination_root": str(target_root),
            "destination_relative_path": relative,
            "destination_path": str(destination),
            "total_bytes": int(decorated.get("size") or 0),
            "verification_mode": verification_mode,
            "created_at": now,
            "updated_at": now,
        })
    if not prepared:
        reasons = "；".join(
            "{}: {}".format(item["video_id"], item["reason"])
            for item in skipped[:5]
        )
        raise ValueError(
            "没有符合条件的视频" + (
                "（{}）".format(reasons)
                if reasons
                else ""
            )
        )
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for item in prepared:
            connection.execute(
                """
                INSERT INTO video_migrations (
                    batch_id, video_id, video_title, mode, direction,
                    status, stage, source_storage_kind, source_volume_id,
                    source_root, source_relative_path, source_path,
                    destination_storage_kind, destination_volume_id,
                    destination_root, destination_relative_path,
                    destination_path, total_bytes, verification_mode,
                    created_at, updated_at
                ) VALUES (
                    :batch_id, :video_id, :video_title, :mode, :direction,
                    :status, :stage, :source_storage_kind, :source_volume_id,
                    :source_root, :source_relative_path, :source_path,
                    :destination_storage_kind, :destination_volume_id,
                    :destination_root, :destination_relative_path,
                    :destination_path, :total_bytes, :verification_mode,
                    :created_at, :updated_at
                )
                """,
                item,
            )
        connection.commit()
    return {
        "batch_id": batch_id,
        "created": len(prepared),
        "skipped": skipped,
        "total_bytes": sum(item["total_bytes"] for item in prepared),
    }


def list_migrations(
    query: str = "",
    status: str = "",
    mode: str = "",
    direction: str = "",
    page: int = 1,
    page_size: int = 30,
) -> Dict:
    clauses = ["1 = 1"]
    params: List = []
    parsed_query = parse_search_query(query)
    if parsed_query.is_id:
        if parsed_query.id_value is None:
            clauses.append("0 = 1")
        else:
            clauses.append("(m.id = ? OR m.video_id = ?)")
            params.extend([
                parsed_query.id_value,
                parsed_query.id_value,
            ])
    elif parsed_query.raw:
        query = parsed_query.raw
        escaped = (
            query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        clauses.append(
            """
            (
                m.batch_id LIKE ? ESCAPE '\\'
                OR m.video_title LIKE ? ESCAPE '\\'
                OR m.source_path LIKE ? ESCAPE '\\'
                OR m.destination_path LIKE ? ESCAPE '\\'
            )
            """
        )
        like = "%{}%".format(escaped)
        params.extend([like, like, like, like])
    if status:
        clauses.append("m.status = ?")
        params.append(status)
    if mode:
        clauses.append("m.mode = ?")
        params.append(mode)
    if direction:
        clauses.append("m.direction = ?")
        params.append(direction)
    page = max(1, int(page))
    page_size = max(1, min(100, int(page_size)))
    with database.connect() as connection:
        total = connection.execute(
            "SELECT COUNT(*) FROM video_migrations m WHERE {}".format(
                " AND ".join(clauses)
            ),
            params,
        ).fetchone()[0]
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        rows = connection.execute(
            """
            SELECT m.*,
                   v.title AS video_current_title,
                   v.path AS video_current_path,
                   v.storage_volume_id AS video_storage_volume_id
            FROM video_migrations m
            LEFT JOIN videos v ON v.id = m.video_id
            WHERE {}
            ORDER BY m.id DESC
            LIMIT ? OFFSET ?
            """.format(" AND ".join(clauses)),
            [*params, page_size, (page - 1) * page_size],
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
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


def migration_counts() -> Dict:
    result = {status: 0 for status in MIGRATION_STATUSES}
    result["total"] = 0
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT status, COUNT(*) AS count "
            "FROM video_migrations GROUP BY status"
        ).fetchall()
    for row in rows:
        if row["status"] in result:
            result[row["status"]] = int(row["count"])
        result["total"] += int(row["count"])
    result["active"] = (
        result["pending"] + result["running"] + result["needs_cleanup"]
    )
    return result


_MIGRATION_UPDATES = {
    "status",
    "stage",
    "copied_bytes",
    "progress_percent",
    "source_sha256",
    "destination_sha256",
    "error_code",
    "error_detail",
    "cancel_requested",
    "attempt_count",
    "copied_at",
    "database_committed_at",
    "source_deleted_at",
    "started_at",
    "finished_at",
}


def update_migration(migration_id: int, **fields) -> None:
    values = {
        key: value
        for key, value in fields.items()
        if key in _MIGRATION_UPDATES
    }
    if not values:
        return
    values["updated_at"] = _now()
    assignments = ", ".join("{} = ?".format(key) for key in values)
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            "SELECT status FROM video_migrations WHERE id = ?",
            (int(migration_id),),
        ).fetchone()
        if current is None:
            connection.rollback()
            return
        if "status" in values:
            JobStateMachine.validate(
                str(current["status"]),
                str(values["status"]),
            )
        connection.execute(
            "UPDATE video_migrations SET {} WHERE id = ?".format(
                assignments
            ),
            [*values.values(), int(migration_id)],
        )


def get_migration(migration_id: int) -> Optional[Dict]:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT * FROM video_migrations WHERE id = ?",
            (int(migration_id),),
        ).fetchone()
    return dict(row) if row else None


def claim_migration() -> Optional[Dict]:
    now = _now()
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT * FROM video_migrations
            WHERE status IN ('needs_cleanup', 'pending')
            ORDER BY
                CASE status WHEN 'needs_cleanup' THEN 0 ELSE 1 END,
                id ASC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            connection.commit()
            return None
        if row["status"] == "pending":
            connection.execute(
                """
                UPDATE video_migrations
                SET status = 'running',
                    stage = 'preflight',
                    cancel_requested = 0,
                    error_code = NULL,
                    error_detail = NULL,
                    attempt_count = attempt_count + 1,
                    started_at = COALESCE(started_at, ?),
                    finished_at = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, now, int(row["id"])),
            )
        connection.commit()
    task = dict(row)
    task["status"] = (
        "needs_cleanup"
        if row["status"] == "needs_cleanup"
        else "running"
    )
    if task["status"] == "running":
        task["stage"] = "preflight"
        task["attempt_count"] = int(task.get("attempt_count") or 0) + 1
    return task


def recover_interrupted_migrations() -> int:
    restored = 0
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT id, destination_path, video_id
            FROM video_migrations
            WHERE status = 'running'
            """
        ).fetchall()
        for row in rows:
            video = None
            if row["video_id"] is not None:
                video = connection.execute(
                    "SELECT path FROM videos WHERE id = ?",
                    (int(row["video_id"]),),
                ).fetchone()
            if video and str(video["path"]) == row["destination_path"]:
                status = "needs_cleanup"
                stage = "cleanup"
            else:
                status = "pending"
                stage = "waiting"
            connection.execute(
                """
                UPDATE video_migrations
                SET status = ?, stage = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, stage, _now(), int(row["id"])),
            )
            restored += 1
        connection.execute(
            """
            UPDATE video_migrations
            SET video_id = NULL, updated_at = ?
            WHERE video_id IS NOT NULL
              AND video_id NOT IN (SELECT id FROM videos)
            """,
            (_now(),),
        )
    return restored


def request_cancel(migration_id: int) -> bool:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT status FROM video_migrations WHERE id = ?",
            (int(migration_id),),
        ).fetchone()
        if row is None or row["status"] not in {"pending", "running"}:
            return False
        if row["status"] == "pending":
            connection.execute(
                """
                UPDATE video_migrations
                SET status = 'cancelled', stage = 'finished',
                    finished_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (_now(), _now(), int(migration_id)),
            )
        else:
            connection.execute(
                """
                UPDATE video_migrations
                SET cancel_requested = 1, updated_at = ?
                WHERE id = ?
                """,
                (_now(), int(migration_id)),
            )
    return True


def retry_migration(migration_id: int) -> bool:
    with database.connect() as connection:
        cursor = connection.execute(
            """
            UPDATE video_migrations
            SET status = 'pending', stage = 'waiting',
                cancel_requested = 0, error_code = NULL,
                error_detail = NULL, finished_at = NULL,
                updated_at = ?
            WHERE id = ?
              AND status IN ('failed', 'cancelled')
            """,
            (_now(), int(migration_id)),
        )
        return cursor.rowcount > 0


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(config.migration_chunk_bytes())
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _check_cancel(task: Dict) -> bool:
    row = get_migration(task["id"])
    return bool(row and row.get("cancel_requested"))


def _copy_with_progress(
    task: Dict,
    source: Path,
    destination: Path,
) -> tuple:
    temporary = destination.with_name(
        ".{}.migration-{}.part".format(
            destination.name,
            task["id"],
        )
    )
    temporary.parent.mkdir(parents=True, exist_ok=True)
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
    digest = hashlib.sha256()
    source_before = source.stat()
    copied = 0
    update_migration(
        task["id"],
        status="running",
        stage="copying",
        total_bytes=source_before.st_size,
        copied_bytes=0,
        progress_percent=0,
    )
    update_at = time.monotonic()
    try:
        with source.open("rb") as reader, temporary.open("wb") as writer:
            while True:
                if _check_cancel(task):
                    raise MigrationError("迁移已取消", "cancelled")
                chunk = reader.read(config.migration_chunk_bytes())
                if not chunk:
                    break
                writer.write(chunk)
                digest.update(chunk)
                copied += len(chunk)
                now = time.monotonic()
                if now - update_at >= 0.5:
                    progress = (
                        copied * 100.0 / source_before.st_size
                        if source_before.st_size
                        else 100.0
                    )
                    update_migration(
                        task["id"],
                        copied_bytes=copied,
                        progress_percent=min(99.9, progress),
                    )
                    update_at = now
            writer.flush()
            os.fsync(writer.fileno())
        source_after = source.stat()
        if (
            source_after.st_size != source_before.st_size
            or source_after.st_mtime_ns != source_before.st_mtime_ns
        ):
            raise MigrationError(
                "复制期间源文件发生变化",
                "source_changed",
            )
        os.utime(
            temporary,
            ns=(source_after.st_atime_ns, source_after.st_mtime_ns),
        )
        update_migration(
            task["id"],
            stage="verifying",
            copied_bytes=copied,
            progress_percent=99.9,
        )
        source_hash = digest.hexdigest()
        destination_hash = _sha256_file(temporary)
        if source_hash != destination_hash:
            raise MigrationError(
                "复制后 SHA-256 校验不一致",
                "verification_failed",
            )
        os.replace(temporary, destination)
        try:
            directory_fd = os.open(str(destination.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
        return source_hash, destination_hash, copied
    except MigrationError:
        temporary.unlink(missing_ok=True)
        raise
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise MigrationError(
            "复制视频失败: {}".format(exc),
            "copy_failed",
        ) from exc


def _commit_location(task: Dict, source: Path, destination: Path) -> None:
    video_id = task.get("video_id")
    if video_id is None:
        raise MigrationError("视频记录已不存在", "video_record_missing")
    video = database.get_video_admin(int(video_id))
    if video is None:
        raise MigrationError("视频记录已不存在", "video_record_missing")
    if video.get("is_deleted"):
        raise MigrationError("视频已删除", "video_deleted")
    current = video_storage.resolve_video_storage(video)
    if current["path"].resolve() != source.resolve():
        raise MigrationError(
            "视频位置已发生变化",
            "video_location_changed",
        )
    destination_stat = destination.stat()
    if task["direction"] == "to_external":
        storage_volume_id = task["destination_volume_id"]
        volume = database.get_storage_volume(int(storage_volume_id))
        if volume is None:
            raise MigrationError(
                "目标存储卷不存在",
                "storage_volume_missing",
            )
        try:
            storage_relative_path = str(
                destination.resolve().relative_to(
                    Path(volume["mount_path"]).resolve()
                )
            )
        except (OSError, ValueError) as exc:
            raise MigrationError(
                "目标路径超出物理存储卷",
                "storage_path_invalid",
            ) from exc
    else:
        storage_volume_id = None
        storage_relative_path = None
    now = _now()
    lock_handle = None
    try:
        try:
            lock_handle = acquire_library_write_lock()
        except ScanAlreadyRunning as exc:
            raise MigrationError(
                "视频库扫描正在进行",
                "library_scan_running",
            ) from exc
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE videos
                SET path = ?, storage_volume_id = ?,
                    storage_relative_path = ?, storage_changed_at = ?,
                    size = ?, mtime_ns = ?, missing = 0, updated_at = ?
                WHERE id = ?
                """,
                (
                    str(destination),
                    storage_volume_id,
                    storage_relative_path,
                    now,
                    destination_stat.st_size,
                    destination_stat.st_mtime_ns,
                    now,
                    int(video_id),
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise MigrationError(
                    "更新视频位置失败",
                    "video_location_changed",
                )
            connection.execute(
                """
                UPDATE download_tasks
                SET file_path = ?, file_name = ?, updated_at = ?
                WHERE video_id = ? AND deleted_at IS NULL
                """,
                (
                    str(destination),
                    destination.name,
                    now,
                    int(video_id),
                ),
            )
            connection.execute(
                """
                UPDATE video_episodes
                SET storage_volume_id = ?,
                    storage_relative_path = ?,
                    absolute_path = ?,
                    updated_at = ?
                WHERE video_id = ?
                """,
                (
                    storage_volume_id,
                    storage_relative_path,
                    str(destination),
                    now,
                    int(video_id),
                ),
            )
            connection.execute(
                """
                UPDATE video_migrations
                SET status = 'needs_cleanup',
                    stage = 'cleanup',
                    database_committed_at = ?,
                    progress_percent = 100,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, now, int(task["id"])),
            )
            connection.commit()
    finally:
        if lock_handle is not None:
            release_library_write_lock(lock_handle)


def _cleanup_source(task: Dict) -> None:
    source = Path(task["source_path"])
    destination = Path(task["destination_path"])
    if source.resolve() == destination.resolve():
        raise MigrationError("源路径和目标路径相同", "same_path")
    if source.exists():
        try:
            source.unlink()
        except OSError as exc:
            raise MigrationError(
                "旧副本清理失败: {}".format(exc),
                "cleanup_failed",
            ) from exc
    update_migration(
        task["id"],
        status="success",
        stage="finished",
        progress_percent=100,
        error_code=None,
        error_detail=None,
        source_deleted_at=_now(),
        finished_at=_now(),
    )


def process_migration(task: Dict) -> None:
    if task.get("status") == "needs_cleanup":
        _cleanup_source(task)
        return
    video_id = task.get("video_id")
    if video_id is None:
        raise MigrationError("视频记录已不存在", "video_record_missing")
    video = database.get_video_admin(int(video_id))
    if video is None:
        raise MigrationError("视频记录已不存在", "video_record_missing")
    if video.get("is_deleted"):
        raise MigrationError("视频已删除", "video_deleted")
    current = video_storage.resolve_video_storage(video)
    source = current["path"]
    destination = Path(task["destination_path"])
    if task["direction"] == "to_external":
        if current["storage_kind"] != "local" or current["state"] != "local_online":
            raise MigrationError("源视频当前不在本地", "source_not_local")
        volume = database.get_storage_volume(
            int(task["destination_volume_id"])
        )
        if not video_storage.volume_online(volume):
            raise MigrationError("目标外置盘未连接", "target_offline")
    else:
        if current["storage_kind"] != "external" or current["state"] != "external_online":
            raise MigrationError(
                "源视频当前不在可用的外置盘",
                "source_external_offline",
            )
    update_migration(
        task["id"],
        status="running",
        stage="preflight",
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_size = source.stat().st_size
    usage = shutil.disk_usage(destination.parent)
    if usage.free < source_size + config.migration_min_free_bytes():
        raise MigrationError(
            "目标磁盘剩余空间不足",
            "insufficient_space",
        )
    if destination.exists():
        source_hash = _sha256_file(source)
        destination_hash = _sha256_file(destination)
        if source_hash != destination_hash:
            raise MigrationError(
                "目标文件已存在且内容不同",
                "destination_conflict",
            )
        update_migration(
            task["id"],
            status="running",
            stage="committing",
            total_bytes=source_size,
            copied_bytes=source_size,
            progress_percent=100,
            source_sha256=source_hash,
            destination_sha256=destination_hash,
            copied_at=_now(),
        )
    else:
        source_hash, destination_hash, copied = _copy_with_progress(
            task,
            source,
            destination,
        )
        update_migration(
            task["id"],
            source_sha256=source_hash,
            destination_sha256=destination_hash,
            copied_bytes=copied,
            copied_at=_now(),
            stage="committing",
        )
    _commit_location(task, source, destination)
    refreshed = get_migration(task["id"])
    if refreshed is None:
        raise MigrationError("迁移记录不存在", "migration_record_missing")
    _cleanup_source(refreshed)


class MigrationWorker:
    def __init__(
        self,
        stop_event: threading.Event,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.stop_event = stop_event
        self.logger = logger or LOGGER
        self.lock_handle = None
        self.runner = JobRunner(
            name="video-migration-worker",
            stop_event=self.stop_event,
            claim_job=self._claim_job,
            process_job=self._process_job,
            poll_seconds=config.migration_poll_seconds,
            logger=self.logger,
        )

    def start(self) -> "MigrationWorker":
        config.ensure_data_directories()
        lock_path = config.DATA_DIR / "migration-worker.lock"
        self.lock_handle = lock_path.open("a+")
        try:
            fcntl.flock(
                self.lock_handle.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError:
            self.lock_handle.close()
            self.lock_handle = None
            self.logger.warning(
                "Migration worker not started because another worker holds the lock"
            )
            return self
        self.runner.start()
        return self

    @property
    def thread(self):
        return self.runner.thread

    def stop(self) -> None:
        self.runner.stop(timeout=15)
        if self.lock_handle is not None:
            self.lock_handle.close()
            self.lock_handle = None

    def _claim_job(self):
        import_job = claim_import_job()
        if import_job is not None:
            return {"kind": "import", "payload": import_job}
        task = claim_migration()
        if task is not None:
            return {"kind": "migration", "payload": task}
        return None

    def _process_job(self, job: Dict) -> JobResult:
        if job["kind"] == "import":
            import_job = job["payload"]
            run_import_job(import_job, self.logger)
            final_job = get_import_job(int(import_job["id"])) or {}
            return JobResult.from_status(final_job.get("status", ""))
        task = job["payload"]
        try:
            process_migration(task)
        except MigrationError as exc:
            if exc.code == "cancelled":
                update_migration(
                    task["id"],
                    status="cancelled",
                    stage="finished",
                    error_code=exc.code,
                    error_detail=str(exc),
                    finished_at=_now(),
                )
            elif exc.code in {
                "library_scan_running",
                "target_offline",
            }:
                update_migration(
                    task["id"],
                    status="pending",
                    stage="waiting",
                    error_code=exc.code,
                    error_detail=str(exc),
                )
            elif exc.code == "cleanup_failed":
                update_migration(
                    task["id"],
                    status="needs_cleanup",
                    stage="cleanup",
                    error_code=exc.code,
                    error_detail=str(exc),
                )
            else:
                update_migration(
                    task["id"],
                    status="failed",
                    stage=task.get("stage") or "preflight",
                    error_code=exc.code,
                    error_detail=str(exc),
                    finished_at=_now(),
                )
            self.logger.warning(
                "Migration failed id=%s code=%s error=%s",
                task["id"],
                exc.code,
                exc,
                extra={"operation": "video.migration.failed"},
            )
        except Exception as exc:
            update_migration(
                task["id"],
                status="failed",
                stage=task.get("stage") or "preflight",
                error_code="unexpected_error",
                error_detail=str(exc),
                finished_at=_now(),
            )
            self.logger.exception(
                "Unexpected migration failure id=%s",
                task["id"],
                extra={"operation": "video.migration.unexpected"},
            )
        final_task = get_migration(int(task["id"])) or {}
        return JobResult.from_status(final_task.get("status", ""))
