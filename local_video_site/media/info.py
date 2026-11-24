from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Optional

from local_video_site.core import config


def available_ffprobe() -> Optional[str]:
    return shutil.which("ffprobe")


def probe_video(
    path: Path,
    timeout: Optional[float] = None,
) -> Dict:
    timeout = (
        config.probe_timeout_seconds()
        if timeout is None
        else timeout
    )
    ffprobe = available_ffprobe()
    if not ffprobe:
        return {}
    try:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                (
                    "format=duration,format_name,format_long_name:"
                    "stream=codec_type,codec_name,width,"
                    "height,duration,pix_fmt,color_range,color_space,"
                    "color_transfer,color_primaries,bits_per_raw_sample"
                ),
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if completed.returncode != 0:
        return {}
    try:
        payload = json.loads(completed.stdout)
    except (TypeError, ValueError):
        return {}

    streams = payload.get("streams") or []
    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"),
        {},
    )
    audio_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"),
        {},
    )
    format_info = payload.get("format") or {}
    duration = _number(format_info.get("duration"))
    if duration is None:
        duration = _number(video_stream.get("duration"))
    format_name = str(format_info.get("format_name") or "").lower()
    return {
        "duration": duration,
        "width": _integer(video_stream.get("width")),
        "height": _integer(video_stream.get("height")),
        "video_codec": video_stream.get("codec_name"),
        "audio_codec": audio_stream.get("codec_name"),
        "audio_channels": _integer(audio_stream.get("channels")),
        "audio_channel_layout": audio_stream.get("channel_layout"),
        "pix_fmt": video_stream.get("pix_fmt"),
        "color_range": video_stream.get("color_range"),
        "color_space": video_stream.get("color_space"),
        "color_transfer": video_stream.get("color_transfer"),
        "color_primaries": video_stream.get("color_primaries"),
        "bits_per_raw_sample": _integer(
            video_stream.get("bits_per_raw_sample")
        ),
        "container_format": container_family(format_name),
        "container_format_name": format_name,
    }


def container_family(format_name: str) -> str:
    value = str(format_name or "").strip().lower()
    names = {part.strip() for part in value.split(",") if part.strip()}
    if names & {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}:
        return "mp4"
    if "mpegts" in names:
        return "mpegts"
    if "matroska" in names or "webm" in names:
        return "matroska"
    if "avi" in names:
        return "avi"
    if "flv" in names:
        return "flv"
    if names & {"rm", "rmvb"}:
        return "realmedia"
    return value.split(",", 1)[0] if value else ""


def _number(value) -> Optional[float]:
    try:
        result = float(value)
        return result if result >= 0 else None
    except (TypeError, ValueError):
        return None


def _integer(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
