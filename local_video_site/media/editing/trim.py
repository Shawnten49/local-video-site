from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from local_video_site.core import config
from local_video_site.storage import volumes as video_storage
from local_video_site import database
from local_video_site.media.ffmpeg import ffmpeg_slot
from local_video_site.jobs.runtime import JobResult, JobRunner
from local_video_site.media.info import available_ffprobe, probe_video
from local_video_site.media.playback.engine import playback_cache_path
from local_video_site.ingestion.library_scan import ScanAlreadyRunning, acquire_library_write_lock, release_library_write_lock


LOGGER = logging.getLogger(__name__)
VALID_TRIM_MODES = {
    "after_keyframe",
    "before_keyframe",
    "precise",
}
TRIM_BACKUP_DIRECTORY = ".video-trim-backups"


class VideoTrimError(RuntimeError):
    def __init__(self, message: str, code: str = "trim_error"):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _job_id() -> str:
    return "trim-{}-{}".format(
        datetime.now().strftime("%Y%m%d%H%M%S"),
        uuid.uuid4().hex[:10],
    )


def _timestamp(value) -> Optional[float]:
    try:
        number = float(value)
        return number if number >= 0 else None
    except (TypeError, ValueError):
        return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _backup_path_for(
    source: Path,
    video_id: int,
    job_id: str,
) -> Path:
    return (
        source.parent
        / TRIM_BACKUP_DIRECTORY
        / str(int(video_id))
        / "{}{}".format(job_id, source.suffix)
    )


def _validate_backup_path(
    job: Dict,
    source: Path,
) -> Path:
    expected = _backup_path_for(
        source,
        int(job["video_id"]),
        str(job["job_id"]),
    ).resolve()
    try:
        actual = Path(str(job.get("backup_path") or "")).resolve()
    except OSError as exc:
        raise VideoTrimError(
            "安全备份路径无效",
            "backup_invalid",
        ) from exc
    if actual != expected:
        raise VideoTrimError(
            "安全备份路径不在受管理目录中",
            "backup_invalid",
        )
    return actual


def _video_source(video: Dict) -> Path:
    resolution = video_storage.resolve_video_storage(video)
    if resolution["state"] == "external_offline":
        raise VideoTrimError(
            "外置存储未连接，不能剪辑片头",
            "storage_offline",
        )
    if resolution["state"] == "file_missing":
        raise VideoTrimError(
            "视频文件不存在，不能剪辑片头",
            "video_file_missing",
        )
    path = Path(resolution["path"])
    if not path.is_file():
        raise VideoTrimError(
            "视频文件不存在，不能剪辑片头",
            "video_file_missing",
        )
    return path


def _validate_start(start_seconds: float, duration: float) -> float:
    try:
        start = float(start_seconds)
    except (TypeError, ValueError) as exc:
        raise VideoTrimError("片头时间格式无效", "invalid_start") from exc
    if start <= 0 or start >= duration:
        raise VideoTrimError(
            "片头时间必须大于 0 且小于视频时长",
            "invalid_start",
        )
    return round(start, 3)


def _keyframe_times(
    source: Path,
    start_seconds: float,
) -> tuple:
    ffprobe = available_ffprobe()
    if not ffprobe:
        raise VideoTrimError("ffprobe is not installed", "ffprobe_missing")
    window = config.trim_keyframe_window_seconds()
    interval_start = max(0.0, start_seconds - window)
    interval_end = start_seconds + window
    command = [
        ffprobe,
        "-v",
        "error",
        "-read_intervals",
        "{}%+{}".format(interval_start, interval_end - interval_start),
        "-select_streams",
        "v:0",
        "-skip_frame",
        "nokey",
        "-show_entries",
        "frame=best_effort_timestamp_time,pkt_dts_time",
        "-of",
        "json",
        str(source),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=config.trim_probe_timeout_seconds(),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VideoTrimError(
            "无法分析视频关键帧",
            "keyframe_probe_failed",
        ) from exc
    if completed.returncode != 0:
        raise VideoTrimError(
            "无法分析视频关键帧",
            "keyframe_probe_failed",
        )
    try:
        payload = json.loads(completed.stdout)
    except (TypeError, ValueError) as exc:
        raise VideoTrimError(
            "关键帧数据格式无效",
            "keyframe_probe_failed",
        ) from exc
    times = sorted({
        value
        for frame in payload.get("frames") or []
        for value in (
            _timestamp(frame.get("best_effort_timestamp_time")),
            _timestamp(frame.get("pkt_dts_time")),
        )
        if value is not None
    })
    previous = max(
        (value for value in times if value <= start_seconds),
        default=None,
    )
    following = min(
        (value for value in times if value >= start_seconds),
        default=None,
    )
    return previous, following


def _frame_dimensions_at(
    source: Path,
    seconds: float,
) -> dict:
    ffprobe = available_ffprobe()
    if not ffprobe:
        return {}
    try:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-ss",
                "{:.3f}".format(float(seconds)),
                "-i",
                str(source),
                "-select_streams",
                "v:0",
                "-read_intervals",
                "%+#1",
                "-show_entries",
                "frame=width,height",
                "-of",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=config.trim_probe_timeout_seconds(),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if completed.returncode != 0:
        return {}
    try:
        frames = json.loads(completed.stdout).get("frames") or []
    except (TypeError, ValueError):
        return {}
    for frame in frames:
        width = frame.get("width")
        height = frame.get("height")
        if width and height:
            return {
                "width": int(width),
                "height": int(height),
            }
    return {}


def probe_trim_options(video_id: int, start_seconds: float) -> Dict:
    video = database.get_video_admin(int(video_id))
    if video is None or video.get("is_deleted"):
        raise VideoTrimError("视频不存在", "video_not_found")
    source = _video_source(video)
    info = probe_video(source)
    duration = _timestamp(info.get("duration")) or _timestamp(video.get("duration"))
    if not duration:
        raise VideoTrimError("无法读取视频时长", "duration_unavailable")
    start = _validate_start(start_seconds, duration)
    previous, following = _keyframe_times(source, start)
    if following is None and previous is None:
        raise VideoTrimError(
            "目标时间附近没有找到关键帧",
            "keyframe_not_found",
        )
    return {
        "video_id": int(video_id),
        "path": str(source),
        "duration": duration,
        "requested_start_seconds": start,
        "previous_keyframe": previous,
        "next_keyframe": following,
        "recommended_mode": (
            "after_keyframe" if following is not None else "before_keyframe"
        ),
        "estimated_duration": {
            "after_keyframe": (
                round(duration - following, 3)
                if following is not None
                else None
            ),
            "before_keyframe": (
                round(duration - (previous or 0), 3)
                if previous is not None
                else None
            ),
            "precise": round(duration - start, 3),
        },
        "video_codec": info.get("video_codec"),
        "audio_codec": info.get("audio_codec"),
        "width": info.get("width"),
        "height": info.get("height"),
    }


def _actual_cut(options: Dict, mode: str) -> float:
    if mode == "after_keyframe":
        value = options.get("next_keyframe")
    elif mode == "before_keyframe":
        value = options.get("previous_keyframe")
    elif mode == "precise":
        value = options.get("requested_start_seconds")
    else:
        raise VideoTrimError("不支持的剪辑方式", "invalid_mode")
    cut = _timestamp(value)
    if not cut or cut <= 0:
        raise VideoTrimError("没有可用的剪切位置", "cut_unavailable")
    return round(cut, 3)


def create_trim_job(
    video_id: int,
    start_seconds: float,
    mode: str,
    expected_path: str = "",
    expected_mtime_ns: Optional[int] = None,
    created_by_user_id: Optional[int] = None,
    created_by_username: Optional[str] = None,
) -> Dict:
    if mode not in VALID_TRIM_MODES:
        raise VideoTrimError("不支持的剪辑方式", "invalid_mode")
    options = probe_trim_options(video_id, start_seconds)
    source = Path(options["path"])
    if expected_path and source.resolve() != Path(expected_path).expanduser().resolve():
        raise VideoTrimError(
            "视频路径已变化，请刷新后重试",
            "stale_video",
        )
    source_stat = source.stat()
    if not os.access(source.parent, os.W_OK):
        raise VideoTrimError(
            "视频所在目录不可写，不能剪辑片头",
            "target_not_writable",
        )
    if expected_mtime_ns is not None and int(expected_mtime_ns) != source_stat.st_mtime_ns:
        raise VideoTrimError(
            "视频文件已变化，请刷新后重试",
            "stale_video",
        )
    with database.connect() as connection:
        active = connection.execute(
            """
            SELECT 1 FROM video_trim_jobs
            WHERE video_id = ?
              AND status IN ('pending', 'running', 'committing')
            LIMIT 1
            """,
            (int(video_id),),
        ).fetchone()
        if active:
            raise VideoTrimError(
                "该视频已有片头剪辑任务",
                "trim_busy",
            )
        active_operation = connection.execute(
            """
            SELECT
                EXISTS(
                    SELECT 1 FROM video_migrations
                    WHERE video_id = ?
                      AND status IN ('pending', 'running', 'needs_cleanup')
                )
                OR EXISTS(
                    SELECT 1 FROM download_tasks
                    WHERE video_id = ?
                      AND status IN (
                          'pending', 'running', 'pausing', 'paused', 'deleting'
                      )
                )
                OR EXISTS(
                    SELECT 1 FROM video_rename_operations
                    WHERE video_id = ?
                ) AS busy
            """,
            (int(video_id), int(video_id), int(video_id)),
        ).fetchone()
        if active_operation and int(active_operation["busy"]):
            raise VideoTrimError(
                "视频正在迁移、下载或重命名，暂时不能剪辑",
                "trim_busy",
            )
        now = _now()
        job_id = _job_id()
        connection.execute(
            """
            INSERT INTO video_trim_jobs (
                job_id, video_id, status, stage, progress_percent,
                requested_start_seconds, actual_cut_seconds, cut_mode,
                keyframe_before_seconds, keyframe_after_seconds,
                source_path, source_size, source_mtime_ns, source_duration,
                source_width, source_height, source_video_codec,
                source_audio_codec,
                created_by_user_id, created_by_username,
                created_at, updated_at
            ) VALUES (
                ?, ?, 'pending', 'waiting', 0, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                job_id,
                int(video_id),
                options["requested_start_seconds"],
                _actual_cut(options, mode),
                mode,
                options.get("previous_keyframe"),
                options.get("next_keyframe"),
                str(source),
                source_stat.st_size,
                source_stat.st_mtime_ns,
                options.get("duration"),
                options.get("width"),
                options.get("height"),
                options.get("video_codec"),
                options.get("audio_codec"),
                created_by_user_id,
                created_by_username,
                now,
                now,
            ),
        )
        connection.commit()
    return get_trim_job(job_id) or {}


def get_trim_job(job_id: str) -> Optional[Dict]:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT * FROM video_trim_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    return dict(row) if row else None


def _update_job(job_id: str, **fields) -> None:
    allowed = {
        "status",
        "stage",
        "progress_percent",
        "temporary_path",
        "backup_path",
        "backup_status",
        "backup_size",
        "backup_mtime_ns",
        "backup_sha256",
        "backup_created_at",
        "backup_deleted_at",
        "output_size",
        "output_duration",
        "restore_count",
        "last_restored_at",
        "last_restored_by_user_id",
        "last_restored_by_username",
        "restore_temporary_path",
        "restore_rollback_path",
        "restore_status",
        "error_code",
        "error_detail",
        "started_at",
        "finished_at",
        "updated_at",
    }
    values = {key: value for key, value in fields.items() if key in allowed}
    if not values:
        return
    values.setdefault("updated_at", _now())
    assignments = ", ".join("{} = ?".format(key) for key in values)
    with database.connect() as connection:
        connection.execute(
            "UPDATE video_trim_jobs SET {} WHERE job_id = ?".format(
                assignments
            ),
            [*values.values(), job_id],
        )


def cancel_trim_job(job_id: str) -> bool:
    now = _now()
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT status FROM video_trim_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            connection.rollback()
            return False
        if row["status"] == "pending":
            connection.execute(
                """
                UPDATE video_trim_jobs
                SET status = 'cancelled', stage = 'finished',
                    finished_at = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (now, now, job_id),
            )
        elif row["status"] in {"running", "committing"}:
            connection.execute(
                """
                UPDATE video_trim_jobs
                SET cancel_requested = 1, updated_at = ?
                WHERE job_id = ?
                """,
                (now, job_id),
            )
        else:
            connection.rollback()
            return False
        connection.commit()
    return True


def _trim_command(
    ffmpeg: str,
    source: Path,
    temporary: Path,
    cut_seconds: float,
    mode: str,
) -> list:
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        "{:.3f}".format(cut_seconds),
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
    ]
    if mode == "precise":
        command.extend([
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
        ])
    else:
        command.extend([
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
        ])
    command.extend([
        "-movflags",
        "+faststart",
        "-y",
        str(temporary),
    ])
    return command


def _reset_assets(video: Dict, output_info: Dict, size: int, mtime_ns: int) -> None:
    now = _now()
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE videos
            SET size = ?, mtime_ns = ?, updated_at = ?
            WHERE id = ? AND missing = 0 AND is_deleted = 0
            """,
            (int(size), int(mtime_ns), now, int(video["id"])),
        )
        connection.execute(
            """
            UPDATE video_media_info
            SET duration = COALESCE(?, duration),
                width = COALESCE(?, width),
                height = COALESCE(?, height),
                video_codec = COALESCE(?, video_codec),
                audio_codec = COALESCE(?, audio_codec),
                metadata_status = 'ready',
                metadata_error = NULL,
                normalization_status = 'none',
                normalization_error = NULL,
                updated_at = ?
            WHERE video_id = ?
            """,
            (
                output_info.get("duration"),
                output_info.get("width"),
                output_info.get("height"),
                output_info.get("video_codec"),
                output_info.get("audio_codec"),
                now,
                int(video["id"]),
            ),
        )
        connection.execute(
            """
            UPDATE video_assets
            SET preview_path = NULL, preview_status = 'pending',
                preview_error = NULL, preview_source_size = NULL,
                preview_source_mtime_ns = NULL, updated_at = ?
            WHERE video_id = ?
            """,
            (now, int(video["id"])),
        )
        connection.execute(
            """
            UPDATE download_tasks
            SET total_bytes = ?, downloaded_bytes = ?, updated_at = ?
            WHERE video_id = ? AND deleted_at IS NULL
            """,
            (int(size), int(size), now, int(video["id"])),
        )
        connection.execute(
            "DELETE FROM playback_cache_entries WHERE video_id = ?",
            (int(video["id"]),),
        )
        connection.commit()


def _delete_preview_files(video: Dict) -> None:
    preview_path = str(video.get("preview_path") or "").strip()
    if not preview_path:
        return
    path = Path(preview_path)
    if not path.is_absolute():
        path = config.PREVIEW_DIR / path
    try:
        path.unlink(missing_ok=True)
    except OSError:
        LOGGER.warning("Could not remove trimmed video preview: %s", path)


def process_trim_job(job: Dict, logger: Optional[logging.Logger] = None) -> Dict:
    logger = logger or LOGGER
    video = database.get_video_admin(int(job["video_id"]))
    if video is None or video.get("is_deleted"):
        raise VideoTrimError("视频不存在或已删除", "video_not_found")
    source = Path(job["source_path"])
    if not source.is_file():
        raise VideoTrimError("源视频不存在", "video_file_missing")
    current_stat = source.stat()
    if (
        current_stat.st_size != int(job["source_size"])
        or current_stat.st_mtime_ns != int(job["source_mtime_ns"])
    ):
        raise VideoTrimError("视频文件已经变化", "stale_video")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise VideoTrimError("ffmpeg is not installed", "ffmpeg_missing")
    if not os.access(source.parent, os.W_OK):
        raise VideoTrimError("视频目录不可写", "target_not_writable")
    free_bytes = shutil.disk_usage(source.parent).free
    if free_bytes < current_stat.st_size + config.playback_remux_disk_reserve_bytes():
        raise VideoTrimError("磁盘可用空间不足", "insufficient_space")
    before = probe_video(source)
    cut_seconds = float(job["actual_cut_seconds"])
    temporary = source.with_name(
        ".{}.trim-{}.tmp{}".format(
            source.stem,
            uuid.uuid4().hex[:10],
            source.suffix,
        )
    )
    backup = _backup_path_for(
        source,
        int(job["video_id"]),
        str(job["job_id"]),
    )
    backup.parent.mkdir(parents=True, exist_ok=True)
    _update_job(
        job["job_id"],
        stage="trimming",
        progress_percent=20,
        temporary_path=str(temporary),
        backup_path=str(backup),
    )
    command = _trim_command(
        ffmpeg,
        source,
        temporary,
        cut_seconds,
        job["cut_mode"],
    )
    try:
        with ffmpeg_slot("video_trim"):
            completed = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=config.trim_timeout_seconds(),
                check=False,
            )
        if completed.returncode != 0 or not temporary.is_file():
            detail = (completed.stderr or "").strip()
            raise VideoTrimError(
                "FFmpeg 剪辑失败{}".format(
                    ": {}".format(detail[-1000:]) if detail else ""
                ),
                "ffmpeg_failed",
            )
        refreshed_job = get_trim_job(job["job_id"])
        if refreshed_job and int(refreshed_job.get("cancel_requested") or 0):
            temporary.unlink(missing_ok=True)
            _update_job(
                job["job_id"],
                status="cancelled",
                stage="finished",
                finished_at=_now(),
            )
            return get_trim_job(job["job_id"]) or {}
        _update_job(
            job["job_id"],
            stage="verifying",
            progress_percent=70,
        )
        output_info = probe_video(temporary)
        if not output_info:
            raise VideoTrimError(
                "剪辑后的视频无法读取",
                "verification_failed",
            )
        expected_duration = max(
            0.0,
            float(job.get("source_duration") or before.get("duration") or 0)
            - cut_seconds,
        )
        output_duration = float(output_info.get("duration") or 0)
        tolerance = max(2.0, expected_duration * 0.005)
        if expected_duration and abs(output_duration - expected_duration) > tolerance:
            raise VideoTrimError(
                "剪辑后视频时长不符合预期",
                "verification_failed",
            )
        expected_dimensions = _frame_dimensions_at(source, cut_seconds)
        for field in ("width", "height"):
            expected_value = expected_dimensions.get(field)
            if (
                expected_value
                and output_info.get(field) != expected_value
            ):
                raise VideoTrimError(
                    "剪辑后{}发生变化".format(field),
                    "verification_failed",
                )
        temporary_stat = temporary.stat()
        _update_job(
            job["job_id"],
            output_size=temporary_stat.st_size,
            output_duration=output_duration,
            stage="committing",
            progress_percent=85,
        )
        lock_handle = acquire_library_write_lock()
        try:
            os.replace(source, backup)
            try:
                os.replace(temporary, source)
                _delete_preview_files(video)
                _reset_assets(
                    video,
                    output_info,
                    source.stat().st_size,
                    source.stat().st_mtime_ns,
                )
            except Exception:
                source.unlink(missing_ok=True)
                os.replace(backup, source)
                raise
        finally:
            release_library_write_lock(lock_handle)
        backup_stat = backup.stat()
        backup_sha256 = _sha256_file(backup)
        try:
            playback_cache_path(video).unlink(missing_ok=True)
        except OSError:
            pass
        _update_job(
            job["job_id"],
            status="success",
            stage="finished",
            progress_percent=100,
            backup_status="available",
            backup_size=backup_stat.st_size,
            backup_mtime_ns=backup_stat.st_mtime_ns,
            backup_sha256=backup_sha256,
            backup_created_at=_now(),
            finished_at=_now(),
        )
        logger.info(
            "Video trim complete job_id=%s video_id=%s mode=%s cut=%.3f",
            job["job_id"],
            job["video_id"],
            job["cut_mode"],
            cut_seconds,
            extra={"operation": "video.trim.complete"},
        )
        return get_trim_job(job["job_id"]) or {}
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        if backup.exists() and not source.exists():
            os.replace(backup, source)
        _update_job(
            job["job_id"],
            status="failed",
            stage=job.get("stage") or "trimming",
            error_code=getattr(exc, "code", "trim_failed"),
            error_detail=str(exc)[:1000],
            finished_at=_now(),
        )
        logger.exception(
            "Video trim failed job_id=%s video_id=%s",
            job["job_id"],
            job["video_id"],
            extra={"operation": "video.trim.failed"},
        )
        raise


def claim_trim_job() -> Optional[Dict]:
    now = _now()
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT * FROM video_trim_jobs
            WHERE status = 'pending'
            ORDER BY id ASC LIMIT 1
            """
        ).fetchone()
        if row is None:
            connection.commit()
            return None
        connection.execute(
            """
            UPDATE video_trim_jobs
            SET status = 'running', stage = 'preflight',
                progress_percent = 5,
                started_at = COALESCE(started_at, ?), updated_at = ?
            WHERE id = ?
            """,
            (now, now, int(row["id"])),
        )
        connection.commit()
    result = dict(row)
    result["status"] = "running"
    result["stage"] = "preflight"
    return result


def recover_interrupted_trim_jobs() -> int:
    recovered = 0
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM video_trim_jobs
            WHERE status IN ('running', 'committing')
            """
        ).fetchall()
        for row in rows:
            source = Path(row["source_path"])
            temporary = Path(row["temporary_path"]) if row["temporary_path"] else None
            backup = Path(row["backup_path"]) if row["backup_path"] else None
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            backup_status = "none"
            backup_size = None
            backup_mtime_ns = None
            if backup is not None and backup.exists():
                if (
                    not source.exists()
                    or not row["output_size"]
                    or source.stat().st_size != int(row["output_size"])
                ):
                    source.unlink(missing_ok=True)
                    os.replace(backup, source)
                else:
                    backup_stat = backup.stat()
                    backup_status = "available"
                    backup_size = backup_stat.st_size
                    backup_mtime_ns = backup_stat.st_mtime_ns
            connection.execute(
                """
                UPDATE video_trim_jobs
                SET status = 'failed', stage = 'finished',
                    error_code = 'service_interrupted',
                    error_detail = '服务重启时片头剪辑未完成',
                    backup_status = ?, backup_size = ?,
                    backup_mtime_ns = ?,
                    finished_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    backup_status,
                    backup_size,
                    backup_mtime_ns,
                    _now(),
                    _now(),
                    int(row["id"]),
                ),
            )
            recovered += 1
        connection.commit()
    return recovered


def reconcile_trim_backups() -> int:
    changed = 0
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM video_trim_jobs
            WHERE backup_path IS NOT NULL
              AND backup_status <> 'deleted'
            """
        ).fetchall()
        for row in rows:
            backup = Path(row["backup_path"])
            if not backup.is_file():
                status = "missing"
                connection.execute(
                    """
                    UPDATE video_trim_jobs
                    SET backup_status = ?, backup_size = NULL,
                        backup_mtime_ns = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (status, _now(), int(row["id"])),
                )
                changed += 1
                continue
            stat = backup.stat()
            sha256 = row["backup_sha256"]
            if not sha256:
                sha256 = _sha256_file(backup)
            connection.execute(
                """
                UPDATE video_trim_jobs
                SET backup_status = 'available', backup_size = ?,
                    backup_mtime_ns = ?, backup_sha256 = ?,
                    backup_created_at = COALESCE(backup_created_at, ?),
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    stat.st_size,
                    stat.st_mtime_ns,
                    sha256,
                    _now(),
                    _now(),
                    int(row["id"]),
                ),
            )
            changed += 1
        connection.commit()
    return changed


def delete_trim_backup(
    job_id: str,
    actor_user_id: Optional[int] = None,
    actor_username: Optional[str] = None,
) -> Dict:
    job = get_trim_job(job_id)
    if job is None:
        raise VideoTrimError("裁剪任务不存在", "job_not_found")
    if job.get("status") != "success":
        raise VideoTrimError(
            "只有成功任务的备份可以删除",
            "backup_unavailable",
        )
    if job.get("backup_status") != "available":
        raise VideoTrimError("安全备份不可用", "backup_unavailable")
    video = database.get_video_admin(int(job["video_id"]))
    if video is None:
        raise VideoTrimError("视频不存在", "video_not_found")
    source = _video_source(video)
    backup = _validate_backup_path(job, source)
    if not backup.is_file():
        _update_job(
            job_id,
            backup_status="missing",
            backup_size=None,
            backup_mtime_ns=None,
        )
        raise VideoTrimError("安全备份文件不存在", "backup_missing")
    try:
        backup.unlink()
    except OSError as exc:
        raise VideoTrimError(
            "无法删除安全备份: {}".format(exc),
            "backup_delete_failed",
        ) from exc
    _update_job(
        job_id,
        backup_status="deleted",
        backup_deleted_at=_now(),
    )
    LOGGER.info(
        "Video trim backup deleted job_id=%s video_id=%s actor=%s",
        job_id,
        job["video_id"],
        actor_username,
        extra={"operation": "video.trim.backup.delete"},
    )
    return get_trim_job(job_id) or {}


def restore_trim_backup(
    job_id: str,
    actor_user_id: Optional[int] = None,
    actor_username: Optional[str] = None,
) -> Dict:
    job = get_trim_job(job_id)
    if job is None:
        raise VideoTrimError("裁剪任务不存在", "job_not_found")
    if job.get("status") != "success":
        raise VideoTrimError(
            "只有成功任务的备份可以恢复",
            "backup_unavailable",
        )
    if job.get("backup_status") != "available":
        raise VideoTrimError("安全备份不可用", "backup_unavailable")
    video = database.get_video_admin(int(job["video_id"]))
    if video is None or video.get("is_deleted"):
        raise VideoTrimError("视频不存在或已删除", "video_not_found")
    source = _video_source(video)
    backup = _validate_backup_path(job, source)
    if not backup.is_file():
        _update_job(job_id, backup_status="missing")
        raise VideoTrimError("安全备份文件不存在", "backup_missing")
    expected_hash = str(job.get("backup_sha256") or "")
    actual_hash = _sha256_file(backup)
    if expected_hash and actual_hash != expected_hash:
        _update_job(job_id, backup_status="missing")
        raise VideoTrimError(
            "安全备份文件校验失败，可能已被修改",
            "backup_corrupt",
        )
    with database.connect() as connection:
        active = connection.execute(
            """
            SELECT
                EXISTS(
                    SELECT 1 FROM video_trim_jobs
                    WHERE video_id = ? AND job_id <> ?
                      AND status IN ('pending', 'running', 'committing')
                )
                OR EXISTS(
                    SELECT 1 FROM video_migrations
                    WHERE video_id = ?
                      AND status IN ('pending', 'running', 'needs_cleanup')
                )
                OR EXISTS(
                    SELECT 1 FROM download_tasks
                    WHERE video_id = ?
                      AND status IN (
                          'pending', 'running', 'pausing', 'paused', 'deleting'
                      )
                )
                OR EXISTS(
                    SELECT 1 FROM video_rename_operations
                    WHERE video_id = ?
                ) AS busy
            """,
            (
                int(video["id"]),
                job_id,
                int(video["id"]),
                int(video["id"]),
                int(video["id"]),
            ),
        ).fetchone()
    if active and int(active["busy"]):
        raise VideoTrimError(
            "视频正在执行其他操作，暂时不能恢复",
            "restore_busy",
        )
    backup_info = probe_video(backup)
    if not backup_info:
        raise VideoTrimError("安全备份无法读取", "backup_corrupt")
    temporary = source.with_name(
        ".{}.restore-{}.tmp{}".format(
            source.stem,
            uuid.uuid4().hex[:10],
            source.suffix,
        )
    )
    rollback = source.with_name(
        ".{}.restore-{}.rollback{}".format(
            source.stem,
            uuid.uuid4().hex[:10],
            source.suffix,
        )
    )
    lock_handle = None
    _update_job(
        job_id,
        restore_temporary_path=str(temporary),
        restore_rollback_path=str(rollback),
        restore_status="preparing",
    )
    try:
        try:
            os.link(backup, temporary)
        except OSError:
            shutil.copy2(backup, temporary)
        try:
            lock_handle = acquire_library_write_lock()
        except ScanAlreadyRunning as exc:
            raise VideoTrimError(
                "视频库正在扫描，请稍后重试",
                "restore_busy",
            ) from exc
        os.replace(source, rollback)
        try:
            os.replace(temporary, source)
            _delete_preview_files(video)
            source_stat = source.stat()
            _reset_assets(
                video,
                backup_info,
                source_stat.st_size,
                source_stat.st_mtime_ns,
            )
        except Exception:
            source.unlink(missing_ok=True)
            os.replace(rollback, source)
            raise
        rollback.unlink(missing_ok=True)
        for entry in database.list_playback_cache_entries(video_id=int(video["id"])):
            Path(entry["cache_path"]).unlink(missing_ok=True)
        _update_job(
            job_id,
            restore_status="success",
            restore_count=int(job.get("restore_count") or 0) + 1,
            last_restored_at=_now(),
            last_restored_by_user_id=actor_user_id,
            last_restored_by_username=actor_username,
            restore_temporary_path=None,
            restore_rollback_path=None,
        )
    except Exception:
        temporary.unlink(missing_ok=True)
        if rollback.exists() and not source.exists():
            os.replace(rollback, source)
        _update_job(
            job_id,
            restore_status="failed",
            restore_temporary_path=None,
            restore_rollback_path=None,
        )
        raise
    finally:
        if lock_handle is not None:
            release_library_write_lock(lock_handle)
    LOGGER.info(
        "Video trim backup restored job_id=%s video_id=%s actor=%s",
        job_id,
        video["id"],
        actor_username,
        extra={"operation": "video.trim.backup.restore"},
    )
    return get_trim_job(job_id) or {}


class VideoTrimWorker:
    def __init__(self, stop_event: threading.Event, logger=None) -> None:
        self.stop_event = stop_event
        self.logger = logger or LOGGER
        self.runner = JobRunner(
            name="video-trim-worker",
            stop_event=self.stop_event,
            claim_job=claim_trim_job,
            process_job=lambda job: JobResult.from_status(
                process_trim_job(
                    job,
                    logger=self.logger,
                ).get("status", "")
            ),
            poll_seconds=lambda: 1.0,
            logger=self.logger,
        )

    def start(self):
        self.runner.start()
        return self

    @property
    def thread(self):
        return self.runner.thread

    def stop(self) -> None:
        self.runner.stop(timeout=10)
