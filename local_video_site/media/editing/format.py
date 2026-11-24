from __future__ import annotations

import json
import logging
import os
import select
import shutil
import signal
import sqlite3
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from local_video_site.core import config
from local_video_site.storage import volumes as video_storage
from local_video_site import database
from local_video_site.media.ffmpeg import ffmpeg_slot
from local_video_site.jobs.runtime import ACTIVE_STATUSES as JOB_ACTIVE_STATUSES, JobResult, JobRunner, JobStateMachine
from local_video_site.media.info import probe_video
from local_video_site.media.playback.engine import mp4_is_fragmented, normalize_codec
from local_video_site.ingestion.library_scan import ScanAlreadyRunning, acquire_library_write_lock, release_library_write_lock
from local_video_site.core.search import parse_search_query


LOGGER = logging.getLogger(__name__)
STATUSES = {
    "pending",
    "running",
    "success",
    "failed",
    "cancelled",
    "needs_cleanup",
}
STATUS_LABELS = {
    "pending": "等待处理",
    "running": "处理中",
    "success": "处理成功",
    "failed": "处理失败",
    "cancelled": "已取消",
    "needs_cleanup": "等待清理旧文件",
}
STAGE_LABELS = {
    "waiting": "等待 Worker",
    "waiting_storage": "等待外盘连接",
    "preflight": "检查媒体与空间",
    "remuxing": "直接封装 MP4",
    "transcoding": "编码转译 MP4",
    "finalizing": "写入 MP4 索引",
    "verifying": "校验输出文件",
    "committing": "更新视频数据",
    "backing_up": "保留安全备份",
    "deleting_source": "清理原 MKV",
    "finished": "处理完成",
}
REMUX_VIDEO_CODECS = {"h264", "h265", "av1", "vp9"}
REMUX_AUDIO_CODECS = {"", "aac", "ac3", "eac3", "mp3", "opus", "flac"}
ACTIVE_OPERATION_STATUSES = set(JOB_ACTIVE_STATUSES)
OPERATION_CONTAINER_REMUX = "container_remux"
OPERATION_STANDARD_TRANSCODE = "standard_transcode"
OPERATIONS = {
    OPERATION_CONTAINER_REMUX,
    OPERATION_STANDARD_TRANSCODE,
}
OPERATION_LABELS = {
    OPERATION_CONTAINER_REMUX: "容器封装 · 无损",
    OPERATION_STANDARD_TRANSCODE: "编码转译 · 有损",
    "mkv_to_mp4_remux": "容器封装 · 无损",
}
REMUX_SOURCE_EXTENSIONS = {
    ".mkv",
    ".mp4",
    ".m4v",
    ".mov",
    ".avi",
    ".ts",
    ".mts",
    ".m2ts",
    ".flv",
    ".webm",
}
TRANSCODE_SOURCE_EXTENSIONS = {
    *REMUX_SOURCE_EXTENSIONS,
    ".rmvb",
    ".rm",
}
STANDARDIZE_SOURCE_EXTENSIONS = set(TRANSCODE_SOURCE_EXTENSIONS)
_EXPECTED_CONTAINERS = {
    ".mp4": "mp4",
    ".m4v": "mp4",
    ".mov": "mp4",
    ".mkv": "matroska",
    ".webm": "matroska",
    ".ts": "mpegts",
    ".mts": "mpegts",
    ".m2ts": "mpegts",
    ".avi": "avi",
    ".flv": "flv",
    ".rm": "realmedia",
    ".rmvb": "realmedia",
}


class VideoFormatError(RuntimeError):
    def __init__(self, message: str, code: str = "format_failed"):
        self.code = code
        super().__init__(message)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _new_batch_id(scan_id: str = "") -> str:
    if scan_id:
        return "fmt-scan-{}".format(scan_id)
    return "fmt-{}-{}".format(
        datetime.now().strftime("%Y%m%d-%H%M%S"),
        uuid.uuid4().hex[:8],
    )


def _new_job_id() -> str:
    return "fmt-{}-{}".format(
        datetime.now().strftime("%Y%m%d%H%M%S"),
        uuid.uuid4().hex[:10],
    )


def _codec(value) -> str:
    return normalize_codec(value)


def compatibility_class(
    video_codec: str,
    audio_codec: str,
) -> str:
    video_codec = _codec(video_codec)
    audio_codec = _codec(audio_codec)
    if video_codec not in REMUX_VIDEO_CODECS:
        return "unsupported"
    if audio_codec not in REMUX_AUDIO_CODECS:
        return "unsupported"
    if video_codec == "h264" and audio_codec in {"", "aac", "mp3"}:
        return "standard"
    return "conditional"


def standardization_preview(video: Dict) -> Dict:
    """Inspect one video and describe the least destructive MP4 repair."""

    video_id = int(video["id"])
    resolution = video_storage.resolve_video_storage(video)
    source = Path(resolution["path"])
    result = {
        "video_id": video_id,
        "eligible": False,
        "reason_code": "",
        "reason": "",
        "strategy": "",
        "lossy": False,
        "source_path": str(source),
        "source_size": int(video.get("size") or 0),
        "required_free_bytes": 0,
        "backup_required": False,
        "source": {},
        "target": {
            "container": "mp4",
            "video_codec": "h264",
            "audio_codec": "aac",
        },
        "active_job": None,
    }
    if resolution["state"] in {"external_offline", "file_missing"}:
        result.update({
            "reason_code": resolution["state"],
            "reason": (
                "外置存储未连接"
                if resolution["state"] == "external_offline"
                else "源视频文件不存在"
            ),
        })
        return result
    if not source.is_file():
        result.update({
            "reason_code": "file_missing",
            "reason": "源视频文件不存在",
        })
        return result

    extension = source.suffix.lower()
    if extension not in STANDARDIZE_SOURCE_EXTENSIONS:
        result.update({
            "reason_code": "unsupported_source",
            "reason": "当前文件格式不支持标准码转换",
        })
        return result

    info = probe_video(source)
    if not info or not info.get("video_codec"):
        result.update({
            "reason_code": "media_unreadable",
            "reason": "FFmpeg 无法读取有效的视频流",
        })
        return result
    container = str(
        info.get("container_format")
        or video.get("container_format")
        or ""
    ).lower()
    video_codec = _codec(info.get("video_codec"))
    audio_codec = _codec(info.get("audio_codec"))
    expected_container = _EXPECTED_CONTAINERS.get(extension)
    container_mismatch = bool(
        container
        and expected_container
        and container != expected_container
    )
    lossy = not (
        video_codec == "h264"
        and audio_codec in {"", "aac"}
        and not requires_transcode_from_info(info)
    )
    strategy = "transcode" if lossy else "remux"
    reason_code = (
        "container_mismatch"
        if container_mismatch
        else "codec_mismatch"
        if lossy
        else "standardize_requested"
    )
    reason = (
        "实际容器与扩展名不一致"
        if container_mismatch
        else "视频或音频编码需要转译"
        if lossy
        else "重新封装为兼容性更好的标准 MP4"
    )
    with database.connect() as connection:
        active = connection.execute(
            """
            SELECT id, job_id, status, stage, progress_percent
            FROM video_format_jobs
            WHERE video_id = ?
              AND status IN ('pending', 'running', 'needs_cleanup')
            ORDER BY id DESC LIMIT 1
            """,
            (video_id,),
        ).fetchone()
    result.update({
        "eligible": active is None,
        "reason_code": "job_active" if active else reason_code,
        "reason": "已有正在进行的格式任务" if active else reason,
        "strategy": strategy,
        "lossy": lossy,
        "source_size": source.stat().st_size,
        "required_free_bytes": (
            source.stat().st_size
            + config.video_format_disk_reserve_bytes()
        ),
        "backup_required": True,
        "source": {
            "container": container,
            "container_format_name": info.get("container_format_name"),
            "video_codec": video_codec,
            "audio_codec": audio_codec,
            "width": info.get("width"),
            "height": info.get("height"),
            "duration": info.get("duration"),
        },
        "target": {
            "container": "mp4",
            "video_codec": "h264",
            "audio_codec": "aac" if audio_codec else "",
        },
        "active_job": dict(active) if active else None,
    })
    return result


def requires_transcode_from_info(info: Dict) -> bool:
    video_codec = _codec(info.get("video_codec"))
    audio_codec = _codec(info.get("audio_codec"))
    return (
        video_codec.startswith(("rv", "real"))
        or audio_codec in {
            "cook",
            "ralf",
            "sipr",
            "real_144",
            "real_288",
        }
    )


def create_standardization_job(
    video_id: int,
    confirmed: bool = False,
    confirm_lossy: bool = False,
    expected_path: str = "",
    expected_mtime_ns: Optional[int] = None,
    created_by_user_id: Optional[int] = None,
    created_by_username: str = "",
) -> Dict:
    if not confirmed:
        raise VideoFormatError(
            "请先确认转标准码",
            "confirmation_required",
        )
    video = database.get_video_admin(int(video_id))
    if video is None or video.get("is_deleted"):
        raise VideoFormatError("视频不存在或已删除", "video_missing")
    resolution = video_storage.resolve_video_storage(video)
    source = Path(resolution["path"])
    if expected_path and source.resolve() != Path(expected_path).expanduser().resolve():
        raise VideoFormatError(
            "视频路径已发生变化，请刷新页面后重试",
            "source_changed",
        )
    if (
        expected_mtime_ns is not None
        and source.is_file()
        and source.stat().st_mtime_ns != int(expected_mtime_ns)
    ):
        raise VideoFormatError(
            "视频文件已发生变化，请刷新页面后重试",
            "source_changed",
        )
    preview = standardization_preview(video)
    active = preview.get("active_job") or {}
    if active:
        return {
            "created": 0,
            "existing_job_id": active.get("id"),
            "job_id": active.get("job_id"),
            "status": active.get("status"),
            "strategy": preview.get("strategy"),
        }
    if not preview["eligible"]:
        raise VideoFormatError(
            preview.get("reason") or "当前视频不能转标准码",
            str(preview.get("reason_code") or "standardize_unavailable"),
        )
    if preview["lossy"] and not confirm_lossy:
        raise VideoFormatError(
            "该视频需要重新编码，请确认画质和音质可能变化",
            "lossy_confirmation_required",
        )
    operation = (
        OPERATION_STANDARD_TRANSCODE
        if preview["lossy"]
        else OPERATION_CONTAINER_REMUX
    )
    result = create_jobs(
        [int(video_id)],
        operation=operation,
        confirmed=True,
        confirm_lossy=bool(confirm_lossy),
        created_by_user_id=created_by_user_id,
        created_by_username=created_by_username,
    )
    with database.connect() as connection:
        job = connection.execute(
            """
            SELECT id, job_id, status
            FROM video_format_jobs
            WHERE video_id = ?
              AND status IN ('pending', 'running', 'needs_cleanup')
            ORDER BY id DESC LIMIT 1
            """,
            (int(video_id),),
        ).fetchone()
    if job:
        result.update(dict(job))
    result["strategy"] = preview["strategy"]
    return result


def normalize_operation(value: str) -> str:
    operation = str(value or "").strip()
    if operation == "mkv_to_mp4_remux":
        return OPERATION_CONTAINER_REMUX
    if operation not in OPERATIONS:
        raise VideoFormatError("不支持的格式处理方式", "invalid_operation")
    return operation


def _formatting_temporarily_blocked(video_id: int) -> bool:
    checks = (
        (
            "SELECT 1 FROM video_migrations "
            "WHERE video_id = ? AND status IN "
            "('pending', 'running', 'needs_cleanup') LIMIT 1",
            (int(video_id),),
        ),
        (
            "SELECT 1 FROM video_rename_operations "
            "WHERE video_id = ? LIMIT 1",
            (int(video_id),),
        ),
        (
            "SELECT 1 FROM video_trim_jobs "
            "WHERE video_id = ? AND status IN "
            "('pending', 'running', 'committing') LIMIT 1",
            (int(video_id),),
        ),
        (
            "SELECT 1 FROM video_import_jobs "
            "WHERE video_id = ? AND status IN "
            "('pending', 'running', 'needs_cleanup') LIMIT 1",
            (int(video_id),),
        ),
    )
    with database.connect() as connection:
        return any(
            connection.execute(sql, params).fetchone() is not None
            for sql, params in checks
        )


def _job_paths(video: Dict) -> Dict:
    resolution = video_storage.resolve_video_storage(video)
    source = Path(resolution["path"])
    volume = resolution["volume"]
    if resolution["storage_kind"] == "external":
        if volume is None:
            raise VideoFormatError(
                "外置存储卷记录不可用",
                "storage_volume_missing",
            )
        source_root = video_storage.volume_root(volume)
        source_relative = str(video.get("storage_relative_path") or "")
    else:
        source_root = video_storage.local_root_for(source)
        if source_root is None:
            raise VideoFormatError(
                "视频不在受管理的本地目录中",
                "source_not_managed",
            )
        source_relative = str(source.relative_to(source_root))
    target = source.with_suffix(".mp4")
    target_relative = str(Path(source_relative).with_suffix(".mp4"))
    if not source_relative or Path(source_relative).is_absolute():
        raise VideoFormatError(
            "视频相对路径无效",
            "invalid_relative_path",
        )
    return {
        "source_path": source,
        "source_root": source_root,
        "source_relative_path": source_relative,
        "target_path": target,
        "target_root": source_root,
        "target_relative_path": target_relative,
        "source_storage_kind": resolution["storage_kind"],
        "source_volume_id": (
            int(video["storage_volume_id"])
            if video.get("storage_volume_id") is not None
            else None
        ),
        "target_storage_kind": resolution["storage_kind"],
        "target_volume_id": (
            int(video["storage_volume_id"])
            if video.get("storage_volume_id") is not None
            else None
        ),
        "storage_state": resolution["state"],
    }


def _prepare_job(
    video: Dict,
    batch_id: str,
    operation: str,
    confirmed_at: str,
    source_scan_id: str = "",
    source_scan_item_id: Optional[int] = None,
    created_by_user_id: Optional[int] = None,
    created_by_username: str = "",
) -> Optional[Dict]:
    if int(video.get("is_deleted") or 0):
        return None
    path = Path(str(video.get("path") or ""))
    extension = path.suffix.lower()
    allowed_extensions = (
        TRANSCODE_SOURCE_EXTENSIONS
        if operation == OPERATION_STANDARD_TRANSCODE
        else REMUX_SOURCE_EXTENSIONS
    )
    if extension not in allowed_extensions:
        return None
    try:
        paths = _job_paths(video)
    except (VideoFormatError, video_storage.StorageError):
        return None
    video_codec = _codec(video.get("video_codec"))
    audio_codec = _codec(video.get("audio_codec"))
    lossy = operation == OPERATION_STANDARD_TRANSCODE
    target_video_codec = "h264" if lossy else (video_codec or None)
    target_audio_codec = "aac" if lossy else (audio_codec or None)
    now = _now()
    return {
        "job_id": _new_job_id(),
        "batch_id": batch_id,
        "video_id": int(video["id"]),
        "source_scan_id": source_scan_id or None,
        "source_scan_item_id": source_scan_item_id,
        "operation": operation,
        "profile_key": "h264_aac_crf20" if lossy else "copy",
        "lossy": 1 if lossy else 0,
        "target_container": "mp4",
        "target_video_codec": target_video_codec,
        "target_audio_codec": target_audio_codec,
        "target_width": video.get("width"),
        "target_height": video.get("height"),
        "encoding_profile_json": json.dumps(
            {
                "profile_key": (
                    "h264_aac_crf20" if lossy else "copy"
                ),
                "video_codec": target_video_codec,
                "audio_codec": target_audio_codec,
                "container": "mp4",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "confirmation_version": 1,
        "confirmed_at": confirmed_at,
        "source_storage_kind": paths["source_storage_kind"],
        "source_volume_id": paths["source_volume_id"],
        "source_root": str(paths["source_root"]),
        "source_relative_path": paths["source_relative_path"],
        "source_path": str(paths["source_path"]),
        "source_extension": path.suffix.lower(),
        "source_size": int(video.get("size") or paths["source_path"].stat().st_size),
        "source_mtime_ns": int(
            video.get("mtime_ns")
            or paths["source_path"].stat().st_mtime_ns
        ),
        "source_duration": video.get("duration"),
        "source_width": video.get("width"),
        "source_height": video.get("height"),
        "source_video_codec": video_codec or None,
        "source_audio_codec": audio_codec or None,
        "source_container_format": video.get("container_format"),
        "target_storage_kind": paths["target_storage_kind"],
        "target_volume_id": paths["target_volume_id"],
        "target_root": str(paths["target_root"]),
        "target_relative_path": paths["target_relative_path"],
        "target_path": str(paths["target_path"]),
        "status": "pending",
        "stage": "waiting",
        "progress_percent": 0,
        "compatibility_class": (
            "standard"
            if lossy
            else compatibility_class(video_codec, audio_codec)
        ),
        "created_by_user_id": created_by_user_id,
        "created_by_username": created_by_username,
        "created_at": now,
        "updated_at": now,
    }


def create_jobs(
    video_ids: List[int],
    operation: str = OPERATION_CONTAINER_REMUX,
    confirmed: bool = True,
    confirm_lossy: bool = False,
    batch_id: str = "",
    source_scan_id: str = "",
    scan_item_ids: Optional[Dict[int, int]] = None,
    created_by_user_id: Optional[int] = None,
    created_by_username: str = "",
) -> Dict:
    operation = normalize_operation(operation)
    if not confirmed:
        raise VideoFormatError(
            "请先确认格式处理方式",
            "confirmation_required",
        )
    if (
        operation == OPERATION_STANDARD_TRANSCODE
        and not confirm_lossy
    ):
        raise VideoFormatError(
            "编码转译是有损操作，需要额外确认",
            "lossy_confirmation_required",
        )
    video_ids = sorted({int(value) for value in video_ids})
    if not video_ids:
        raise VideoFormatError("请选择至少一个视频", "videos_required")
    if len(video_ids) > config.video_format_batch_max():
        raise VideoFormatError(
            "单次最多加入 {} 个视频".format(config.video_format_batch_max()),
            "batch_too_large",
        )
    videos = database.get_videos_by_ids(video_ids)
    video_map = {int(video["id"]): video for video in videos}
    batch_id = batch_id or _new_batch_id(source_scan_id)
    prepared = []
    skipped = []
    for video_id in video_ids:
        video = video_map.get(video_id)
        if video is None:
            skipped.append({"video_id": video_id, "reason": "视频不存在"})
            continue
        extension = Path(str(video.get("path") or "")).suffix.lower()
        allowed_extensions = (
            TRANSCODE_SOURCE_EXTENSIONS
            if operation == OPERATION_STANDARD_TRANSCODE
            else REMUX_SOURCE_EXTENSIONS
        )
        if extension not in allowed_extensions:
            skipped.append({
                "video_id": video_id,
                "reason": (
                    "该格式不支持编码转译"
                    if operation == OPERATION_STANDARD_TRANSCODE
                    else "该格式不支持容器封装"
                ),
            })
            continue
        if _formatting_temporarily_blocked(video_id):
            skipped.append({
                "video_id": video_id,
                "reason": "存在其他活动文件任务",
            })
            continue
        job = _prepare_job(
            video,
            batch_id,
            operation=operation,
            confirmed_at=_now(),
            source_scan_id=source_scan_id,
            source_scan_item_id=(scan_item_ids or {}).get(video_id),
            created_by_user_id=created_by_user_id,
            created_by_username=created_by_username,
        )
        if job is None:
            skipped.append({
                "video_id": video_id,
                "reason": "无法确定受管理的视频路径",
            })
            continue
        prepared.append(job)
    if not prepared:
        raise VideoFormatError(
            "没有符合条件的视频",
            "no_format_candidates",
        )
    created = 0
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for item in prepared:
            try:
                connection.execute(
                    """
                    INSERT INTO video_format_jobs (
                        job_id, batch_id, video_id, source_scan_id,
                        source_scan_item_id, operation,
                        profile_key, lossy, target_container,
                        target_video_codec, target_audio_codec,
                        target_width, target_height, encoding_profile_json,
                        confirmation_version, confirmed_at,
                        source_storage_kind, source_volume_id, source_root,
                        source_relative_path, source_path, source_extension,
                        source_size, source_mtime_ns, source_duration,
                        source_width, source_height, source_video_codec,
                        source_audio_codec, source_container_format,
                        target_storage_kind,
                        target_volume_id, target_root,
                        target_relative_path, target_path, status, stage,
                        progress_percent, compatibility_class,
                        created_by_user_id, created_by_username,
                        created_at, updated_at
                    ) VALUES (
                        :job_id, :batch_id, :video_id, :source_scan_id,
                        :source_scan_item_id, :operation,
                        :profile_key, :lossy, :target_container,
                        :target_video_codec, :target_audio_codec,
                        :target_width, :target_height,
                        :encoding_profile_json, :confirmation_version,
                        :confirmed_at,
                        :source_storage_kind, :source_volume_id, :source_root,
                        :source_relative_path, :source_path, :source_extension,
                        :source_size, :source_mtime_ns, :source_duration,
                        :source_width, :source_height, :source_video_codec,
                        :source_audio_codec, :source_container_format,
                        :target_storage_kind,
                        :target_volume_id, :target_root,
                        :target_relative_path, :target_path, :status, :stage,
                        :progress_percent, :compatibility_class,
                        :created_by_user_id, :created_by_username,
                        :created_at, :updated_at
                    )
                    """,
                    item,
                )
                created += 1
            except sqlite3.IntegrityError:
                skipped.append({
                    "video_id": item["video_id"],
                    "reason": "已有活动格式任务",
                })
        connection.commit()
    result = {
        "batch_id": batch_id,
        "created": created,
        "skipped": skipped,
        "total_bytes": sum(item["source_size"] for item in prepared),
    }
    if created == 0 and len(video_ids) == 1:
        with database.connect() as connection:
            existing = connection.execute(
                """
                SELECT id, job_id, status FROM video_format_jobs
                WHERE video_id = ?
                  AND status IN ('pending', 'running', 'needs_cleanup')
                ORDER BY id DESC LIMIT 1
                """,
                (video_ids[0],),
            ).fetchone()
        if existing:
            result["existing_job_id"] = int(existing["id"])
            result["job_id"] = existing["job_id"]
            result["status"] = existing["status"]
    return result


def get_job(job_id: int) -> Optional[Dict]:
    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT j.*, v.title AS video_current_title,
                   v.path AS video_current_path,
                   v.storage_volume_id AS video_storage_volume_id
            FROM video_format_jobs j
            LEFT JOIN video_catalog v ON v.id = j.video_id
            WHERE j.id = ?
            """,
            (int(job_id),),
        ).fetchone()
    return _decorate_job(dict(row)) if row else None


def get_jobs_by_ids(job_ids: List[int]) -> List[Dict]:
    normalized = sorted({int(value) for value in job_ids})
    if not normalized:
        return []
    placeholders = ",".join("?" for _ in normalized)
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT j.*, v.title AS video_current_title,
                   v.path AS video_current_path
            FROM video_format_jobs j
            LEFT JOIN video_catalog v ON v.id = j.video_id
            WHERE j.id IN ({})
            """.format(placeholders),
            normalized,
        ).fetchall()
    return [_decorate_job(dict(row)) for row in rows]


def _decorate_job(item: Dict) -> Dict:
    item["status_label"] = STATUS_LABELS.get(
        item.get("status"),
        item.get("status"),
    )
    item["stage_label"] = STAGE_LABELS.get(
        item.get("stage"),
        item.get("stage"),
    )
    operation = normalize_operation(
        item.get("operation") or OPERATION_CONTAINER_REMUX
    )
    item["operation"] = operation
    item["operation_label"] = OPERATION_LABELS[operation]
    item["lossy"] = bool(
        int(item.get("lossy") or 0)
        or operation == OPERATION_STANDARD_TRANSCODE
    )
    return item


def list_jobs(
    query: str = "",
    status: str = "",
    page: int = 1,
    page_size: int = 30,
) -> Dict:
    clauses = ["1 = 1"]
    params: List = []
    parsed = parse_search_query(query)
    if parsed.is_id:
        if parsed.id_value is None:
            clauses.append("0 = 1")
        else:
            clauses.append("(j.id = ? OR j.video_id = ?)")
            params.extend([parsed.id_value, parsed.id_value])
    elif parsed.raw:
        escaped = (
            parsed.raw.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        clauses.append(
            """
            (
                j.job_id LIKE ? ESCAPE '\\'
                OR j.batch_id LIKE ? ESCAPE '\\'
                OR COALESCE(v.title, '') LIKE ? ESCAPE '\\'
                OR j.source_path LIKE ? ESCAPE '\\'
                OR j.target_path LIKE ? ESCAPE '\\'
            )
            """
        )
        like = "%{}%".format(escaped)
        params.extend([like, like, like, like, like])
    if status:
        clauses.append("j.status = ?")
        params.append(status)
    page = max(1, int(page))
    page_size = max(1, min(100, int(page_size)))
    with database.connect() as connection:
        total = connection.execute(
            """
            SELECT COUNT(*)
            FROM video_format_jobs j
            LEFT JOIN video_catalog v ON v.id = j.video_id
            WHERE {}
            """.format(" AND ".join(clauses)),
            params,
        ).fetchone()[0]
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        rows = connection.execute(
            """
            SELECT j.*, v.title AS video_current_title,
                   v.path AS video_current_path,
                   v.storage_volume_id AS video_storage_volume_id
            FROM video_format_jobs j
            LEFT JOIN video_catalog v ON v.id = j.video_id
            WHERE {}
            ORDER BY
                CASE j.status
                    WHEN 'running' THEN 0
                    WHEN 'pending' THEN 1
                    WHEN 'needs_cleanup' THEN 2
                    ELSE 3
                END,
                j.id DESC
            LIMIT ? OFFSET ?
            """.format(" AND ".join(clauses)),
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    return {
        "items": [_decorate_job(dict(row)) for row in rows],
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


def job_counts() -> Dict:
    result = {status: 0 for status in STATUSES}
    result["total"] = 0
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT status, COUNT(*) AS count "
            "FROM video_format_jobs GROUP BY status"
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


def claim_job() -> Optional[Dict]:
    now = _now()
    storage_cutoff = (
        datetime.now().astimezone() - timedelta(seconds=30)
    ).isoformat(timespec="seconds")
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT *
            FROM video_format_jobs
            WHERE status IN ('pending', 'needs_cleanup')
              AND (
                  status = 'needs_cleanup'
                  OR stage != 'waiting_storage'
                  OR updated_at <= ?
              )
            ORDER BY
                CASE status WHEN 'needs_cleanup' THEN 0 ELSE 1 END,
                CASE stage WHEN 'waiting_storage' THEN 1 ELSE 0 END,
                id ASC
            LIMIT 1
            """,
            (storage_cutoff,),
        ).fetchone()
        if row is None:
            connection.commit()
            return None
        stage = (
            "deleting_source"
            if row["status"] == "needs_cleanup"
            else "preflight"
        )
        connection.execute(
            """
            UPDATE video_format_jobs
            SET status = 'running', stage = ?,
                progress_percent = CASE
                    WHEN status = 'needs_cleanup' THEN 97
                    ELSE 5
                END,
                cancel_requested = 0, error_code = NULL,
                error_detail = NULL,
                attempt_count = attempt_count + 1,
                started_at = COALESCE(started_at, ?),
                finished_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (stage, now, now, int(row["id"])),
        )
        connection.commit()
    result = dict(row)
    result["status"] = "running"
    result["stage"] = stage
    result["attempt_count"] = int(result.get("attempt_count") or 0) + 1
    return result


_UPDATABLE_FIELDS = {
    "source_root",
    "source_relative_path",
    "source_path",
    "source_size",
    "source_mtime_ns",
    "source_duration",
    "source_width",
    "source_height",
    "source_video_codec",
    "source_audio_codec",
    "source_container_format",
    "target_root",
    "target_relative_path",
    "target_path",
    "temporary_path",
    "status",
    "stage",
    "progress_percent",
    "processed_seconds",
    "compatibility_class",
    "error_code",
    "error_detail",
    "cancel_requested",
    "output_size",
    "output_mtime_ns",
    "output_duration",
    "output_video_codec",
    "output_audio_codec",
    "result_json",
    "backup_path",
    "backup_size",
    "backup_mtime_ns",
    "backup_status",
    "backup_created_at",
    "backup_deleted_at",
    "restore_status",
    "last_restored_at",
    "db_committed_at",
    "source_deleted_at",
    "finished_at",
}


def update_job(job_id: int, **fields) -> None:
    values = {
        key: value
        for key, value in fields.items()
        if key in _UPDATABLE_FIELDS
    }
    if not values:
        return
    values["updated_at"] = _now()
    assignments = ", ".join("{} = ?".format(key) for key in values)
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            "SELECT status FROM video_format_jobs WHERE id = ?",
            (int(job_id),),
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
            "UPDATE video_format_jobs SET {} WHERE id = ?".format(
                assignments
            ),
            [*values.values(), int(job_id)],
        )


def _defer_for_storage(job_id: int, message: str) -> None:
    update_job(
        job_id,
        status="pending",
        stage="waiting_storage",
        progress_percent=0,
        error_code="storage_offline",
        error_detail=message,
    )


def _fail_job(
    job_id: int,
    code: str,
    detail: str,
) -> None:
    update_job(
        job_id,
        status="failed",
        stage="finished",
        error_code=code,
        error_detail=detail[:1000],
        finished_at=_now(),
    )


def request_cancel(job_id: int) -> bool:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT status FROM video_format_jobs WHERE id = ?",
            (int(job_id),),
        ).fetchone()
        if row is None or row["status"] not in {"pending", "running"}:
            return False
        if row["status"] == "pending":
            connection.execute(
                """
                UPDATE video_format_jobs
                SET status = 'cancelled', stage = 'finished',
                    finished_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (_now(), _now(), int(job_id)),
            )
        else:
            connection.execute(
                """
                UPDATE video_format_jobs
                SET cancel_requested = 1, updated_at = ?
                WHERE id = ?
                """,
                (_now(), int(job_id)),
            )
    return True


def retry_job(job_id: int) -> bool:
    with database.connect() as connection:
        cursor = connection.execute(
            """
            UPDATE video_format_jobs
            SET status = 'pending',
                stage = CASE
                    WHEN status = 'needs_cleanup' THEN 'deleting_source'
                    ELSE 'waiting'
                END,
                progress_percent = CASE
                    WHEN status = 'needs_cleanup' THEN 97
                    ELSE 0
                END,
                cancel_requested = 0, error_code = NULL,
                error_detail = NULL, finished_at = NULL,
                updated_at = ?
            WHERE id = ?
              AND status IN ('failed', 'cancelled', 'needs_cleanup')
            """,
            (_now(), int(job_id)),
        )
        return cursor.rowcount > 0


def recover_interrupted_jobs() -> int:
    now = _now()
    recovered = 0
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM video_format_jobs WHERE status = 'running'"
        ).fetchall()
        for row in rows:
            temporary = (
                Path(row["temporary_path"])
                if row["temporary_path"]
                else None
            )
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            video = connection.execute(
                "SELECT path FROM videos WHERE id = ?",
                (int(row["video_id"]),),
            ).fetchone()
            source = Path(row["source_path"])
            target = Path(row["target_path"])
            backup = Path(
                row["backup_path"]
                or str(_backup_path_for(dict(row)))
            )
            if video and str(video["path"]) == str(target):
                if source.exists() or backup.exists():
                    status = "needs_cleanup"
                    stage = "deleting_source"
                    progress = 97
                else:
                    status = "success"
                    stage = "finished"
                    progress = 100
            else:
                if target.exists() and source.exists():
                    target.unlink(missing_ok=True)
                status = "pending"
                stage = "waiting"
                progress = 0
            connection.execute(
                """
                UPDATE video_format_jobs
                SET status = ?, stage = ?, progress_percent = ?,
                    temporary_path = NULL, updated_at = ?
                WHERE id = ?
                """,
                (status, stage, progress, now, int(row["id"])),
            )
            recovered += 1
    return recovered


def _current_job(job_id: int) -> Dict:
    job = get_job(job_id)
    if job is None:
        raise VideoFormatError("格式处理任务不存在", "job_missing")
    return job


def _remove_playback_caches(video_id: int) -> None:
    for entry in database.list_playback_cache_entries(video_id=video_id):
        path = Path(entry["cache_path"])
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            continue
        database.delete_playback_cache_entry(path)


def _source_identity_matches(path: Path, size: int, mtime_ns: int) -> bool:
    try:
        stat = path.stat()
    except OSError:
        return False
    return stat.st_size == int(size) and stat.st_mtime_ns == int(mtime_ns)


def _assert_target_writable(target: Path, job_id: str) -> None:
    probe = target.parent / ".video-format-write-test-{}".format(job_id)
    try:
        with probe.open("xb"):
            pass
        probe.unlink()
    except OSError as exc:
        raise VideoFormatError(
            "目标目录不可写: {}".format(exc),
            "target_not_writable",
        ) from exc
    finally:
        probe.unlink(missing_ok=True)


def _backup_path_for(job: Dict) -> Path:
    source = Path(job["source_path"])
    return (
        source.parent
        / ".video-format-backups"
        / str(int(job["video_id"]))
        / "{}{}".format(job["job_id"], source.suffix)
    )


def _cleanup_committed_source(job: Dict) -> Dict:
    source = Path(job["source_path"])
    target = Path(job["target_path"])
    video = database.get_video_admin(int(job["video_id"]))
    if (
        video is None
        or Path(str(video["path"])).resolve() != target.resolve()
    ):
        raise VideoFormatError(
            "数据库尚未指向目标 MP4，不能完成清理",
            "database_not_committed",
        )
    if not target.is_file():
        raise VideoFormatError(
            "目标 MP4 不存在，不能完成清理",
            "target_missing",
        )
    operation = normalize_operation(
        job.get("operation") or OPERATION_CONTAINER_REMUX
    )
    if source.resolve() == target.resolve():
        backup = Path(
            str(job.get("backup_path") or _backup_path_for(job))
        )
        if not backup.is_file():
            raise VideoFormatError(
                "原地标准化后未找到安全备份",
                "backup_missing",
            )
        backup_stat = backup.stat()
        update_job(
            job["id"],
            status="success",
            stage="finished",
            progress_percent=100,
            source_deleted_at=_now(),
            error_code=None,
            error_detail=None,
            finished_at=_now(),
            backup_path=str(backup),
            backup_size=backup_stat.st_size,
            backup_mtime_ns=backup_stat.st_mtime_ns,
            backup_status="available",
            backup_created_at=job.get("backup_created_at") or _now(),
            backup_deleted_at=None,
        )
        return get_job(int(job["id"])) or {}
    backup_fields = {}
    if source.exists():
        if not _source_identity_matches(
            source,
            int(job["source_size"]),
            int(job["source_mtime_ns"]),
        ):
            raise VideoFormatError(
                "原视频已发生变化，拒绝自动删除",
                "source_changed",
            )
        if operation == OPERATION_STANDARD_TRANSCODE:
            backup = _backup_path_for(job)
            if backup.exists():
                raise VideoFormatError(
                    "安全备份目标已存在",
                    "backup_exists",
                )
            backup.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, backup)
            backup_stat = backup.stat()
            backup_fields = {
                "backup_path": str(backup),
                "backup_size": backup_stat.st_size,
                "backup_mtime_ns": backup_stat.st_mtime_ns,
                "backup_status": "available",
                "backup_created_at": _now(),
                "backup_deleted_at": None,
            }
        else:
            source.unlink()
    elif operation == OPERATION_STANDARD_TRANSCODE:
        backup = Path(str(job.get("backup_path") or _backup_path_for(job)))
        if not backup.is_file():
            raise VideoFormatError(
                "原始视频和安全备份均不存在",
                "backup_missing",
            )
        backup_fields = {
            "backup_path": str(backup),
            "backup_size": backup.stat().st_size,
            "backup_mtime_ns": backup.stat().st_mtime_ns,
            "backup_status": "available",
        }
    update_job(
        job["id"],
        status="success",
        stage="finished",
        progress_percent=100,
        source_deleted_at=_now(),
        error_code=None,
        error_detail=None,
        finished_at=_now(),
        **backup_fields,
    )
    return get_job(int(job["id"])) or {}


def _validated_backup_path(job: Dict) -> Path:
    expected = _backup_path_for(job).resolve()
    actual = Path(str(job.get("backup_path") or "")).resolve()
    if actual != expected or not actual.is_file():
        raise VideoFormatError(
            "安全备份不存在或路径无效",
            "backup_missing",
        )
    return actual


def delete_backup(job_id: int) -> bool:
    job = get_job(job_id)
    if (
        job is None
        or job.get("backup_status") != "available"
    ):
        return False
    backup = _validated_backup_path(job)
    backup.unlink()
    update_job(
        job_id,
        backup_status="deleted",
        backup_deleted_at=_now(),
        error_code=None,
        error_detail=None,
    )
    return True


def restore_backup(job_id: int) -> Dict:
    job = get_job(job_id)
    if (
        job is None
        or job.get("backup_status") != "available"
    ):
        raise VideoFormatError(
            "当前任务没有可恢复的安全备份",
            "backup_unavailable",
        )
    video = database.get_video_admin(int(job["video_id"]))
    if video is None or video.get("is_deleted"):
        raise VideoFormatError("视频记录不存在", "video_missing")
    source = Path(job["source_path"])
    target = Path(job["target_path"])
    if Path(str(video["path"])).resolve() != target.resolve():
        raise VideoFormatError(
            "当前视频已经不在编码转译目标路径",
            "video_path_changed",
        )
    backup = _validated_backup_path(job)
    if not _source_identity_matches(
        backup,
        int(job["source_size"]),
        int(job["source_mtime_ns"]),
    ):
        raise VideoFormatError(
            "安全备份已发生变化，不能自动恢复",
            "backup_changed",
        )
    same_path = source.resolve() == target.resolve()
    if source.exists() and not same_path:
        raise VideoFormatError(
            "原视频路径已存在其他文件",
            "source_exists",
        )
    rollback = target.with_name(
        ".{}.restore-{}.mp4".format(target.stem, job["job_id"])
    )
    rollback.unlink(missing_ok=True)
    lock_handle = acquire_library_write_lock()
    try:
        os.replace(target, rollback)
        try:
            os.replace(backup, source)
            source_stat = source.stat()
            database.update_video_after_normalization(
                video_id=int(job["video_id"]),
                size=source_stat.st_size,
                mtime_ns=source_stat.st_mtime_ns,
                info={
                    "duration": job.get("source_duration"),
                    "width": job.get("source_width"),
                    "height": job.get("source_height"),
                    "video_codec": job.get("source_video_codec"),
                    "audio_codec": job.get("source_audio_codec"),
                    "container_format": job.get("source_container_format"),
                },
                new_path=source,
                mark_normalized=False,
            )
        except Exception:
            source.unlink(missing_ok=True)
            os.replace(rollback, target)
            raise
        rollback.unlink(missing_ok=True)
        _remove_playback_caches(int(job["video_id"]))
        update_job(
            job_id,
            backup_status="restored",
            backup_path=None,
            restore_status="success",
            last_restored_at=_now(),
            error_code=None,
            error_detail=None,
        )
    finally:
        release_library_write_lock(lock_handle)
    return get_job(job_id) or {}


def _compatibility_error(video_codec: str, audio_codec: str) -> str:
    if _codec(video_codec) not in REMUX_VIDEO_CODECS:
        return "视频编码 {} 不能直接封装为 MP4".format(
            video_codec or "unknown"
        )
    if _codec(audio_codec) not in REMUX_AUDIO_CODECS:
        return "音频编码 {} 不能直接封装为 MP4".format(
            audio_codec or "unknown"
        )
    return "当前编码不支持直接封装"


def _validate_output(
    source_info: Dict,
    output_info: Dict,
    output_path: Path,
    operation: str = OPERATION_CONTAINER_REMUX,
) -> None:
    if not output_info:
        raise VideoFormatError("输出 MP4 无法读取", "verification_failed")
    if (
        output_info.get("container_format")
        and output_info["container_format"] != "mp4"
    ):
        raise VideoFormatError(
            "输出文件的真实容器不是 MP4",
            "verification_failed",
        )
    if mp4_is_fragmented(output_path):
        raise VideoFormatError(
            "输出文件仍然是分片 MP4",
            "verification_failed",
        )
    if operation == OPERATION_STANDARD_TRANSCODE:
        if _codec(output_info.get("video_codec")) != "h264":
            raise VideoFormatError(
                "编码转译后视频不是 H.264",
                "verification_failed",
            )
        if (
            _codec(source_info.get("audio_codec"))
            and _codec(output_info.get("audio_codec")) != "aac"
        ):
            raise VideoFormatError(
                "编码转译后音频不是 AAC",
                "verification_failed",
            )
        if (
            output_info.get("pix_fmt")
            and output_info["pix_fmt"] != "yuv420p"
        ):
            raise VideoFormatError(
                "编码转译后像素格式不是 yuv420p",
                "verification_failed",
            )
    else:
        for field in ("video_codec", "audio_codec"):
            source_codec = _codec(source_info.get(field))
            output_codec = _codec(output_info.get(field))
            if source_codec and output_codec and source_codec != output_codec:
                raise VideoFormatError(
                    "封装后 {} 发生变化".format(field),
                    "verification_failed",
                )
    for field in ("width", "height"):
        if (
            source_info.get(field)
            and output_info.get(field)
            and int(source_info[field]) != int(output_info[field])
        ):
            raise VideoFormatError(
                "封装后 {} 发生变化".format(field),
                "verification_failed",
            )
    source_duration = source_info.get("duration")
    output_duration = output_info.get("duration")
    if source_duration and output_duration:
        tolerance = max(1.0, float(source_duration) * 0.005)
        if abs(float(source_duration) - float(output_duration)) > tolerance:
            raise VideoFormatError(
                "封装后视频时长差异过大",
                "verification_failed",
            )


def _format_command(
    ffmpeg: str,
    source: Path,
    destination: Path,
    operation: str,
    source_info: Dict,
) -> List[str]:
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostats",
        "-progress",
        "pipe:1",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-map_metadata",
        "0",
        "-map_chapters",
        "0",
    ]
    if operation == OPERATION_STANDARD_TRANSCODE:
        transfer = str(source_info.get("color_transfer") or "").lower()
        primaries = str(source_info.get("color_primaries") or "").lower()
        is_hdr = (
            transfer in {"smpte2084", "arib-std-b67"}
            or primaries == "bt2020"
        )
        video_filter = (
            "zscale=t=linear:npl=100,format=gbrpf32le,"
            "zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
            "zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
            if is_hdr
            else "scale=trunc(iw/2)*2:trunc(ih/2)*2:flags=lanczos,"
                 "format=yuv420p"
        )
        command.extend([
            "-vf",
            video_filter,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-profile:v",
            "high",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "384k",
        ])
        channels = int(source_info.get("audio_channels") or 0)
        if channels > 2:
            command.extend(["-ac", str(min(6, channels))])
    else:
        command.extend(["-c", "copy"])
        if _codec(source_info.get("video_codec")) == "h265":
            command.extend(["-tag:v", "hvc1"])
    command.extend([
        "-movflags",
        "+faststart",
    ])
    command.extend([
        "-f",
        "mp4",
        "-y",
        str(destination),
    ])
    return command


def _parse_progress_seconds(line: str) -> Optional[float]:
    if not line.startswith("out_time="):
        return None
    value = line.split("=", 1)[1].strip()
    try:
        hours, minutes, seconds = value.split(":")
        result = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        return result if result >= 0 else None
    except (TypeError, ValueError):
        return None


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except OSError:
        process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            process.kill()


def _run_ffmpeg(
    job: Dict,
    command: List[str],
    stop_event: Optional[threading.Event] = None,
) -> None:
    stderr_path = Path(job["temporary_path"]).with_suffix(".ffmpeg.log")
    started = time.monotonic()
    duration = float(job.get("source_duration") or 0)
    process = None
    try:
        with ffmpeg_slot("video_format"):
            with stderr_path.open("wb") as stderr:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=stderr,
                    text=True,
                    bufsize=1,
                    start_new_session=True,
                )
                assert process.stdout is not None
                while process.poll() is None:
                    if stop_event is not None and stop_event.is_set():
                        _stop_process(process)
                        raise VideoFormatError(
                            "服务正在停止",
                            "service_stopping",
                        )
                    refreshed = _current_job(int(job["id"]))
                    if int(refreshed.get("cancel_requested") or 0):
                        _stop_process(process)
                        raise VideoFormatError("任务已取消", "cancelled")
                    if (
                        time.monotonic() - started
                        > config.video_format_timeout_seconds()
                    ):
                        _stop_process(process)
                        raise VideoFormatError(
                            "FFmpeg 处理超时",
                            "ffmpeg_timeout",
                        )
                    ready, _, _ = select.select(
                        [process.stdout],
                        [],
                        [],
                        0.5,
                    )
                    if not ready:
                        continue
                    line = process.stdout.readline()
                    if not line:
                        continue
                    seconds = _parse_progress_seconds(line)
                    if seconds is None:
                        continue
                    percent = (
                        min(84.0, 5.0 + (seconds / duration) * 79.0)
                        if duration > 0
                        else 30.0
                    )
                    update_job(
                        job["id"],
                        stage=(
                            "transcoding"
                            if normalize_operation(
                                job.get("operation")
                                or OPERATION_CONTAINER_REMUX
                            ) == OPERATION_STANDARD_TRANSCODE
                            else "remuxing"
                        ),
                        processed_seconds=round(seconds, 3),
                        progress_percent=round(percent, 1),
                    )
                returncode = process.wait()
                if returncode != 0:
                    detail = ""
                    try:
                        detail = stderr_path.read_text(
                            encoding="utf-8",
                            errors="replace",
                        )[-1000:]
                    except OSError:
                        pass
                    raise VideoFormatError(
                        "FFmpeg 格式处理失败{}".format(
                            ": {}".format(detail) if detail else ""
                        ),
                        "ffmpeg_failed",
                    )
    finally:
        if process is not None and process.poll() is None:
            _stop_process(process)
        stderr_path.unlink(missing_ok=True)


def _refresh_source_snapshot(
    job: Dict,
    source: Path,
    info: Dict,
    operation: str,
) -> str:
    source_stat = source.stat()
    compatibility = (
        "standard"
        if operation == OPERATION_STANDARD_TRANSCODE
        else compatibility_class(
            info.get("video_codec") or job.get("source_video_codec"),
            info.get("audio_codec") or job.get("source_audio_codec"),
        )
    )
    update_job(
        job["id"],
        source_path=str(source),
        source_size=source_stat.st_size,
        source_mtime_ns=source_stat.st_mtime_ns,
        source_duration=info.get("duration"),
        source_width=info.get("width"),
        source_height=info.get("height"),
        source_video_codec=_codec(info.get("video_codec")) or None,
        source_audio_codec=_codec(info.get("audio_codec")) or None,
        compatibility_class=compatibility,
    )
    job.update({
        "source_path": str(source),
        "source_size": source_stat.st_size,
        "source_mtime_ns": source_stat.st_mtime_ns,
        "source_duration": info.get("duration"),
        "source_width": info.get("width"),
        "source_height": info.get("height"),
        "source_video_codec": _codec(info.get("video_codec")) or None,
        "source_audio_codec": _codec(info.get("audio_codec")) or None,
        "compatibility_class": compatibility,
    })
    return compatibility


def process_job(
    job: Dict,
    logger: Optional[logging.Logger] = None,
    stop_event: Optional[threading.Event] = None,
) -> Dict:
    logger = logger or LOGGER
    job = _current_job(int(job["id"]))
    operation = normalize_operation(
        job.get("operation") or OPERATION_CONTAINER_REMUX
    )
    if job["status"] == "running" and int(job.get("cancel_requested") or 0):
        update_job(
            job["id"],
            status="cancelled",
            stage="finished",
            finished_at=_now(),
        )
        return _current_job(int(job["id"]))
    if job.get("db_committed_at"):
        return _cleanup_committed_source(job)

    video = database.get_video_admin(int(job["video_id"]))
    if video is None or video.get("is_deleted"):
        _fail_job(job["id"], "video_missing", "视频记录不存在或已删除")
        return _current_job(int(job["id"]))
    if _formatting_temporarily_blocked(int(job["video_id"])):
        update_job(
            job["id"],
            status="pending",
            stage="waiting",
            error_code="operation_conflict",
            error_detail="存在其他活动文件任务，稍后自动重试",
        )
        return _current_job(int(job["id"]))
    resolution = video_storage.resolve_video_storage(video)
    if resolution["state"] == "external_offline":
        _defer_for_storage(job["id"], "外置存储未连接")
        return _current_job(int(job["id"]))
    source = Path(resolution["path"])
    target = Path(job["target_path"])
    same_path = source.resolve() == target.resolve()
    if not source.is_file() and same_path:
        backup = Path(
            str(job.get("backup_path") or _backup_path_for(job))
        )
        if backup.is_file():
            if target.exists():
                target.unlink(missing_ok=True)
            os.replace(backup, source)
            update_job(
                job["id"],
                status="pending",
                stage="waiting",
                progress_percent=0,
                temporary_path=None,
                backup_path=None,
                backup_size=None,
                backup_mtime_ns=None,
                backup_status="none",
                backup_created_at=None,
                backup_deleted_at=None,
                error_code=None,
                error_detail=None,
            )
            return _current_job(int(job["id"]))
    if not source.is_file():
        _fail_job(job["id"], "source_missing", "源视频文件不存在")
        return _current_job(int(job["id"]))
    allowed_extensions = (
        TRANSCODE_SOURCE_EXTENSIONS
        if operation == OPERATION_STANDARD_TRANSCODE
        else REMUX_SOURCE_EXTENSIONS
    )
    if source.suffix.lower() not in allowed_extensions:
        _fail_job(job["id"], "source_changed", "源文件格式已发生变化")
        return _current_job(int(job["id"]))
    if target.exists() and not same_path:
        if Path(str(video["path"])) == target:
            update_job(
                job["id"],
                db_committed_at=job.get("db_committed_at") or _now(),
                progress_percent=97,
            )
            return _cleanup_committed_source(_current_job(int(job["id"])))
        _fail_job(
            job["id"],
            "target_exists",
            "目标 MP4 已存在，不能覆盖：{}".format(target),
        )
        return _current_job(int(job["id"]))

    lock_handle = None
    temporary = None
    db_committed = False
    try:
        lock_handle = acquire_library_write_lock()
    except ScanAlreadyRunning:
        update_job(
            job["id"],
            status="pending",
            stage="waiting",
            error_code="library_scan_running",
            error_detail="视频库扫描正在进行，稍后自动重试",
        )
        return _current_job(int(job["id"]))

    try:
        info = probe_video(source)
        compatibility = _refresh_source_snapshot(
            job,
            source,
            info,
            operation,
        )
        if (
            compatibility == "unsupported"
            and operation == OPERATION_CONTAINER_REMUX
        ):
            raise VideoFormatError(
                _compatibility_error(
                    info.get("video_codec"),
                    info.get("audio_codec"),
                ),
                "unsupported_codec",
            )
        if not _source_identity_matches(
            source,
            int(job["source_size"]),
            int(job["source_mtime_ns"]),
        ):
            raise VideoFormatError(
                "源文件在任务开始前发生变化",
                "source_changed",
            )
        _assert_target_writable(target, str(job["job_id"]))
        free_bytes = shutil.disk_usage(target.parent).free
        required = (
            int(job["source_size"])
            + config.video_format_disk_reserve_bytes()
        )
        if free_bytes < required:
            raise VideoFormatError(
                "磁盘可用空间不足，需要 {} 字节".format(required),
                "disk_space_low",
            )
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise VideoFormatError("ffmpeg is not installed", "ffmpeg_missing")
        temporary = target.with_name(
            ".{}.format-{}.part.mp4".format(
                target.stem,
                job["job_id"],
            )
        )
        temporary.unlink(missing_ok=True)
        update_job(
            job["id"],
            status="running",
            stage=(
                "transcoding"
                if operation == OPERATION_STANDARD_TRANSCODE
                else "remuxing"
            ),
            progress_percent=5,
            temporary_path=str(temporary),
        )
        job["temporary_path"] = str(temporary)
        command = _format_command(
            ffmpeg,
            source,
            temporary,
            operation,
            info,
        )
        _run_ffmpeg(job, command, stop_event=stop_event)
        if not temporary.is_file():
            raise VideoFormatError(
                "FFmpeg 未生成输出文件",
                "ffmpeg_failed",
            )
        update_job(
            job["id"],
            stage="finalizing",
            progress_percent=84,
        )
        refreshed = _current_job(int(job["id"]))
        if int(refreshed.get("cancel_requested") or 0):
            raise VideoFormatError("任务已取消", "cancelled")
        update_job(
            job["id"],
            stage="verifying",
            progress_percent=86,
        )
        output_info = probe_video(temporary)
        _validate_output(
            info,
            output_info,
            temporary,
            operation,
        )
        output_stat = temporary.stat()
        current_video = database.get_video_admin(int(job["video_id"]))
        if (
            current_video is None
            or Path(str(current_video["path"])).resolve()
            != source.resolve()
        ):
            raise VideoFormatError(
                "视频路径已发生变化",
                "source_changed",
            )
        if not _source_identity_matches(
            source,
            int(job["source_size"]),
            int(job["source_mtime_ns"]),
        ):
            raise VideoFormatError(
                "源文件在处理期间发生变化",
                "source_changed",
            )
        update_job(
            job["id"],
            stage="committing",
            progress_percent=90,
            output_size=output_stat.st_size,
            output_mtime_ns=output_stat.st_mtime_ns,
            output_duration=output_info.get("duration"),
            output_video_codec=_codec(output_info.get("video_codec")) or None,
            output_audio_codec=_codec(output_info.get("audio_codec")) or None,
        )
        backup_fields = {}
        backup = _backup_path_for(job)
        if same_path:
            if backup.exists():
                raise VideoFormatError(
                    "安全备份目标已存在",
                    "backup_exists",
                )
            backup.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, backup)
            backup_stat = backup.stat()
            backup_fields = {
                "backup_path": str(backup),
                "backup_size": backup_stat.st_size,
                "backup_mtime_ns": backup_stat.st_mtime_ns,
                "backup_status": "available",
                "backup_created_at": _now(),
                "backup_deleted_at": None,
            }
        try:
            os.replace(temporary, target)
        except Exception:
            if same_path and backup.exists() and not source.exists():
                os.replace(backup, source)
            raise
        try:
            database.update_video_after_normalization(
                video_id=int(job["video_id"]),
                size=target.stat().st_size,
                mtime_ns=target.stat().st_mtime_ns,
                info=output_info,
                new_path=target,
            )
        except Exception:
            target.unlink(missing_ok=True)
            if same_path and backup.exists() and not source.exists():
                os.replace(backup, source)
            raise
        db_committed = True
        _remove_playback_caches(int(job["video_id"]))
        update_job(
            job["id"],
            db_committed_at=_now(),
            temporary_path=None,
            stage=(
                "backing_up"
                if operation == OPERATION_STANDARD_TRANSCODE
                else "deleting_source"
            ),
            progress_percent=97,
            **backup_fields,
        )
        _cleanup_committed_source(_current_job(int(job["id"])))
    except VideoFormatError as exc:
        if db_committed:
            update_job(
                job["id"],
                status="needs_cleanup",
                stage="deleting_source",
                progress_percent=97,
                error_code=exc.code,
                error_detail=str(exc),
                finished_at=None,
            )
        elif exc.code in {"cancelled", "service_stopping"}:
            update_job(
                job["id"],
                status=(
                    "cancelled"
                    if exc.code == "cancelled"
                    else "pending"
                ),
                stage=(
                    "finished"
                    if exc.code == "cancelled"
                    else "waiting"
                ),
                error_code=None,
                error_detail=None,
                finished_at=(
                    _now()
                    if exc.code == "cancelled"
                    else None
                ),
            )
        else:
            _fail_job(job["id"], exc.code, str(exc))
    except Exception as exc:
        logger.exception(
            "Video format job raised an unexpected error job_id=%s",
            job["job_id"],
            extra={"operation": "video.format.failed"},
        )
        if db_committed:
            update_job(
                job["id"],
                status="needs_cleanup",
                stage="deleting_source",
                progress_percent=97,
                error_code="cleanup_failed",
                error_detail=str(exc)[:1000],
                finished_at=None,
            )
        else:
            _fail_job(job["id"], "format_failed", str(exc))
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if lock_handle is not None:
            release_library_write_lock(lock_handle)
    result = _current_job(int(job["id"]))
    logger.info(
        "Video format job finished job_id=%s video_id=%s status=%s",
        result["job_id"],
        result["video_id"],
        result["status"],
        extra={"operation": "video.format.finished"},
    )
    return result


class VideoFormatWorker:
    def __init__(
        self,
        stop_event: threading.Event,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.stop_event = stop_event
        self.logger = logger or LOGGER
        self.runner = JobRunner(
            name="video-format-worker",
            stop_event=self.stop_event,
            claim_job=claim_job,
            process_job=lambda job: JobResult.from_status(
                process_job(
                    job,
                    logger=self.logger,
                    stop_event=self.stop_event,
                ).get("status", "")
            ),
            poll_seconds=config.video_format_poll_seconds,
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
