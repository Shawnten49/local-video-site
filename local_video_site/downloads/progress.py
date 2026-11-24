from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import selectors
import signal
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from local_video_site.core import config
from local_video_site import database
from local_video_site.identity.permissions.content import validate_access_level
from local_video_site.catalog.categories import rebuild_actor_index, rebuild_category_index
from local_video_site.storage.deletion import delete_video_authorized
from local_video_site.media.info import probe_video
from local_video_site.downloads.policies.media import policy_for_task
from local_video_site.media.quality import normalise_quality_label, resolution_label
from local_video_site.integrations.metadata import fetch_site_metadata, metadata_parser_version, metadata_provider_for_url
from local_video_site.media.assets.thumbnails import generate_thumbnail
from local_video_site.downloads.policies.transport import ensure_site_for_url, learn_transport_mode, record_qualification, record_ytdlp_fallback, record_task_result, record_task_created, resolve_transport_for_task
from local_video_site.downloader.errors import permanent_download_error_message
from local_video_site.downloader.sniffer import redact_url


LOGGER = logging.getLogger(__name__)
PROGRESS_RE = re.compile(r"Progress\s+([0-9]+(?:\.[0-9]+)?)%")
MEDIA_SELECTED_RE = re.compile(
    r"Media selected:\s+quality=(?P<quality>\S+)\s+"
    r"width=(?P<width>[0-9]+|unknown)\s+"
    r"height=(?P<height>[0-9]+|unknown)",
    flags=re.IGNORECASE,
)
MEDIA_RECOVERY_STATE_RE = re.compile(
    r"Media recovery state:\s+expires_at=(?P<expires>\S+)\s+"
    r"identity=(?P<identity>[0-9a-f]+)",
    flags=re.IGNORECASE,
)
MEDIA_REFRESH_START_RE = re.compile(
    r"Media URL refresh start reason=.*?"
    r"refresh=(?P<count>[0-9]+)/(?P<limit>[0-9]+)",
    flags=re.IGNORECASE,
)
MEDIA_REFRESH_SUCCESS_RE = re.compile(
    r"Media URL refresh success expires_at=(?P<expires>\S+)\s+"
    r"partial_bytes=(?P<partial>[0-9]+)",
    flags=re.IGNORECASE,
)
MEDIA_RANGE_VALIDATION_RE = re.compile(
    r"Media resume Range validation: status=(?P<status>[0-9]+)"
    r".*?content_range=(?P<range>.+)$",
    flags=re.IGNORECASE,
)
DIRECT_RANGE_INIT_RE = re.compile(
    r"Direct range initialized connections=(?P<connections>[0-9]+).*?"
    r"completed_chunks=(?P<completed>[0-9]+)\s+"
    r"total_chunks=(?P<total>[0-9]+)",
    flags=re.IGNORECASE,
)
DIRECT_RANGE_CHUNK_RE = re.compile(
    r"Direct range chunk complete completed=(?P<completed>[0-9]+)/"
    r"(?P<total>[0-9]+)\s+connections=(?P<connections>[0-9]+)",
    flags=re.IGNORECASE,
)
HLS_TRANSPORT_RE = re.compile(
    r"Browser HLS download attempt|\bHLS batch\b|"
    r"Browser HLS segments merged|Selected media type:\s+hls",
    flags=re.IGNORECASE,
)
HLS_BATCH_RE = re.compile(
    r"HLS batch\s+[0-9]+-(?P<completed>[0-9]+)/(?P<total>[0-9]+)"
    r"\s+concurrency=(?P<connections>[0-9]+)",
    flags=re.IGNORECASE,
)
TRANSPORT_QUALIFICATION_RE = re.compile(
    r"Transport qualification:\s+status=(?P<status>passed|failed)"
    r"(?:\s+total=(?P<total>[0-9]+)\s+tls_backend=(?P<tls>\S+))?"
    r"(?:\s+reason=(?P<reason>.*))?",
    flags=re.IGNORECASE,
)
TRANSPORT_FALLBACK_RE = re.compile(
    r"Direct Range path unavailable; falling back to yt-dlp",
    flags=re.IGNORECASE,
)
SIZE_TOKEN_RE = re.compile(
    r"([0-9]+(?:\.[0-9]+)?)\s*(TiB|GiB|MiB|KiB|B)\b",
    flags=re.IGNORECASE,
)
SPEED_TOKEN_RE = re.compile(
    r"([0-9]+(?:\.[0-9]+)?)\s*(TiB|GiB|MiB|KiB|B)/s\b",
    flags=re.IGNORECASE,
)
ETA_TOKEN_RE = re.compile(r"\bETA\s+([0-9]+(?:\.[0-9]+)?)s\b", re.IGNORECASE)
PAUSE_EXIT_CODE = 130
SHUTDOWN_EXIT_CODE = 131
STALL_EXIT_CODE = 132
DELETE_EXIT_CODE = 133
RATE_LIMIT_PAUSE_EXIT_CODE = 134
DOWNLOAD_QUALITY_OPTIONS = (
    "site",
    "auto",
    "2160p",
    "1080p",
    "720p",
    "480p",
    "360p",
)


def normalise_requested_quality(value) -> str:
    raw = str(value or "site").strip().lower()
    if raw in ("", "site", "follow-site"):
        return "site"
    if raw in ("auto", "best"):
        return "auto"
    quality = normalise_quality_label(raw)
    if quality in DOWNLOAD_QUALITY_OPTIONS[2:]:
        return quality
    raise ValueError(
        "下载清晰度必须是 site、auto、2160p、1080p、720p、480p 或 360p"
    )
def normalize_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("URL 不能为空")
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise ValueError("URL 格式无效") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("仅支持完整的 HTTP/HTTPS URL")
    host = parsed.hostname.lower()
    port = parsed.port
    netloc = host
    if port and not (
        (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
    ):
        netloc = "{}:{}".format(host, port)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
    ]
    return urlunsplit((
        parsed.scheme.lower(),
        netloc,
        parsed.path or "/",
        urlencode(query),
        "",
    ))
def validate_output_dir(value: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("下载路径不能为空")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError("下载路径必须是绝对路径")
    path = path.resolve()
    if path == Path("/"):
        raise ValueError("不能直接使用根目录作为下载路径")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError("无法创建下载目录: {}".format(exc)) from exc
    if not path.is_dir():
        raise ValueError("下载路径不是目录")
    probe = path / ".download-write-test-{}".format(uuid.uuid4().hex)
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise ValueError("下载目录不可写: {}".format(exc)) from exc
    return path
def _read_task_summary(log_dir: Path, url: str) -> Optional[Dict]:
    summary_path = log_dir / "summary.jsonl"
    try:
        lines = summary_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except (TypeError, ValueError):
            continue
        if payload.get("input_url") == url:
            return payload
    return None
def _transport_kind_from_summary(summary: Dict) -> str:
    kind = str((summary or {}).get("transport_kind") or "").strip().lower()
    return kind if kind in {"hls", "direct-range", "yt-dlp"} else ""
def _read_download_log_tail(log_path: str) -> str:
    path = Path(str(log_path or "")).resolve()
    if path.parent != config.DOWNLOAD_TASK_LOG_DIR.resolve():
        return ""
    chunks = []
    for item in path.glob("download-*.log"):
        try:
            with item.open("rb") as handle:
                handle.seek(max(0, item.stat().st_size - 8 * 1024 * 1024))
                chunks.append(handle.read().decode("utf-8", errors="ignore"))
        except OSError:
            continue
    return "\n".join(chunks)
def _infer_transport_kind_from_log(log_path: str) -> str:
    text = _read_download_log_tail(log_path)
    if not text:
        return ""
    candidates = []
    markers = (
        ("Browser HLS segments merged:", "hls"),
        ("Direct range download complete:", "direct-range"),
        ("yt-dlp options:", "yt-dlp"),
    )
    for marker, kind in markers:
        position = text.rfind(marker)
        if position >= 0:
            candidates.append((position, kind))
    return max(candidates, default=(0, ""))[1]
def _infer_hls_progress_from_log(log_path: str) -> Dict:
    text = _read_download_log_tail(log_path)
    if not text:
        return {}
    matches = list(HLS_BATCH_RE.finditer(text))
    if not matches:
        return {}
    match = matches[-1]
    return {
        "hls_connection_count": int(match.group("connections")),
        "hls_completed_segments": int(match.group("completed")),
        "hls_total_segments": int(match.group("total")),
    }
def backfill_download_task_media_info(logger: logging.Logger) -> Dict[str, int]:
    stats = {
        "tasks": 0,
        "from_videos": 0,
        "from_summaries": 0,
        "from_ffprobe": 0,
        "unknown": 0,
        "video_relation_updates": 0,
    }
    stats["video_relation_updates"] = (
        database.backfill_download_media_from_videos()
    )
    for task in database.download_tasks_missing_media_info():
        stats["tasks"] += 1
        width = _optional_dimension(
            task.get("media_width") or task.get("video_width")
        )
        height = _optional_dimension(
            task.get("media_height") or task.get("video_height")
        )
        media_fields = _media_fields_from_summary({}, {
            "width": width,
            "height": height,
        })
        source = "videos" if media_fields else ""

        log_path = str(task.get("log_path") or "").strip()
        if log_path:
            summary = _read_task_summary(Path(log_path), task["url"]) or {}
            summary_fields = _media_fields_from_summary(summary, {
                "width": width,
                "height": height,
            })
            if summary_fields:
                media_fields.update(summary_fields)
                source = "summaries"

        if not media_fields.get("media_quality"):
            file_path = str(task.get("file_path") or "").strip()
            if file_path:
                path = Path(file_path)
                if path.is_file():
                    info = probe_video(path)
                    probe_fields = _media_fields_from_summary({}, info)
                    if probe_fields:
                        media_fields.update(probe_fields)
                        source = "ffprobe"

        if not media_fields:
            stats["unknown"] += 1
            logger.info(
                "Download task media unknown task_id=%s",
                task["id"],
                extra={"operation": "download.task.media_unknown"},
            )
            continue
        database.update_download_task(int(task["id"]), **media_fields)
        if source == "summaries":
            stats["from_summaries"] += 1
        elif source == "ffprobe":
            stats["from_ffprobe"] += 1
        else:
            stats["from_videos"] += 1
        logger.info(
            "Download task media backfilled task_id=%s quality=%s "
            "resolution=%s source=%s",
            task["id"],
            media_fields.get("media_quality"),
            resolution_label(
                media_fields.get("media_width"),
                media_fields.get("media_height"),
            ),
            source or "unknown",
            extra={"operation": "download.task.media_backfilled"},
        )
    return stats
def backfill_download_task_transport_info(
    logger: logging.Logger,
) -> Dict[str, int]:
    stats = {
        "tasks": 0,
        "hls": 0,
        "direct-range": 0,
        "yt-dlp": 0,
        "unknown": 0,
        "hls_progress": 0,
    }
    seen_ids = set()
    for task in database.download_tasks_missing_transport_info():
        seen_ids.add(int(task["id"]))
        stats["tasks"] += 1
        log_path = str(task.get("log_path") or "").strip()
        summary = (
            _read_task_summary(Path(log_path), task["url"])
            if log_path
            else None
        ) or {}
        transport_kind = (
            _transport_kind_from_summary(summary)
            or _infer_transport_kind_from_log(log_path)
        )
        if not transport_kind:
            stats["unknown"] += 1
            continue
        update_fields = {"transport_kind": transport_kind}
        if transport_kind == "hls":
            update_fields.update(_infer_hls_progress_from_log(log_path))
        database.update_download_task(int(task["id"]), **update_fields)
        stats[transport_kind] += 1
        if task.get("status") == "success" and task.get("transport_site_key"):
            learn_transport_mode(
                str(task["transport_site_key"]),
                int(task["id"]),
                transport_kind,
            )
        logger.info(
            "Download task transport backfilled task_id=%s transport=%s",
            task["id"],
            transport_kind,
            extra={"operation": "download.task.transport_backfilled"},
        )
    for task in database.download_tasks_missing_hls_progress_info():
        if int(task["id"]) in seen_ids:
            continue
        hls_fields = _infer_hls_progress_from_log(
            str(task.get("log_path") or "")
        )
        if not hls_fields:
            continue
        database.update_download_task(int(task["id"]), **hls_fields)
        stats["hls_progress"] += 1
        logger.info(
            "Download task HLS progress backfilled task_id=%s "
            "connections=%s segments=%s/%s",
            task["id"],
            hls_fields["hls_connection_count"],
            hls_fields["hls_completed_segments"],
            hls_fields["hls_total_segments"],
            extra={"operation": "download.task.hls_progress_backfilled"},
        )
    return stats
def _size_to_bytes(value: str, unit: str) -> int:
    multipliers = {
        "b": 1,
        "kib": 1024,
        "mib": 1024 ** 2,
        "gib": 1024 ** 3,
        "tib": 1024 ** 4,
    }
    try:
        return int(float(value) * multipliers[unit.lower()])
    except (KeyError, TypeError, ValueError):
        return 0
def _parse_progress_bytes(detail: str, percent: float) -> tuple:
    downloaded, total, _, _ = _parse_progress_metrics(detail, percent)
    return downloaded, total
def _parse_progress_metrics(detail: str, percent: float) -> tuple:
    parts = [part.strip() for part in detail.split("|") if part.strip()]
    if not parts:
        return 0, 0, 0, None
    size_part = parts[1] if parts[0].lower().startswith("segment") else parts[0]
    matches = SIZE_TOKEN_RE.findall(size_part)
    if not matches:
        return 0, 0, 0, None
    downloaded = _size_to_bytes(*matches[0])
    total = _size_to_bytes(*matches[1]) if len(matches) > 1 else 0
    if not total and downloaded and percent > 0:
        total = int(downloaded * 100.0 / percent)
    speed_match = SPEED_TOKEN_RE.search(detail)
    speed = _size_to_bytes(*speed_match.groups()) if speed_match else 0
    eta_match = ETA_TOKEN_RE.search(detail)
    eta = int(round(float(eta_match.group(1)))) if eta_match else None
    return downloaded, total, speed, eta
def _optional_dimension(value) -> Optional[int]:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None
def _media_fields_from_line(line: str) -> Dict:
    match = MEDIA_SELECTED_RE.search(str(line or ""))
    if not match:
        return {}
    width = _optional_dimension(match.group("width"))
    height = _optional_dimension(match.group("height"))
    quality = normalise_quality_label(
        match.group("quality"),
        width=width,
        height=height,
    )
    return {
        "media_width": width,
        "media_height": height,
        "media_quality": quality,
    }
def _hls_fields_from_line(line: str) -> Dict:
    match = HLS_BATCH_RE.search(str(line or ""))
    if not match:
        return {}
    return {
        "hls_connection_count": int(match.group("connections")),
        "hls_completed_segments": int(match.group("completed")),
        "hls_total_segments": int(match.group("total")),
    }
def _media_fields_from_summary(summary: Dict, video: Optional[Dict] = None) -> Dict:
    payload = summary or {}
    video_payload = video or {}
    width = _optional_dimension(
        payload.get("media_width") or video_payload.get("width")
    )
    height = _optional_dimension(
        payload.get("media_height") or video_payload.get("height")
    )
    quality = normalise_quality_label(
        payload.get("media_quality"),
        width=width,
        height=height,
    )
    result = {
        "media_width": width,
        "media_height": height,
        "media_quality": quality,
        "media_strategy_key": payload.get("media_strategy_key"),
        "selection_reason": payload.get("selection_reason"),
        "selected_candidate_source": payload.get(
            "selected_candidate_source"
        ),
        "selected_candidate_kind": payload.get("selected_candidate_kind"),
        "fallback_trace_json": (
            json.dumps(
                payload.get("fallback_trace"),
                ensure_ascii=False,
            )
            if payload.get("fallback_trace") is not None
            else None
        ),
    }
    return {key: value for key, value in result.items() if value is not None}
def decorate_download_task_metrics(
    task: Dict,
    now: Optional[datetime] = None,
) -> Dict:
    result = dict(task)
    current = now or datetime.now().astimezone()
    status = str(result.get("status") or "")
    stage = str(result.get("stage") or "")
    elapsed = int(result.get("elapsed_seconds") or 0)
    run_started_at = result.get("run_started_at")
    if status in {"running", "pausing"} and run_started_at:
        try:
            started = datetime.fromisoformat(str(run_started_at))
            if started.tzinfo is None:
                started = started.astimezone()
            elapsed += max(0, int((current - started).total_seconds()))
        except (TypeError, ValueError):
            pass

    speed = float(result.get("speed_bytes_per_sec") or 0)
    eta = result.get("eta_seconds")
    eta = int(eta) if eta is not None else None
    stale = False
    if status == "running" and stage == "downloading":
        last_progress_at = result.get("last_progress_at")
        try:
            last_progress = datetime.fromisoformat(str(last_progress_at))
            if last_progress.tzinfo is None:
                last_progress = last_progress.astimezone()
            stale = (
                current - last_progress
            ).total_seconds() > config.download_metrics_stale_seconds()
        except (TypeError, ValueError):
            stale = True
        if stale:
            speed = 0
            eta = None

    speed_kind = ""
    if status == "running" and speed > 0:
        speed_kind = "current"
    elif status == "success" and elapsed > 0:
        speed = max(
            0.0,
            float(result.get("downloaded_bytes") or 0) / elapsed,
        )
        speed_kind = "average" if speed > 0 else ""
    else:
        speed = 0

    if (
        status == "running"
        and stage == "downloading"
        and eta is None
        and speed > 0
    ):
        remaining = max(
            0,
            int(result.get("total_bytes") or 0)
            - int(result.get("downloaded_bytes") or 0),
        )
        if remaining and speed > 0:
            eta = int((remaining + speed - 1) // speed)
    if status == "success":
        eta = 0
    elif status != "running":
        eta = None

    width = _optional_dimension(
        result.get("media_width") or result.get("video_width")
    )
    height = _optional_dimension(
        result.get("media_height") or result.get("video_height")
    )
    quality = normalise_quality_label(
        result.get("media_quality"),
        width=width,
        height=height,
    )
    try:
        requested_quality = normalise_requested_quality(
            result.get("requested_quality")
        )
    except ValueError:
        requested_quality = "auto"
    result.update(
        {
            "elapsed_live_seconds": elapsed,
            "speed_live_bytes": speed,
            "speed_kind": speed_kind,
            "eta_live_seconds": eta,
            "metrics_stale": stale,
            "display_media_width": width,
            "display_media_height": height,
            "display_media_quality": quality,
            "display_requested_quality": (
                "跟随站点"
                if requested_quality == "site"
                else (
                    "自动"
                    if requested_quality == "auto"
                    else (
                        "4K 优先"
                        if requested_quality == "2160p"
                        else requested_quality
                    )
                )
            ),
            "display_media_resolution": resolution_label(width, height),
        }
    )
    return result
