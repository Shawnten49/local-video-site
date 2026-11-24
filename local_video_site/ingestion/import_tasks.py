from __future__ import annotations

import hashlib
import logging
import os
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from local_video_site.core import config
from local_video_site.storage import volumes as video_storage
from local_video_site import database
from local_video_site.identity.permissions.content import validate_access_level
from local_video_site.media.info import probe_video
from local_video_site.media.playback.normalize import normalize_video
from local_video_site.ingestion.library_scan import ScanAlreadyRunning, acquire_library_write_lock, release_library_write_lock
from local_video_site.core.search import parse_search_query
from local_video_site.media.assets.thumbnails import generate_thumbnail


LOGGER = logging.getLogger(__name__)
VIDEO_EXTENSIONS = {
    ".mp4",
    ".m4v",
    ".webm",
    ".mkv",
    ".mov",
    ".ts",
    ".avi",
    ".rmvb",
}
IMPORT_STATUS_LABELS = {
    "pending": "等待导入",
    "running": "正在导入",
    "success": "导入成功",
    "failed": "导入失败",
    "cancelled": "已取消",
    "needs_cleanup": "等待清理源文件",
}


class ImportError(RuntimeError):
    def __init__(self, message: str, code: str = "import_failed"):
        self.code = code
        super().__init__(message)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def list_import_entries(raw_path: str) -> Dict:
    path = Path(raw_path or "/Volumes").expanduser()
    if not path.is_absolute():
        raise ImportError("目录路径必须是绝对路径", "path_not_absolute")
    path = path.resolve()
    if not path.is_dir():
        raise ImportError("目录不存在", "directory_not_found")
    directories = []
    files = []
    try:
        entries = sorted(
            path.iterdir(),
            key=lambda item: (not item.is_dir(), item.name.casefold()),
        )
    except OSError as exc:
        raise ImportError(
            "无法读取目录: {}".format(exc),
            "directory_unreadable",
        ) from exc
    for item in entries:
        if item.name.startswith(".") or item.is_symlink():
            continue
        if item.is_dir():
            directories.append({"name": item.name, "path": str(item)})
        elif item.is_file() and item.suffix.lower() in VIDEO_EXTENSIONS:
            try:
                size = item.stat().st_size
            except OSError:
                continue
            files.append({
                "name": item.name,
                "path": str(item),
                "size": size,
                "extension": item.suffix.lower(),
            })
    usage = shutil.disk_usage(path)
    return {
        "path": str(path),
        "parent": str(path.parent) if path.parent != path else "",
        "directories": directories,
        "files": files,
        "free_bytes": int(usage.free),
    }


def _is_ignored_file(path: Path) -> bool:
    name = path.name.lower()
    return (
        name.startswith(".")
        or name.endswith(".part")
        or name.endswith(".ytdl")
        or name.endswith(".range.json")
        or name.endswith(".resume.json")
    )


def _iter_source_videos(source: Path) -> Iterable[Path]:
    if source.is_file():
        if (
            not _is_ignored_file(source)
            and source.suffix.lower() in VIDEO_EXTENSIONS
        ):
            yield source
        return
    for directory, dirnames, filenames in os.walk(
        source,
        followlinks=False,
    ):
        dirnames[:] = sorted(
            name for name in dirnames if not name.startswith(".")
        )
        for filename in sorted(filenames):
            path = Path(directory) / filename
            if path.is_symlink() or _is_ignored_file(path):
                continue
            if path.suffix.lower() in VIDEO_EXTENSIONS:
                yield path


def _db_path_conflict(path: Path) -> bool:
    target = str(path.resolve()).casefold()
    with database.connect() as connection:
        rows = connection.execute("SELECT path FROM videos").fetchall()
    return any(str(row["path"]).casefold() == target for row in rows)


def _destination_conflict(path: Path) -> Optional[str]:
    if _db_path_conflict(path):
        return "视频表已经存在相同路径"
    if path.exists():
        return "目标文件已经存在"
    try:
        parent_entries = list(path.parent.iterdir())
    except FileNotFoundError:
        return None
    except OSError as exc:
        return "无法读取目标目录: {}".format(exc)
    target_name = path.name.casefold()
    for entry in parent_entries:
        if entry.name.casefold() == target_name:
            return "目标目录已经存在同名文件"
    return None


def _validate_source(source: Path) -> Path:
    source = source.expanduser()
    if not source.is_absolute():
        raise ImportError("来源路径必须是绝对路径", "source_not_absolute")
    source = source.resolve()
    if not source.exists():
        raise ImportError("来源文件或目录不存在", "source_missing")
    if source.is_symlink():
        raise ImportError("不支持导入符号链接", "source_symlink")
    for volume_root in config.external_volume_roots():
        try:
            volume_root = volume_root.resolve()
        except OSError:
            continue
        if source == volume_root:
            raise ImportError(
                "请进入具体外置硬盘目录后再选择",
                "source_is_volume_root",
            )
    unsafe_roots = [
        config.DATA_DIR,
        *config.configured_roots(),
    ]
    for root in unsafe_roots:
        try:
            if source == root or root.resolve() in source.parents:
                raise ImportError(
                    "来源不能位于当前本地视频库或应用数据目录",
                    "source_inside_local_storage",
                )
        except OSError:
            continue
    return source


def _validate_destination(raw_path: str, source: Path) -> Path:
    destination = video_storage.validate_target("to_local", raw_path)
    for root in config.external_volume_roots():
        try:
            root = root.resolve()
        except OSError:
            continue
        if destination == root or root in destination.parents:
            raise ImportError(
                "本地目标目录不能位于外置盘挂载入口",
                "destination_inside_external_storage",
            )
    if destination == source or destination in source.parents:
        raise ImportError(
            "本地目标目录不能位于来源目录中",
            "destination_inside_source",
        )
    try:
        if source in destination.parents:
            raise ImportError(
                "本地目标目录不能位于来源目录中",
                "destination_inside_source",
            )
    except OSError:
        pass
    return destination


def _candidate_rows(
    source: Path,
    destination_root: Path,
) -> tuple:
    files = []
    skipped = 0
    maximum_files = config.imports_max_files()
    for path in _iter_source_videos(source):
        try:
            stat = path.stat()
        except OSError:
            skipped += 1
            continue
        if stat.st_size < config.minimum_video_bytes():
            skipped += 1
            continue
        if source.is_file():
            relative = Path(source.name)
            source_root = source.parent
        else:
            relative = Path(source.name) / path.relative_to(source)
            source_root = source
        destination = destination_root / relative
        files.append({
            "source_path": path,
            "source_root": source_root,
            "source_relative_path": relative,
            "source_size": stat.st_size,
            "source_mtime_ns": stat.st_mtime_ns,
            "destination_path": destination,
            "conflict": _destination_conflict(destination),
        })
        if len(files) > maximum_files:
            raise ImportError(
                "单次最多导入 {} 个视频".format(maximum_files),
                "too_many_files",
            )
    return files, skipped


def preview_import(source_path: str, destination_root: str) -> Dict:
    source = _validate_source(Path(source_path))
    destination = _validate_destination(destination_root, source)
    files, skipped = _candidate_rows(source, destination)
    conflicts = [item for item in files if item["conflict"]]
    usage = shutil.disk_usage(destination)
    total_bytes = sum(item["source_size"] for item in files)
    return {
        "source_path": str(source),
        "destination_root": str(destination),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "skipped_count": skipped,
        "conflict_count": len(conflicts),
        "can_create": bool(files),
        "insufficient_space": (
            usage.free
            < total_bytes + config.migration_min_free_bytes()
        ),
        "free_bytes": int(usage.free),
        "samples": [
            {
                "source_path": str(item["source_path"]),
                "destination_path": str(item["destination_path"]),
                "size": item["source_size"],
                "conflict": item["conflict"],
            }
            for item in files[:20]
        ],
    }


def create_import_batch(
    source_path: str,
    destination_root: str,
    delete_source: bool,
    access_level: int = 0,
    verification_mode: str = "sha256",
) -> Dict:
    try:
        access_level = validate_access_level(access_level)
    except ValueError as exc:
        raise ImportError(str(exc), "invalid_access_level") from exc
    source = _validate_source(Path(source_path))
    destination = _validate_destination(destination_root, source)
    files, skipped = _candidate_rows(source, destination)
    if not files:
        raise ImportError("来源中没有可导入的视频", "empty_source")
    usage = shutil.disk_usage(destination)
    total_bytes = sum(item["source_size"] for item in files)
    if usage.free < total_bytes + config.migration_min_free_bytes():
        raise ImportError("本地目标磁盘剩余空间不足", "insufficient_space")
    now = _now()
    batch_id = "import-{}-{}".format(
        datetime.now().strftime("%Y%m%d-%H%M%S"),
        uuid.uuid4().hex[:8],
    )
    rows = []
    with database.connect() as connection:
        active_sources = {
            row["source_path"]
            for row in connection.execute(
                """
                SELECT source_path FROM video_import_jobs
                WHERE status IN ('pending', 'running', 'needs_cleanup')
                """
            ).fetchall()
        }
    for item in files:
        source_file = item["source_path"]
        if str(source_file) in active_sources:
            skipped += 1
            continue
        rows.append({
            "batch_id": batch_id,
            "source_path": str(source_file),
            "source_root": str(item["source_root"]),
            "source_relative_path": str(item["source_relative_path"]),
            "source_size": item["source_size"],
            "source_mtime_ns": item["source_mtime_ns"],
            "destination_root": str(destination),
            "destination_relative_path": str(
                item["destination_path"].relative_to(destination)
            ),
            "destination_path": str(item["destination_path"]),
            "delete_source": 1 if delete_source else 0,
            "access_level": int(access_level),
            "verification_mode": (
                verification_mode
                if verification_mode in {"sha256", "size_mtime"}
                else "sha256"
            ),
            "created_at": now,
            "updated_at": now,
        })
    if not rows:
        raise ImportError("没有可创建的外部视频导入任务", "empty_tasks")
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for row in rows:
            connection.execute(
                """
                INSERT INTO video_import_jobs (
                    batch_id, source_path, source_root,
                    source_relative_path, source_size, source_mtime_ns,
                    destination_root, destination_relative_path,
                    destination_path, delete_source, access_level,
                    status, stage,
                    verification_mode, created_at, updated_at
                ) VALUES (
                    :batch_id, :source_path, :source_root,
                    :source_relative_path, :source_size, :source_mtime_ns,
                    :destination_root, :destination_relative_path,
                    :destination_path, :delete_source, :access_level,
                    'pending', 'waiting',
                    :verification_mode, :created_at, :updated_at
                )
                """,
                row,
            )
        connection.commit()
    return {
        "batch_id": batch_id,
        "created": len(rows),
        "skipped": skipped,
        "total_bytes": total_bytes,
    }


def list_import_jobs(
    query: str = "",
    status: str = "",
    access_level: Optional[int] = None,
    page: int = 1,
    page_size: int = 30,
) -> Dict:
    page = max(1, int(page))
    page_size = max(1, min(100, int(page_size)))
    clauses = ["1 = 1"]
    params: List = []
    parsed_query = parse_search_query(query)
    if parsed_query.is_id:
        if parsed_query.id_value is None:
            clauses.append("0 = 1")
        else:
            clauses.append("j.id = ?")
            params.append(parsed_query.id_value)
    elif parsed_query.raw:
        escaped = (
            parsed_query.raw.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        like = "%{}%".format(escaped)
        clauses.append(
            """
            (
                j.batch_id LIKE ? ESCAPE '\\'
                OR j.source_path LIKE ? ESCAPE '\\'
                OR j.destination_path LIKE ? ESCAPE '\\'
                OR COALESCE(v.title, '') LIKE ? ESCAPE '\\'
            )
            """
        )
        params.extend([like, like, like, like])
    if status:
        clauses.append("j.status = ?")
        params.append(status)
    if access_level is not None:
        clauses.append("j.access_level = ?")
        params.append(int(access_level))
    where = " AND ".join(clauses)
    with database.connect() as connection:
        total = int(connection.execute(
            """
            SELECT COUNT(*)
            FROM video_import_jobs j
            LEFT JOIN video_catalog v ON v.id = j.video_id
            WHERE {}
            """.format(where),
            params,
        ).fetchone()[0])
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        rows = connection.execute(
            """
            SELECT j.*, v.title AS video_title, v.preview_status AS video_preview_status
            FROM video_import_jobs j
            LEFT JOIN video_catalog v ON v.id = j.video_id
            WHERE {}
            ORDER BY j.id DESC
            LIMIT ? OFFSET ?
            """.format(where),
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["status_label"] = IMPORT_STATUS_LABELS.get(
            item["status"],
            item["status"],
        )
        items.append(item)
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


def import_job_counts() -> Dict:
    result = {status: 0 for status in IMPORT_STATUS_LABELS}
    result["total"] = 0
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM video_import_jobs
            GROUP BY status
            """
        ).fetchall()
    for row in rows:
        if row["status"] in result:
            result[row["status"]] = int(row["count"])
        result["total"] += int(row["count"])
    result["active"] = (
        result["pending"]
        + result["running"]
        + result["needs_cleanup"]
    )
    return result


def get_import_jobs_by_ids(job_ids: List[int]) -> List[Dict]:
    normalized = sorted({
        int(value) for value in job_ids if value is not None
    })
    if not normalized:
        return []
    placeholders = ",".join("?" for _ in normalized)
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT j.*, v.title AS video_title,
                   v.preview_status AS video_preview_status
            FROM video_import_jobs j
            LEFT JOIN video_catalog v ON v.id = j.video_id
            WHERE j.id IN ({})
            ORDER BY j.id DESC
            """.format(placeholders),
            normalized,
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["status_label"] = IMPORT_STATUS_LABELS.get(
            item["status"],
            item["status"],
        )
        items.append(item)
    return items


_IMPORT_UPDATES = {
    "video_id",
    "status",
    "stage",
    "progress_percent",
    "copied_bytes",
    "source_sha256",
    "destination_sha256",
    "metadata_status",
    "thumbnail_status",
    "preview_status",
    "error_code",
    "error_detail",
    "attempt_count",
    "copied_at",
    "video_inserted_at",
    "source_deleted_at",
    "started_at",
    "finished_at",
    "destination_path",
    "destination_relative_path",
}


def update_import_job(job_id: int, **fields) -> None:
    values = {
        key: value
        for key, value in fields.items()
        if key in _IMPORT_UPDATES
    }
    if not values:
        return
    values["updated_at"] = _now()
    assignments = ", ".join("{} = ?".format(key) for key in values)
    with database.connect() as connection:
        connection.execute(
            "UPDATE video_import_jobs SET {} WHERE id = ?".format(
                assignments
            ),
            [*values.values(), int(job_id)],
        )


def get_import_job(job_id: int) -> Optional[Dict]:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT * FROM video_import_jobs WHERE id = ?",
            (int(job_id),),
        ).fetchone()
    return dict(row) if row else None


def claim_import_job() -> Optional[Dict]:
    now = _now()
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT * FROM video_import_jobs
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
                UPDATE video_import_jobs
                SET status = 'running', stage = 'preflight',
                    attempt_count = attempt_count + 1,
                    started_at = COALESCE(started_at, ?),
                    finished_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, now, int(row["id"])),
            )
        connection.commit()
    task = dict(row)
    if task["status"] == "pending":
        task["status"] = "running"
        task["stage"] = "preflight"
    return task


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(config.migration_chunk_bytes())
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _copy_with_progress(task: Dict, source: Path, destination: Path) -> tuple:
    temporary = destination.with_name(
        ".{}.import-{}.part".format(destination.name, task["id"])
    )
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.unlink(missing_ok=True)
    source_before = source.stat()
    digest = hashlib.sha256()
    copied = 0
    update_import_job(
        task["id"],
        status="running",
        stage="copying",
    )
    updated_at = time.monotonic()
    try:
        with source.open("rb") as reader, temporary.open("wb") as writer:
            while True:
                chunk = reader.read(config.migration_chunk_bytes())
                if not chunk:
                    break
                writer.write(chunk)
                digest.update(chunk)
                copied += len(chunk)
                now = time.monotonic()
                if now - updated_at >= 0.5:
                    progress = (
                        copied * 100.0 / source_before.st_size
                        if source_before.st_size
                        else 100.0
                    )
                    update_import_job(
                        task["id"],
                        copied_bytes=copied,
                        progress_percent=min(99.9, progress),
                    )
                    updated_at = now
            writer.flush()
            os.fsync(writer.fileno())
        source_after = source.stat()
        if (
            source_after.st_size != source_before.st_size
            or source_after.st_mtime_ns != source_before.st_mtime_ns
        ):
            raise ImportError(
                "复制期间源文件发生变化",
                "source_changed",
            )
        os.utime(
            temporary,
            ns=(source_after.st_atime_ns, source_after.st_mtime_ns),
        )
        source_hash = digest.hexdigest()
        destination_hash = _sha256_file(temporary)
        if source_hash != destination_hash:
            raise ImportError(
                "复制后 SHA-256 校验不一致",
                "verification_failed",
            )
        os.replace(temporary, destination)
        return source_hash, destination_hash, copied
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _ensure_resources(job: Dict, video: Dict) -> None:
    path = Path(video["path"])
    if not path.is_file():
        raise ImportError("入库后的视频文件不存在", "registered_file_missing")
    try:
        info = probe_video(path)
        thumbnail = generate_thumbnail(
            video_path=path,
            size=video["size"],
            mtime_ns=video["mtime_ns"],
            duration=info.get("duration"),
        )
        status = "ready" if info or thumbnail else "unavailable"
        database.update_metadata(
            video_id=video["id"],
            info=info,
            thumbnail_path=str(thumbnail) if thumbnail else None,
            status=status,
            error=None if status == "ready" else "metadata unavailable",
        )
        update_import_job(
            job["id"],
            metadata_status=status,
            thumbnail_status="ready" if thumbnail else "failed",
            preview_status="queued",
            stage="preview_queued",
        )
    except Exception as exc:
        update_import_job(
            job["id"],
            metadata_status="failed",
            thumbnail_status="failed",
            error_code="resource_generation_failed",
            error_detail=str(exc),
        )


def _normalize_imported_container(
    job: Dict,
    video: Dict,
    destination: Path,
    logger: logging.Logger,
) -> tuple:
    if destination.suffix.lower() not in {".ts", ".mts", ".m2ts"}:
        return video, destination
    if not config.normalize_ts_on_import():
        return video, destination
    try:
        result = normalize_video(video, logger=logger)
        if result.get("status") != "ready":
            return video, destination
        normalized_path = Path(result["path"])
        normalized_video = database.get_video_admin(int(video["id"])) or video
        try:
            relative = normalized_path.relative_to(
                Path(job["destination_root"]).resolve()
            )
        except ValueError:
            relative = Path(normalized_path.name)
        update_import_job(
            job["id"],
            destination_path=str(normalized_path),
            destination_relative_path=str(relative),
        )
        logger.info(
            "Imported TS normalized to MP4 job_id=%s video_id=%s path=%s",
            job["id"],
            video["id"],
            normalized_path,
            extra={"operation": "video.import.ts_normalized"},
        )
        return normalized_video, normalized_path
    except Exception as exc:
        logger.warning(
            "Imported TS normalization failed; keeping source job_id=%s "
            "video_id=%s error=%s",
            job["id"],
            video.get("id"),
            exc,
            extra={"operation": "video.import.ts_normalize_failed"},
        )
        return database.get_video_admin(int(video["id"])) or video, destination


def _insert_video(job: Dict, destination: Path) -> Dict:
    lock_handle = None
    try:
        try:
            lock_handle = acquire_library_write_lock()
        except ScanAlreadyRunning as exc:
            raise ImportError(
                "视频库扫描正在进行",
                "library_scan_running",
            ) from exc
        if _db_path_conflict(destination):
            raise ImportError(
                "视频表已经存在相同路径",
                "destination_conflict",
            )
        stat = destination.stat()
        status = database.upsert_video(
            path=destination,
            title=destination.stem,
            directory=Path(job["destination_root"]).name,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        )
        if status != "added":
            raise ImportError(
                "视频表已经存在该路径",
                "destination_conflict",
            )
        video = database.get_video_by_path(destination)
        if video is None:
            raise ImportError("视频入库后无法读取记录", "video_insert_failed")
        database.set_video_access_level(
            int(video["id"]),
            int(job.get("access_level") or 0),
        )
        update_import_job(
            job["id"],
            video_id=video["id"],
            stage="metadata",
            video_inserted_at=_now(),
        )
        return video
    finally:
        if lock_handle is not None:
            release_library_write_lock(lock_handle)


def _cleanup_source(job: Dict) -> None:
    if not int(job.get("delete_source") or 0):
        update_import_job(
            job["id"],
            status="success",
            stage="finished",
            progress_percent=100,
            finished_at=_now(),
        )
        return
    source = Path(job["source_path"])
    if source.exists():
        try:
            source.unlink()
        except OSError as exc:
            raise ImportError(
                "删除外盘源文件失败: {}".format(exc),
                "source_cleanup_failed",
            ) from exc
    update_import_job(
        job["id"],
        status="success",
        stage="finished",
        progress_percent=100,
        source_deleted_at=_now(),
        finished_at=_now(),
    )


def run_import_job(job: Dict, logger: Optional[logging.Logger] = None) -> None:
    logger = logger or LOGGER
    try:
        process_import_job(job, logger)
    except ImportError as exc:
        if exc.code == "library_scan_running":
            update_import_job(
                job["id"],
                status="pending",
                stage="waiting",
                error_code=exc.code,
                error_detail=str(exc),
            )
        elif exc.code == "source_cleanup_failed":
            update_import_job(
                job["id"],
                status="needs_cleanup",
                stage="cleanup",
                error_code=exc.code,
                error_detail=str(exc),
            )
        else:
            update_import_job(
                job["id"],
                status="failed",
                stage=job.get("stage") or "preflight",
                error_code=exc.code,
                error_detail=str(exc),
                finished_at=_now(),
            )
        logger.warning(
            "External video import failed id=%s code=%s error=%s",
            job["id"],
            exc.code,
            exc,
            extra={"operation": "video.import.failed"},
        )
    except Exception as exc:
        update_import_job(
            job["id"],
            status="failed",
            stage=job.get("stage") or "preflight",
            error_code="unexpected_error",
            error_detail=str(exc),
            finished_at=_now(),
        )
        logger.exception(
            "Unexpected external video import failure id=%s",
            job["id"],
        )


def process_import_job(job: Dict, logger: logging.Logger) -> None:
    destination = Path(job["destination_path"])
    source = Path(job["source_path"])
    if job.get("status") == "needs_cleanup":
        _cleanup_source(job)
        return
    video = (
        database.get_video_admin(int(job["video_id"]))
        if job.get("video_id") is not None
        else None
    )
    if video is not None:
        _ensure_resources(job, video)
        _cleanup_source(job)
        return
    if not source.is_file():
        raise ImportError("来源视频文件不存在", "source_missing")
    conflict = _destination_conflict(destination)
    if conflict and not destination.is_file():
        raise ImportError(conflict, "destination_conflict")
    usage = shutil.disk_usage(destination.parent)
    if usage.free < source.stat().st_size + config.migration_min_free_bytes():
        raise ImportError("本地目标磁盘剩余空间不足", "insufficient_space")
    if destination.is_file():
        source_hash = _sha256_file(source)
        destination_hash = _sha256_file(destination)
        if source_hash != destination_hash:
            raise ImportError(
                "目标文件已经存在且内容不同",
                "destination_conflict",
            )
        copied = source.stat().st_size
        update_import_job(
            job["id"],
            stage="committing_video",
            copied_bytes=copied,
            progress_percent=100,
            source_sha256=source_hash,
            destination_sha256=destination_hash,
            copied_at=_now(),
        )
    else:
        source_hash, destination_hash, copied = _copy_with_progress(
            job,
            source,
            destination,
        )
        update_import_job(
            job["id"],
            stage="verifying",
            source_sha256=source_hash,
            destination_sha256=destination_hash,
            copied_bytes=copied,
            copied_at=_now(),
        )
    try:
        video = _insert_video(job, destination)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    video, destination = _normalize_imported_container(
        job,
        video,
        destination,
        logger,
    )
    _ensure_resources(job, video)
    _cleanup_source(job)


def recover_interrupted_imports() -> int:
    with database.connect() as connection:
        cursor = connection.execute(
            """
            UPDATE video_import_jobs
            SET status = 'pending', stage = 'waiting', updated_at = ?
            WHERE status = 'running'
            """,
            (_now(),),
        )
        return cursor.rowcount
