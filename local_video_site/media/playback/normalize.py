from __future__ import annotations

import fcntl
import logging
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from local_video_site.core import config
from local_video_site import database
from local_video_site.media.ffmpeg import ffmpeg_slot
from local_video_site.media.info import probe_video
from local_video_site.media.playback.engine import mp4_is_fragmented, playback_cache_path


LOGGER = logging.getLogger(__name__)
SUPPORTED_VIDEO_CODECS = {"h264", "av1", "vp8", "vp9"}
SUPPORTED_AUDIO_CODECS = {"aac", "mp3", "opus"}
TRANSPORT_STREAM_SUFFIXES = {".ts", ".mts", ".m2ts"}


class NormalizationError(RuntimeError):
    pass


def _mp4_family(video: Dict) -> bool:
    return Path(video["path"]).suffix.lower() in {
        ".mp4",
        ".m4v",
        ".mov",
    }


def needs_in_place_normalization(
    video: Dict,
    info: Optional[Dict] = None,
) -> bool:
    path = Path(video["path"])
    if not path.is_file():
        return False
    info = info or {}
    codec = str(
        info.get("video_codec")
        or video.get("video_codec")
        or ""
    ).lower()
    if path.suffix.lower() in TRANSPORT_STREAM_SUFFIXES:
        return not codec or codec in SUPPORTED_VIDEO_CODECS
    if not _mp4_family(video) or not mp4_is_fragmented(path):
        return False
    return not codec or codec in SUPPORTED_VIDEO_CODECS


def _validate_normalized(
    source_info: Dict,
    output_info: Dict,
    output_path: Path,
    source_path: Path,
) -> None:
    if not output_info:
        raise NormalizationError("输出文件无法被 ffprobe 读取")
    if mp4_is_fragmented(output_path):
        raise NormalizationError("输出文件仍然是分片 MP4")
    if (
        source_info.get("video_codec")
        and output_info.get("video_codec")
        and source_info["video_codec"] != output_info["video_codec"]
    ):
        raise NormalizationError("重封装后视频编码发生变化")
    if (
        source_info.get("audio_codec")
        and output_info.get("audio_codec")
        and source_info["audio_codec"] != output_info["audio_codec"]
    ):
        raise NormalizationError("重封装后音频编码发生变化")
    for field in ("width", "height"):
        if (
            source_info.get(field)
            and output_info.get(field)
            and source_info[field] != output_info[field]
        ):
            raise NormalizationError("重封装后 {} 发生变化".format(field))
    source_duration = source_info.get("duration")
    output_duration = output_info.get("duration")
    if source_duration and output_duration:
        source_duration = float(source_duration)
        output_duration = float(output_duration)
        tolerance = max(1.0, source_duration * 0.005)
        if source_path.suffix.lower() in TRANSPORT_STREAM_SUFFIXES:
            # MPEG-TS can carry timestamp gaps that ffprobe's aggregate
            # duration does not include. A stream-copy MP4 may therefore be
            # longer without losing frames. Reject truncation strictly, but
            # allow a bounded extension for discontinuities.
            allowed_extension = max(300.0, source_duration * 0.05)
            duration_invalid = (
                output_duration < source_duration - tolerance
                or output_duration > source_duration + allowed_extension
            )
        else:
            duration_invalid = (
                abs(source_duration - output_duration) > tolerance
            )
        if duration_invalid:
            raise NormalizationError("重封装后视频时长差异过大")


def _remove_playback_cache(video: Dict) -> None:
    paths = {
        playback_cache_path(video),
        *(
            Path(entry["cache_path"])
            for entry in database.list_playback_cache_entries(
                video_id=int(video["id"])
            )
        ),
    }
    for path in paths:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            continue
        database.delete_playback_cache_entry(path)


def normalize_video(
    video: Dict,
    logger: Optional[logging.Logger] = None,
    timeout: float = 7200.0,
) -> Dict:
    logger = logger or LOGGER
    source = Path(video["path"])
    if not source.is_file():
        raise NormalizationError("源视频不存在")
    before = probe_video(source)
    if not needs_in_place_normalization(video, before):
        return {
            "status": "skipped",
            "reason": "不需要重封装",
            "video_id": video["id"],
        }
    codec = str(before.get("video_codec") or "").lower()
    audio_codec = str(before.get("audio_codec") or "").lower()
    if codec not in SUPPORTED_VIDEO_CODECS:
        raise NormalizationError(
            "视频编码 {} 不能无损重封装为浏览器兼容 MP4".format(
                codec or "unknown"
            )
        )
    if audio_codec and audio_codec not in SUPPORTED_AUDIO_CODECS:
        raise NormalizationError(
            "音频编码 {} 不能无损重封装为浏览器兼容 MP4".format(
                audio_codec
            )
        )
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise NormalizationError("ffmpeg is not installed")
    source_stat = source.stat()
    transport_stream = source.suffix.lower() in TRANSPORT_STREAM_SUFFIXES
    target = (
        source.with_suffix(".mp4")
        if transport_stream
        else source
    )
    if target != source and target.exists():
        raise NormalizationError(
            "目标 MP4 已经存在，不能覆盖"
        )
    free_bytes = shutil.disk_usage(source.parent).free
    reserve = config.playback_remux_disk_reserve_bytes()
    if free_bytes < source_stat.st_size + reserve:
        raise NormalizationError(
            "磁盘可用空间不足，需要额外 {} 字节加安全余量".format(
                source_stat.st_size
            )
        )

    temporary = target.with_name(
        ".{}.{}.normalize.tmp.mp4".format(
            target.stem,
            uuid.uuid4().hex[:10],
        )
    )
    database.update_normalization_status(video["id"], "processing")
    logger.info(
        "Playback normalization started video_id=%s size=%s codec=%s",
        video["id"],
        source_stat.st_size,
        codec,
        extra={"operation": "playback.normalize.start"},
    )
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        "-y",
        str(temporary),
    ]
    started = time.monotonic()
    try:
        with ffmpeg_slot("playback_normalize"):
            completed = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                check=False,
            )
        if completed.returncode != 0 or not temporary.is_file():
            detail = (completed.stderr or "").strip()
            raise NormalizationError(
                "FFmpeg 重封装失败{}".format(
                    ": {}".format(detail[-1000:]) if detail else ""
                )
            )
        output_info = probe_video(temporary)
        _validate_normalized(before, output_info, temporary, source)
        output_stat = temporary.stat()
        _remove_playback_cache(video)
        os.replace(temporary, target)
        new_stat = target.stat()
        try:
            database.update_video_after_normalization(
                video_id=video["id"],
                size=new_stat.st_size,
                mtime_ns=new_stat.st_mtime_ns,
                info=output_info,
                new_path=target,
            )
        except Exception:
            if target != source:
                target.unlink(missing_ok=True)
            raise
        cleanup_warning = ""
        if target != source:
            try:
                source.unlink()
            except OSError as exc:
                cleanup_warning = str(exc)
                logger.warning(
                    "Normalized source cleanup deferred video_id=%s "
                    "source=%s error=%s",
                    video["id"],
                    source,
                    exc,
                )
    except Exception as exc:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        database.update_normalization_status(
            video["id"],
            "failed",
            str(exc)[:1000],
        )
        logger.exception(
            "Playback normalization failed video_id=%s",
            video["id"],
            extra={"operation": "playback.normalize.failed"},
        )
        raise

    elapsed = time.monotonic() - started
    logger.info(
        "Playback normalization complete video_id=%s elapsed=%.2fs "
        "before=%s after=%s",
        video["id"],
        elapsed,
        source_stat.st_size,
        new_stat.st_size,
        extra={"operation": "playback.normalize.complete"},
    )
    return {
        "status": "ready",
        "video_id": video["id"],
        "path": str(target),
        "source_path": str(source),
        "before_bytes": source_stat.st_size,
        "after_bytes": new_stat.st_size,
        "elapsed_seconds": round(elapsed, 3),
        "cleanup_warning": cleanup_warning,
    }


def normalize_playback_videos(
    video_id: Optional[int] = None,
    limit: Optional[int] = None,
    retry_failed: bool = False,
    dry_run: bool = False,
    logger: Optional[logging.Logger] = None,
) -> Dict:
    logger = logger or LOGGER
    videos = database.active_videos()
    if video_id is not None:
        videos = [video for video in videos if video["id"] == int(video_id)]
    candidates = []
    skipped = 0
    for video in videos:
        if (
            video.get("normalization_status") == "failed"
            and not retry_failed
        ):
            skipped += 1
            continue
        if needs_in_place_normalization(video):
            candidates.append(video)
        else:
            skipped += 1
        if limit is not None and len(candidates) >= max(0, int(limit)):
            break

    report = {
        "scanned": len(videos),
        "candidates": len(candidates),
        "succeeded": 0,
        "failed": 0,
        "skipped": skipped,
        "dry_run": bool(dry_run),
        "before_bytes": sum(int(video["size"]) for video in candidates),
        "after_bytes": 0,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "errors": [],
    }
    if dry_run:
        report["items"] = [
            {
                "video_id": video["id"],
                "path": video["path"],
                "size": video["size"],
                "codec": video.get("video_codec"),
            }
            for video in candidates
        ]
        return report

    config.ensure_data_directories()
    lock_path = config.DATA_DIR / "playback-normalize.lock"
    with lock_path.open("a+") as lock_handle:
        try:
            fcntl.flock(
                lock_handle.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError as exc:
            raise NormalizationError(
                "另一个播放标准化任务正在运行"
            ) from exc
        for index, video in enumerate(candidates, start=1):
            logger.info(
                "Playback normalization progress %s/%s video_id=%s",
                index,
                len(candidates),
                video["id"],
                extra={"operation": "playback.normalize.progress"},
            )
            try:
                result = normalize_video(video, logger=logger)
                if result["status"] == "ready":
                    report["succeeded"] += 1
                    report["after_bytes"] += result["after_bytes"]
                else:
                    report["skipped"] += 1
            except Exception as exc:
                report["failed"] += 1
                report["errors"].append({
                    "video_id": video["id"],
                    "path": video["path"],
                    "error": str(exc),
                })
    report["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return report
