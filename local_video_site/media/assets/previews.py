from __future__ import annotations

import fcntl
import hashlib
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, Optional

from local_video_site.core import config
from local_video_site import database
from local_video_site.media.ffmpeg import ffmpeg_slot
from local_video_site.jobs.runtime import JobOutcome, JobResult, JobRunner
from local_video_site.media.info import probe_video


LOGGER = logging.getLogger(__name__)
_LUMINANCE_RE = re.compile(r"lavfi\.signalstats\.YAVG=([0-9.]+)")
_MOTION_RE = re.compile(r"lavfi\.signalstats\.YDIF=([0-9.]+)")
_SATURATION_RE = re.compile(r"lavfi\.signalstats\.SATAVG=([0-9.]+)")


def preview_file_path(video: Dict) -> Path:
    token = "preview-v{}:{}:{}:{}".format(
        database.PREVIEW_VERSION,
        video["path"],
        video["size"],
        video["mtime_ns"],
    ).encode("utf-8")
    digest = hashlib.sha256(token).hexdigest()[:24]
    return config.PREVIEW_DIR / "{}.mp4".format(digest)


def _candidate_starts(duration: float) -> list:
    if duration <= 10:
        return [0.0]
    starts = []
    for percentage in (0.15, 0.30, 0.50, 0.70):
        value = min(
            max(duration * percentage, 1.0),
            max(duration - config.preview_duration(), 0.5),
        )
        rounded = round(value, 3)
        if rounded not in starts:
            starts.append(rounded)
    return starts


def _luminance(stdout: str) -> Optional[float]:
    values = [
        float(value)
        for value in _LUMINANCE_RE.findall(stdout or "")
    ]
    return max(values) if values else None


def _motion_stats(stdout: str) -> tuple:
    motion = [float(value) for value in _MOTION_RE.findall(stdout or "")]
    saturation = [
        float(value)
        for value in _SATURATION_RE.findall(stdout or "")
    ]
    return (
        max(motion or [0.0]),
        max(saturation or [0.0]),
    )


def _preview_signal_stats(path: Path) -> tuple:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return 0.0, 0.0, 0.0
    try:
        with ffmpeg_slot("preview_stats"):
            completed = subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(path),
                    "-vf",
                    "fps=1,scale=64:-2,signalstats,metadata=print:file=-",
                    "-frames:v",
                    "8",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                timeout=config.preview_probe_timeout_seconds(),
                check=False,
            )
    except (OSError, subprocess.SubprocessError):
        return 0.0, 0.0, 0.0
    luminance = _luminance(completed.stdout) or 0.0
    motion, saturation = _motion_stats(completed.stdout)
    return luminance, motion, saturation


def generate_preview(
    video: Dict,
    logger: Optional[logging.Logger] = None,
    timeout: Optional[float] = None,
) -> Dict:
    timeout = (
        config.preview_generation_timeout_seconds()
        if timeout is None
        else timeout
    )
    logger = logger or LOGGER
    source = Path(video["path"])
    if not source.is_file():
        raise RuntimeError("源视频不存在")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not installed")
    config.ensure_data_directories()
    target = preview_file_path(video)
    if target.is_file() and target.stat().st_size > 0:
        return {
            "path": target,
            "start": float(video.get("preview_start") or 0),
            "duration": float(video.get("preview_duration") or 0),
        }
    duration = float(video.get("duration") or 0)
    if duration <= 0:
        raise RuntimeError("视频时长无效")
    clip_duration = min(config.preview_duration(), max(duration, 2.0))
    last_error = None
    best: Optional[tuple] = None
    for index, start in enumerate(_candidate_starts(duration)):
        temporary = config.PREVIEW_DIR / (
            ".preview-{}-{}-{}.tmp.mp4".format(
                video["id"],
                index,
                uuid.uuid4().hex[:8],
            )
        )
        width = config.preview_width()
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "{:.3f}".format(start),
            "-i",
            str(source),
            "-t",
            "{:.3f}".format(clip_duration),
            "-an",
            "-map",
            "0:v:0",
            "-vf",
            "scale='min({},iw)':-2,signalstats,"
            "metadata=print:file=-".format(width),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            str(config.preview_crf()),
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-y",
            str(temporary),
        ]
        try:
            with ffmpeg_slot("preview"):
                completed = subprocess.run(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
        except (OSError, subprocess.SubprocessError) as exc:
            temporary.unlink(missing_ok=True)
            last_error = str(exc)
            continue
        if (
            completed.returncode != 0
            or not temporary.is_file()
            or temporary.stat().st_size < 1024
        ):
            detail = (
                completed.stderr
                or completed.stdout
                or "FFmpeg preview generation failed"
            ).strip()
            last_error = detail
            temporary.unlink(missing_ok=True)
            continue
        luminance, motion, saturation = _preview_signal_stats(temporary)
        if luminance <= 0:
            luminance = _luminance(completed.stdout)
            motion, saturation = _motion_stats(completed.stdout)
        static_preview = motion < config.preview_min_motion()
        if (
            (
                luminance is None
                or luminance >= config.preview_min_luminance()
            )
            and not static_preview
        ):
            os.replace(temporary, target)
            return {
                "path": target,
                "start": start,
                "duration": clip_duration,
            }
        score = (
            (luminance if luminance is not None else 0.0)
            + motion * 8.0
            + saturation * 2.0
        )
        if best is None or score > best[0]:
            if best:
                best[1].unlink(missing_ok=True)
            best = (score, temporary, start)
        else:
            temporary.unlink(missing_ok=True)
    if best:
        _, temporary, start = best
        os.replace(temporary, target)
        return {
            "path": target,
            "start": start,
            "duration": clip_duration,
        }
    raise RuntimeError(last_error or "预览片段生成失败")


class PreviewWorker:
    def __init__(
        self,
        stop_event: threading.Event,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.stop_event = stop_event
        self.logger = logger or LOGGER
        self.lock_handle = None
        self.runner = JobRunner(
            name="video-preview-worker",
            stop_event=self.stop_event,
            claim_job=lambda: database.claim_preview_video(
                retry_failed=False
            ),
            process_job=self.process_claimed,
            poll_seconds=config.preview_poll_seconds,
            logger=self.logger,
        )

    def start(self) -> "PreviewWorker":
        if not config.preview_generation_enabled():
            self.logger.info("Preview worker disabled by configuration")
            return self
        config.ensure_data_directories()
        lock_path = config.DATA_DIR / "preview-worker.lock"
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
                "Preview worker not started because another worker holds the lock"
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

    def process_claimed(self, video: Dict) -> JobResult:
        try:
            result = generate_preview(video, logger=self.logger)
            output = result["path"]
            probe = probe_video(output)
            if not probe or probe.get("video_codec") != "h264":
                raise RuntimeError("预览文件编码验证失败")
            database.save_preview_result(
                video_id=video["id"],
                status="ready",
                preview_path=output.name,
                start=result["start"],
                duration=result["duration"],
                source_size=int(video["size"]),
                source_mtime_ns=int(video["mtime_ns"]),
            )
            self.logger.info(
                "Preview generated video_id=%s size=%s start=%.2f",
                video["id"],
                output.stat().st_size,
                result["start"],
                extra={"operation": "preview.generated"},
            )
            return JobResult(JobOutcome.SUCCESS)
        except Exception as exc:
            database.save_preview_result(
                video_id=video["id"],
                status="failed",
                source_size=int(video["size"]),
                source_mtime_ns=int(video["mtime_ns"]),
                error=str(exc)[:1000],
            )
            self.logger.exception(
                "Preview generation failed video_id=%s",
                video["id"],
                extra={"operation": "preview.failed"},
            )
            return JobResult(JobOutcome.FAILED, str(exc))


def generate_missing_previews(
    limit: Optional[int] = None,
    retry_failed: bool = False,
    logger: Optional[logging.Logger] = None,
) -> Dict:
    logger = logger or LOGGER
    database.recover_interrupted_previews()
    report = {
        "succeeded": 0,
        "failed": 0,
        "errors": [],
    }
    processor = PreviewWorker(threading.Event(), logger=logger)
    while limit is None or report["succeeded"] + report["failed"] < limit:
        video = database.claim_preview_video(retry_failed=retry_failed)
        if video is None:
            break
        before_failed = report["failed"]
        processor.process_claimed(video)
        updated = database.get_video(video["id"])
        if updated and updated.get("preview_status") == "ready":
            report["succeeded"] += 1
        else:
            report["failed"] += 1
            report["errors"].append({
                "video_id": video["id"],
                "error": (updated or {}).get("preview_error"),
            })
        if before_failed == report["failed"] and retry_failed:
            continue
    return report
