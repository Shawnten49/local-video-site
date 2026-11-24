from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from local_video_site.core import config
from local_video_site.media.ffmpeg import ffmpeg_slot


_LUMINANCE_RE = re.compile(r"lavfi\.signalstats\.YAVG=([0-9.]+)")


def available_ffmpeg() -> Optional[str]:
    return shutil.which("ffmpeg")


def thumbnail_cache_path(
    video_path: Path,
    size: int,
    mtime_ns: int,
) -> Path:
    token = "{}:{}:{}".format(video_path, size, mtime_ns).encode("utf-8")
    digest = hashlib.sha256(token).hexdigest()[:24]
    return config.THUMBNAIL_DIR / "{}.jpg".format(digest)


def custom_cover_cache_path(
    video_path: Path,
    size: int,
    mtime_ns: int,
    timestamp: float,
) -> Path:
    token = "cover-v1:{}:{}:{}:{:.3f}".format(
        video_path,
        size,
        mtime_ns,
        float(timestamp),
    ).encode("utf-8")
    digest = hashlib.sha256(token).hexdigest()[:24]
    return config.COVER_DIR / "{}.jpg".format(digest)


def generate_custom_cover(
    video_path: Path,
    size: int,
    mtime_ns: int,
    timestamp: float,
    timeout: Optional[float] = None,
) -> Optional[Path]:
    timeout = (
        config.thumbnail_timeout_seconds()
        if timeout is None
        else timeout
    )
    ffmpeg = available_ffmpeg()
    if not ffmpeg:
        return None
    config.ensure_data_directories()
    output_path = custom_cover_cache_path(
        video_path,
        size,
        mtime_ns,
        timestamp,
    )
    if output_path.is_file() and output_path.stat().st_size > 0:
        return output_path

    temporary = output_path.with_suffix(".tmp.jpg")
    if temporary.exists():
        temporary.unlink()
    try:
        with ffmpeg_slot("thumbnail"):
            completed = subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    "{:.3f}".format(float(timestamp)),
                    "-i",
                    str(video_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=480:-2",
                    "-q:v",
                    "3",
                    "-y",
                    str(temporary),
                ],
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


def _seek_candidates(duration: Optional[float]) -> List[float]:
    if not duration or duration <= 0:
        return [1.0]
    candidates = []
    for percentage in (0.10, 0.30, 0.50, 0.70):
        seek = min(
            max(duration * percentage, 0.5),
            max(duration - 0.5, 0.5),
        )
        rounded = round(seek, 3)
        if rounded not in candidates:
            candidates.append(rounded)
    return candidates


def _luminance(stdout: str) -> Optional[float]:
    values = [
        float(value)
        for value in _LUMINANCE_RE.findall(stdout or "")
    ]
    return max(values) if values else None


def generate_thumbnail(
    video_path: Path,
    size: int,
    mtime_ns: int,
    duration: Optional[float],
    timeout: Optional[float] = None,
    force: bool = False,
) -> Optional[Path]:
    timeout = (
        config.thumbnail_timeout_seconds()
        if timeout is None
        else timeout
    )
    ffmpeg = available_ffmpeg()
    if not ffmpeg:
        return None

    config.ensure_data_directories()
    output_path = thumbnail_cache_path(video_path, size, mtime_ns)
    if not force and output_path.is_file() and output_path.stat().st_size > 0:
        return output_path

    best: Optional[Tuple[Path, float]] = None
    for index, seek in enumerate(_seek_candidates(duration)):
        temporary = output_path.with_name(
            "{}.{}.tmp.jpg".format(output_path.stem, index)
        )
        if temporary.exists():
            temporary.unlink()

        try:
            with ffmpeg_slot("thumbnail"):
                completed = subprocess.run(
                    [
                        ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-ss",
                        "{:.3f}".format(seek),
                        "-i",
                        str(video_path),
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale=480:-2,signalstats,metadata=print:file=-",
                        "-q:v",
                        "4",
                        "-y",
                        str(temporary),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
        except (OSError, subprocess.SubprocessError):
            if temporary.exists():
                temporary.unlink()
            continue

        if completed.returncode != 0 or not temporary.is_file():
            if temporary.exists():
                temporary.unlink()
            continue

        luminance = _luminance(completed.stdout)
        if (
            luminance is None
            or luminance >= config.thumbnail_min_luminance()
        ):
            if best and best[0].exists():
                best[0].unlink()
            os.replace(temporary, output_path)
            return output_path
        if best is None or luminance > best[1]:
            if best and best[0].exists():
                best[0].unlink()
            best = (temporary, luminance)
        else:
            temporary.unlink()

    if best:
        os.replace(best[0], output_path)
        return output_path
    return None
