from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from local_video_site.core import config
from local_video_site.media.ffmpeg import ffmpeg_slot


def series_cover_path(
    series_id: int,
    source_kind: str,
    source_value: str,
    timestamp: float = 0,
) -> Path:
    token = "series-cover-v1:{}:{}:{}:{:.3f}".format(
        int(series_id),
        source_kind,
        source_value,
        float(timestamp),
    ).encode("utf-8")
    digest = hashlib.sha256(token).hexdigest()[:24]
    return config.SERIES_COVER_DIR / "{}.jpg".format(digest)


def season_cover_path(
    series_id: int,
    season_id: int,
    source_token: str,
    timestamp: float,
) -> Path:
    token = "season-cover-v1:{}:{}:{}:{:.3f}".format(
        int(series_id),
        int(season_id),
        source_token,
        float(timestamp),
    ).encode("utf-8")
    digest = hashlib.sha256(token).hexdigest()[:24]
    return config.SEASON_COVER_DIR / "{}.jpg".format(digest)


def _generate_series_cover(
    output_path: Path,
    source_path: Path,
    *,
    is_video: bool,
    timestamp: float = 0,
    timeout: Optional[float] = None,
) -> Optional[Path]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not source_path.is_file():
        return None
    config.ensure_data_directories()
    temporary = output_path.with_suffix(".tmp.jpg")
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    if is_video:
        command.extend(["-ss", "{:.3f}".format(max(0.0, timestamp))])
    command.extend([
        "-i",
        str(source_path),
        "-frames:v",
        "1",
        "-vf",
        (
            "scale=960:540:force_original_aspect_ratio=increase,"
            "crop=960:540"
        ),
        "-q:v",
        "3",
        "-y",
        str(temporary),
    ])
    timeout = (
        config.thumbnail_timeout_seconds()
        if timeout is None
        else timeout
    )
    try:
        with ffmpeg_slot(
            "series_cover",
            wait_seconds=config.ffmpeg_interactive_wait_seconds(),
        ):
            completed = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                check=False,
            )
    except (OSError, subprocess.SubprocessError):
        if temporary.exists():
            temporary.unlink()
        return None
    if completed.returncode != 0 or not temporary.is_file():
        if temporary.exists():
            temporary.unlink()
        return None
    os.replace(temporary, output_path)
    return output_path


def generate_series_cover_from_video(
    series_id: int,
    video_path: Path,
    video_id: int,
    timestamp: float,
    timeout: Optional[float] = None,
) -> Optional[Path]:
    try:
        source_version = "{}:{}:{}".format(
            int(video_id),
            video_path.stat().st_size,
            video_path.stat().st_mtime_ns,
        )
    except OSError:
        source_version = str(int(video_id))
    output = series_cover_path(
        series_id,
        "video",
        source_version,
        timestamp,
    )
    if output.is_file() and output.stat().st_size > 0:
        return output
    return _generate_series_cover(
        output,
        video_path,
        is_video=True,
        timestamp=timestamp,
        timeout=timeout,
    )


def generate_series_cover_from_image(
    series_id: int,
    image_path: Path,
    season_id: int,
    timeout: Optional[float] = None,
) -> Optional[Path]:
    try:
        source_version = "{}:{}:{}".format(
            int(season_id),
            image_path.stat().st_size,
            image_path.stat().st_mtime_ns,
        )
    except OSError:
        source_version = str(int(season_id))
    output = series_cover_path(
        series_id,
        "season",
        source_version,
    )
    if output.is_file() and output.stat().st_size > 0:
        return output
    return _generate_series_cover(
        output,
        image_path,
        is_video=False,
        timeout=timeout,
    )


def generate_season_cover_from_video(
    series_id: int,
    season_id: int,
    video_path: Path,
    video_id: int,
    timestamp: float,
    timeout: Optional[float] = None,
) -> Optional[Path]:
    try:
        source_version = "{}:{}:{}".format(
            int(video_id),
            video_path.stat().st_size,
            video_path.stat().st_mtime_ns,
        )
    except OSError:
        source_version = str(int(video_id))
    output = season_cover_path(
        series_id,
        season_id,
        source_version,
        timestamp,
    )
    if output.is_file() and output.stat().st_size > 0:
        return output
    return _generate_series_cover(
        output,
        video_path,
        is_video=True,
        timestamp=timestamp,
        timeout=timeout,
    )
