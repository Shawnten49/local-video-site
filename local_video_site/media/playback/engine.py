from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

from local_video_site.core import config
from local_video_site import database
from local_video_site.media.ffmpeg import ffmpeg_slot
from local_video_site.media.info import probe_video


_PREPARE_LOCKS: Dict[int, threading.Lock] = {}
_PREPARE_LOCKS_GUARD = threading.Lock()
PLAYBACK_CACHE_VERSION = 2
PLAYBACK_CACHE_DECISION_VERSION = 2
_DIRECT_VIDEO_CODECS = {"h264", "vp8", "vp9", "av1"}
_DIRECT_AUDIO_CODECS = {"", "aac", "mp3", "opus", "vorbis", "flac"}
_NORMALIZED_VIDEO_CODECS = {"h264", "h265", "av1", "vp9"}
_NORMALIZED_AUDIO_CODECS = {"", "aac", "ac3", "eac3", "mp3", "opus", "flac"}
_CONTAINER_FAMILIES = {
    ".mp4": {"mp4"},
    ".m4v": {"mp4"},
    ".mov": {"mp4"},
    ".webm": {"matroska"},
    ".mkv": {"matroska"},
    ".ts": {"mpegts"},
    ".mts": {"mpegts"},
    ".m2ts": {"mpegts"},
    ".avi": {"avi"},
    ".flv": {"flv"},
    ".rm": {"realmedia"},
    ".rmvb": {"realmedia"},
}


def normalize_codec(value) -> str:
    codec = str(value or "").strip().lower()
    return {
        "avc1": "h264",
        "avc3": "h264",
        "vp09": "vp9",
        "av01": "av1",
        "hev1": "h265",
        "hvc1": "h265",
        "hevc": "h265",
    }.get(codec, codec)


def mp4_is_fragmented(path: Path) -> bool:
    """Return whether the MP4 contains top-level movie fragments."""

    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            offset = 0
            for _ in range(512):
                if offset + 8 > size:
                    break
                handle.seek(offset)
                header = handle.read(8)
                if len(header) != 8:
                    break
                box_size = int.from_bytes(header[:4], "big")
                box_type = header[4:8]
                if box_type == b"moof":
                    return True
                if box_size == 1:
                    extended = handle.read(8)
                    if len(extended) != 8:
                        break
                    box_size = int.from_bytes(extended, "big")
                elif box_size == 0:
                    break
                if box_size < 8 or offset + box_size > size:
                    break
                offset += box_size
    except OSError:
        return False
    return False


def requires_playback_cache(video: dict) -> bool:
    path = Path(video["path"])
    extension = path.suffix.lower()
    codec = normalize_codec(video.get("video_codec"))
    audio_codec = normalize_codec(video.get("audio_codec"))
    container = str(video.get("container_format") or "").lower()
    expected_containers = _CONTAINER_FAMILIES.get(extension)
    if container and expected_containers and container not in expected_containers:
        return True
    if not codec:
        # An extension alone is not enough to justify duplicating the file.
        # The playback path probes metadata before making this decision.
        return extension not in {".mp4", ".m4v", ".mov", ".webm"}
    direct_audio = audio_codec in _DIRECT_AUDIO_CODECS
    if extension in (".mp4", ".m4v", ".mov"):
        # Cache preparation only remuxes the container with -c copy. It can
        # repair a fragmented MP4, but cannot make an unsupported video or
        # audio codec decodable. A regular MP4 must therefore play directly
        # even when the browser chooses to report HEVC/AC3 as unsupported.
        return path.is_file() and mp4_is_fragmented(path)
    if (
        video.get("normalization_status") == "ready"
        and codec in _NORMALIZED_VIDEO_CODECS
        and audio_codec in _NORMALIZED_AUDIO_CODECS
    ):
        # Container normalization intentionally preserves the source codecs.
        # Treat a successfully normalized MP4 as direct-play for deployments
        # whose clients can decode those codecs, instead of duplicating it
        # into a byte-for-byte equivalent playback cache.
        return False
    if extension == ".webm" and codec in ("vp8", "vp9", "av1"):
        return not direct_audio
    return True


def requires_transcode(video: dict) -> bool:
    path = Path(video["path"])
    extension = path.suffix.lower()
    video_codec = normalize_codec(video.get("video_codec"))
    audio_codec = normalize_codec(video.get("audio_codec"))
    return (
        extension in {".rmvb", ".rm"}
        or video_codec.startswith("rv")
        or video_codec.startswith("real")
        or audio_codec in {
            "cook",
            "ralf",
            "sipr",
            "real_144",
            "real_288",
        }
    )


def playback_preparation_mode(video: dict) -> str:
    if not requires_playback_cache(video):
        return "direct"
    if requires_transcode(video):
        return "unsupported"
    return "remux"


def playback_cache_reason(video: dict) -> str:
    extension = Path(video["path"]).suffix.lower()
    codec = normalize_codec(video.get("video_codec"))
    audio_codec = normalize_codec(video.get("audio_codec"))
    container = str(video.get("container_format") or "").lower()
    expected_containers = _CONTAINER_FAMILIES.get(extension)
    if container and expected_containers and container not in expected_containers:
        return "container_mismatch"
    if extension in {".mp4", ".m4v", ".mov"}:
        return "fragmented_mp4"
    if codec and codec not in _DIRECT_VIDEO_CODECS:
        return "unsupported_video_codec"
    if audio_codec not in _DIRECT_AUDIO_CODECS:
        return "unsupported_audio_codec"
    return "container_or_codec_requires_cache"


def ensure_playback_technical_info(
    video: dict,
    logger: Optional[logging.Logger] = None,
) -> dict:
    """Probe once when legacy records have no technical codec information."""

    if (
        normalize_codec(video.get("video_codec"))
        and str(video.get("container_format") or "").strip()
    ):
        remove_playback_cache_if_not_needed(video)
        return video
    path = Path(str(video.get("path") or ""))
    if not path.is_file():
        return video
    info = probe_video(path)
    if not info:
        return video
    database.update_video_technical_info(int(video["id"]), info)
    updated = database.get_video(int(video["id"])) or video
    updated = {**updated, "path": str(path)}
    logger = logger or logging.getLogger(__name__)
    logger.info(
        "Playback metadata probed video_id=%s video=%s audio=%s",
        video["id"],
        updated.get("video_codec"),
        updated.get("audio_codec"),
        extra={"operation": "playback.metadata.probed"},
    )
    remove_playback_cache_if_not_needed(updated)
    return updated


def remove_playback_cache_if_not_needed(
    video: dict,
) -> int:
    if not normalize_codec(video.get("video_codec")):
        return 0
    if requires_playback_cache(video):
        return 0
    removed = 0
    for entry in database.list_playback_cache_entries(
        video_id=int(video["id"])
    ):
        path = Path(entry["cache_path"])
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            continue
        database.delete_playback_cache_entry(path)
        removed += 1
    return removed


def _playback_command(
    ffmpeg: str,
    source: Path,
    destination: Path,
    mode: str,
) -> list:
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
        "-sn",
    ]
    if mode == "transcode":
        raise ValueError(
            "Playback cache no longer transcodes media streams"
        )
    else:
        command.extend(["-c", "copy"])
    command.extend([
        "-movflags",
        "+faststart",
        "-y",
        str(destination),
    ])
    return command


def playback_cache_path(video: dict) -> Path:
    token = "v{}:{}:{}:{}".format(
        PLAYBACK_CACHE_VERSION,
        video["path"],
        video["size"],
        video["mtime_ns"],
    ).encode("utf-8")
    digest = hashlib.sha256(token).hexdigest()[:28]
    return config.playback_cache_dir() / "{}.mp4".format(digest)


def prepared_playback_path(video: dict) -> Optional[Path]:
    path = playback_cache_path(video)
    if path.is_file() and path.stat().st_size > 0:
        return path
    return None


def _lock_for(video_id: int) -> threading.Lock:
    with _PREPARE_LOCKS_GUARD:
        return _PREPARE_LOCKS.setdefault(video_id, threading.Lock())


def _timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed


def playback_cache_stats() -> Dict:
    cache_dir = config.playback_cache_dir()
    files = list(cache_dir.glob("*.mp4"))
    total = sum(path.stat().st_size for path in files if path.is_file())
    entries = database.list_playback_cache_entries()
    return {
        "directory": str(cache_dir),
        "entry_count": len(entries),
        "file_count": len(files),
        "total_bytes": total,
        "max_bytes": config.playback_cache_max_bytes(),
        "max_age_days": config.playback_cache_max_age_days(),
    }


def touch_playback_cache(cache_path: Path) -> None:
    database.touch_playback_cache(cache_path)


def prune_playback_cache(
    max_bytes: Optional[int] = None,
    max_age_days: Optional[int] = None,
    dry_run: bool = False,
    logger: Optional[logging.Logger] = None,
) -> Dict:
    logger = logger or logging.getLogger(__name__)
    cache_dir = config.playback_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    max_bytes = (
        config.playback_cache_max_bytes()
        if max_bytes is None
        else max(0, int(max_bytes))
    )
    max_age_days = (
        config.playback_cache_max_age_days()
        if max_age_days is None
        else max(0, int(max_age_days))
    )
    now = datetime.now().astimezone()
    protect_after = now - timedelta(
        seconds=config.playback_cache_protect_seconds()
    )
    max_age_after = (
        now - timedelta(days=max_age_days)
        if max_age_days > 0
        else None
    )
    disk_files = {
        path.resolve(): path
        for path in cache_dir.glob("*.mp4")
        if path.is_file()
    }
    entries = database.list_playback_cache_entries()
    registered_paths = set()
    candidates = []
    protected = 0
    errors = []

    for entry in entries:
        path = Path(entry["cache_path"])
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        registered_paths.add(resolved)
        if not path.is_file():
            if not dry_run:
                database.delete_playback_cache_entry(path)
            continue
        video = (
            database.get_video(entry["video_id"])
            if entry.get("video_id") is not None
            else None
        )
        source_changed = bool(
            video
            and (
                int(video["size"]) != int(entry["source_size"])
                or int(video["mtime_ns"]) != int(entry["source_mtime_ns"])
                or str(video["path"]) != entry["source_path"]
            )
        )
        no_longer_needed = bool(
            video
            and normalize_codec(video.get("video_codec"))
            and not requires_playback_cache(video)
        )
        missing_source = video is None
        last_access = _timestamp(entry.get("last_accessed_at"))
        created = _timestamp(entry.get("created_at"))
        reference = last_access or created
        is_protected = bool(reference and reference >= protect_after)
        expired = bool(
            max_age_after is not None
            and created
            and created < max_age_after
        )
        candidates.append({
            "path": path,
            "size": path.stat().st_size,
            "created": created or now,
            "protected": is_protected,
            "reason": (
                "source_changed"
                if source_changed
                else "no_longer_needed"
                if no_longer_needed
                else "source_missing"
                if missing_source
                else "expired"
                if expired
                else "capacity"
            ),
            "database_entry": entry,
        })

    for resolved, path in disk_files.items():
        if resolved in registered_paths:
            continue
        try:
            created = datetime.fromtimestamp(
                path.stat().st_mtime,
                tz=now.tzinfo,
            )
        except OSError:
            continue
        is_protected = created >= protect_after
        candidates.append({
            "path": path,
            "size": path.stat().st_size,
            "created": created,
            "protected": is_protected,
            "reason": "orphan",
            "database_entry": None,
        })

    selected = []
    selected_paths = set()

    def select(item):
        key = str(item["path"])
        if key in selected_paths:
            return
        if item["protected"]:
            return
        selected_paths.add(key)
        selected.append(item)

    for item in candidates:
        if item["reason"] in {
            "source_changed",
            "no_longer_needed",
            "source_missing",
        }:
            select(item)
        elif item["reason"] == "orphan":
            select(item)
        elif item["reason"] == "expired":
            select(item)

    total_before = sum(path.stat().st_size for path in disk_files.values())
    retained = [
        item
        for item in candidates
        if item["path"].resolve() in disk_files
        and str(item["path"]) not in selected_paths
    ]
    total_after = sum(item["size"] for item in retained)
    if total_after > max_bytes:
        for item in sorted(
            (item for item in retained if item["reason"] == "capacity"),
            key=lambda value: (value["created"], str(value["path"])),
        ):
            if total_after <= max_bytes:
                break
            # The configured size is a hard budget. Capacity eviction follows
            # creation time and does not let the recent-access protection keep
            # the directory permanently above that budget.
            if item["protected"]:
                protected += 1
            item["protected"] = False
            select(item)
            total_after -= item["size"]

    removed_bytes = 0
    for item in selected:
        removed_bytes += item["size"]
        if dry_run:
            continue
        try:
            item["path"].unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            errors.append({
                "path": str(item["path"]),
                "error": str(exc),
            })
            removed_bytes -= item["size"]
            continue
        database.delete_playback_cache_entry(item["path"])
        logger.info(
            "Playback cache removed path=%s reason=%s size=%s",
            item["path"],
            item["reason"],
            item["size"],
            extra={"operation": "playback.cache.removed"},
        )

    return {
        "directory": str(cache_dir),
        "dry_run": bool(dry_run),
        "entries": len(entries),
        "files": len(disk_files),
        "max_bytes": max_bytes,
        "max_age_days": max_age_days,
        "total_bytes_before": total_before,
        "total_bytes_after": max(0, total_before - removed_bytes),
        "removed_files": len(selected),
        "removed_bytes": removed_bytes,
        "protected": protected,
        "over_limit": max(0, total_before - removed_bytes) > max_bytes,
        "removals": [
            {
                "path": str(item["path"]),
                "reason": item["reason"],
                "size": item["size"],
            }
            for item in selected
        ],
        "errors": errors,
    }


def ensure_playback_cache_capacity(required_bytes: int) -> None:
    maximum = config.playback_cache_max_bytes()
    required = max(0, int(required_bytes))
    if required > maximum:
        raise RuntimeError(
            "视频需要 {} 字节缓存，超过缓存上限 {} 字节".format(
                required,
                maximum,
            )
        )
    report = prune_playback_cache(max_bytes=max(0, maximum - required))
    if report["total_bytes_after"] + required > maximum:
        raise RuntimeError("播放缓存空间不足，且没有可安全清理的旧缓存")


def prepare_playback_cache(
    video: dict,
    logger: Optional[logging.Logger] = None,
    timeout: float = 1800.0,
) -> Path:
    logger = logger or logging.getLogger(__name__)
    if not requires_playback_cache(video):
        return Path(video["path"])
    mode = playback_preparation_mode(video)
    if mode == "unsupported":
        raise RuntimeError(
            "当前仅支持容器封装，系统不会自动转码视频或音频"
        )
    decision_reason = playback_cache_reason(video)
    existing = prepared_playback_path(video)
    if existing:
        database.register_playback_cache(
            existing,
            video,
            existing.stat().st_size,
            preparation_mode=mode,
            decision_reason=decision_reason,
            decision_version=PLAYBACK_CACHE_DECISION_VERSION,
        )
        return existing
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not installed")

    config.ensure_data_directories()
    cache_path = playback_cache_path(video)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_name(cache_path.stem + ".part.mp4")
    source = Path(video["path"])
    if not source.is_file():
        raise RuntimeError("source video not found")
    ensure_playback_cache_capacity(source.stat().st_size)

    with _lock_for(video["id"]):
        existing = prepared_playback_path(video)
        if existing:
            database.register_playback_cache(
                existing,
                video,
                existing.stat().st_size,
                preparation_mode=mode,
                decision_reason=decision_reason,
                decision_version=PLAYBACK_CACHE_DECISION_VERSION,
            )
            return existing
        logger.info(
            "Preparing browser playback cache: %s mode=%s",
            source.name,
            mode,
        )
        if temporary.exists():
            temporary.unlink()
        command = _playback_command(
            ffmpeg,
            source,
            temporary,
            mode,
        )
        try:
            with ffmpeg_slot(
                "playback_cache",
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
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("Could not prepare playback cache: {}".format(exc)) from exc
        if completed.returncode != 0 or not temporary.is_file():
            detail = (completed.stderr or "").strip()
            if temporary.exists():
                temporary.unlink()
            action = (
                "RMVB 兼容转码失败"
                if mode == "transcode"
                else "播放缓存准备失败"
            )
            raise RuntimeError(
                "{}{}".format(
                    action,
                    ": {}".format(detail) if detail else ""
                )
            )
        os.replace(temporary, cache_path)
        database.register_playback_cache(
            cache_path,
            video,
            cache_path.stat().st_size,
            preparation_mode=mode,
            decision_reason=decision_reason,
            decision_version=PLAYBACK_CACHE_DECISION_VERSION,
        )
        logger.info("Playback cache ready: %s", cache_path)
        return cache_path
