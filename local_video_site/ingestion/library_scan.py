from __future__ import annotations

import fcntl
import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

from local_video_site.core import config
from local_video_site import database
from local_video_site.catalog.categories import rebuild_actor_index, rebuild_category_index
from local_video_site.media.info import probe_video
from local_video_site.media.assets.thumbnails import generate_thumbnail
from local_video_site.storage.volumes import resolve_video_storage


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
IGNORED_DIRECTORY_NAMES = {
    ".chrome-cdp-profile",
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    "playback-cache",
    "thumbnails",
    ".video-trim-backups",
}
ACTIVE_SCAN_STATES = {"scanning", "indexing_metadata"}
_SCAN_LOCK = threading.Lock()
LOGGER = logging.getLogger(__name__)


@dataclass
class ScanStats:
    discovered: int = 0
    added: int = 0
    changed: int = 0
    missing: int = 0
    categories_rebuilt: bool = False


class ScanAlreadyRunning(RuntimeError):
    pass


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _acquire_scan_lock():
    if not _SCAN_LOCK.acquire(blocking=False):
        raise ScanAlreadyRunning("A library scan is already running")
    handle = None
    try:
        config.ensure_data_directories()
        lock_path = config.DATA_DIR / "scan.lock"
        handle = lock_path.open("a+")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        if handle is not None:
            handle.close()
        _SCAN_LOCK.release()
        raise ScanAlreadyRunning("A library scan is already running") from exc
    except Exception:
        _SCAN_LOCK.release()
        raise
    return handle


def acquire_library_write_lock():
    return _acquire_scan_lock()


def release_library_write_lock(handle) -> None:
    try:
        if handle is not None:
            handle.close()
    finally:
        _SCAN_LOCK.release()


class ScanRuntime:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = "idle"
        self._started_at: Optional[str] = None
        self._finished_at: Optional[str] = None
        self._roots: List[str] = []
        self._stats: dict = {}
        self._error: Optional[str] = None

    def _payload_locked(self) -> dict:
        return {
            "state": self._state,
            "started_at": self._started_at,
            "finished_at": self._finished_at,
            "roots": list(self._roots),
            "stats": dict(self._stats),
            "error": self._error,
        }

    def _persist_locked(self) -> None:
        try:
            config.ensure_data_directories()
            temporary = config.SCAN_STATUS_PATH.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    self._payload_locked(),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            temporary.replace(config.SCAN_STATUS_PATH)
        except OSError:
            LOGGER.exception("Failed to persist scan status")

    def try_start(self, roots: List[Path]) -> bool:
        with self._lock:
            if self._state in ACTIVE_SCAN_STATES:
                return False
            self._state = "scanning"
            self._started_at = _now()
            self._finished_at = None
            self._roots = [str(root) for root in roots]
            self._stats = {}
            self._error = None
            self._persist_locked()
            return True

    def index_complete(self, stats: ScanStats) -> None:
        with self._lock:
            self._state = "indexing_metadata"
            self._stats = {
                "discovered": stats.discovered,
                "added": stats.added,
                "changed": stats.changed,
                "missing": stats.missing,
                "categories_rebuilt": stats.categories_rebuilt,
            }
            self._persist_locked()

    def resources_complete(self) -> None:
        with self._lock:
            self._state = "complete"
            self._finished_at = _now()
            self._persist_locked()

    def fail(self, error: str) -> None:
        with self._lock:
            self._state = "failed"
            self._finished_at = _now()
            self._error = error
            self._persist_locked()

    def snapshot(self) -> dict:
        with self._lock:
            result = self._payload_locked()
        try:
            result.update(database.resource_stats())
        except Exception as exc:
            result["resource_stats_error"] = str(exc)
        return result


SCAN_RUNTIME = ScanRuntime()


def persisted_scan_status() -> dict:
    try:
        result = json.loads(config.SCAN_STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        result = {}
    try:
        result.update(database.resource_stats())
    except Exception as exc:
        result["resource_stats_error"] = str(exc)
    return result


def iter_video_files(root: Path, minimum_bytes: int) -> Iterable[Path]:
    if not root.is_dir():
        return
    data_root = config.DATA_DIR.resolve()
    resolved_root = root.resolve()
    if resolved_root == data_root or data_root in resolved_root.parents:
        return

    def ignored(directory: Path) -> bool:
        if directory.name in IGNORED_DIRECTORY_NAMES:
            return True
        if directory.name.startswith("."):
            return True
        candidate = directory.resolve()
        return candidate == data_root or data_root in candidate.parents

    for current_text, directories, files in os.walk(
        str(root),
        followlinks=False,
    ):
        current = Path(current_text)
        directories[:] = [
            name
            for name in directories
            if not ignored(current / name)
        ]
        for name in files:
            path = current / name
            if path.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            if path.name.endswith((".part", ".ytdl")):
                continue
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                if path.stat().st_size < minimum_bytes:
                    continue
            except OSError:
                continue
            yield path.resolve()


def scan_library(
    roots: Optional[List[Path]] = None,
    minimum_bytes: Optional[int] = None,
) -> ScanStats:
    lock_handle = _acquire_scan_lock()
    try:
        roots = [
            Path(root).expanduser().resolve()
            for root in (roots or config.configured_roots())
        ]
        minimum_bytes = (
            config.minimum_video_bytes()
            if minimum_bytes is None
            else max(0, int(minimum_bytes))
        )
        database.init_db()
        stats = ScanStats()
        seen_paths = set()

        for root in roots:
            for path in iter_video_files(root, minimum_bytes):
                key = str(path)
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                try:
                    stat = path.stat()
                except OSError:
                    continue
                stats.discovered += 1
                status = database.upsert_video(
                    path=path,
                    title=path.stem,
                    directory=root.name,
                    size=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                )
                if status == "added":
                    stats.added += 1
                elif status == "changed":
                    stats.changed += 1

        active_roots = [root for root in roots if root.is_dir()]
        stats.missing = database.mark_missing_except(active_roots, seen_paths)
        if stats.added or stats.changed or stats.missing:
            rebuild_category_index()
            rebuild_actor_index()
            stats.categories_rebuilt = True
        return stats
    finally:
        lock_handle.close()
        _SCAN_LOCK.release()


def process_pending_metadata(
    logger: Optional[logging.Logger] = None,
    max_workers: int = 2,
    limit: Optional[int] = None,
) -> int:
    logger = logger or logging.getLogger(__name__)
    rows = database.pending_metadata(limit=limit)
    if not rows:
        return 0

    processed = 0
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        futures = {
            executor.submit(_process_video, row, logger): row for row in rows
        }
        for future in as_completed(futures):
            try:
                future.result()
                processed += 1
            except Exception:
                logger.exception("Unexpected metadata worker failure")
    return processed


def process_pending_until_empty(
    logger: Optional[logging.Logger] = None,
    stop_event: Optional[threading.Event] = None,
) -> int:
    logger = logger or logging.getLogger(__name__)
    stop_event = stop_event or threading.Event()
    processed = 0
    while not stop_event.is_set():
        row = database.claim_pending_metadata()
        if row is None:
            break
        try:
            _process_video(row, logger)
            processed += 1
        except Exception:
            database.update_metadata(
                video_id=row["id"],
                info={},
                thumbnail_path=None,
                status="unavailable",
                error="metadata processing failed",
            )
            logger.exception("Metadata worker failed for %s", row["path"])
    return processed


def repair_missing_thumbnails(
    logger: Optional[logging.Logger] = None,
    stop_event: Optional[threading.Event] = None,
    limit: Optional[int] = None,
) -> int:
    logger = logger or logging.getLogger(__name__)
    stop_event = stop_event or threading.Event()
    repaired = 0
    for row in database.videos_without_thumbnails(limit=limit):
        if stop_event.is_set():
            break
        path = Path(row["path"])
        if not path.is_file():
            database.mark_video_missing(row["id"])
            continue
        thumbnail = generate_thumbnail(
            video_path=path,
            size=row["size"],
            mtime_ns=row["mtime_ns"],
            duration=row.get("duration"),
        )
        if not thumbnail:
            logger.warning("Thumbnail repair failed: %s", path.name)
            continue
        database.update_thumbnail(row["id"], str(thumbnail))
        repaired += 1
        logger.info("Thumbnail repaired: %s", path.name)
    return repaired


def backfill_episode_technical_metadata(
    logger: Optional[logging.Logger] = None,
    limit: Optional[int] = None,
) -> int:
    logger = logger or logging.getLogger(__name__)
    updated = 0
    for row in database.episode_videos_missing_technical_info(limit=limit):
        try:
            resolution = resolve_video_storage(row)
            if not resolution["online"]:
                continue
            path = Path(resolution["path"])
            if not path.is_file():
                continue
            changed = False
            needs_probe = any(
                row.get(key) is None
                for key in (
                    "duration",
                    "width",
                    "height",
                    "video_codec",
                    "audio_codec",
                )
            )
            info = dict(row)
            if needs_probe:
                info = probe_video(path)
                if info:
                    database.update_video_technical_info(row["id"], info)
                    changed = True
                else:
                    logger.warning(
                        "Episode technical probe returned no data video_id=%s",
                        row["id"],
                    )
            if not row.get("thumbnail_path"):
                thumbnail = generate_thumbnail(
                    video_path=path,
                    size=int(row["size"]),
                    mtime_ns=int(row["mtime_ns"]),
                    duration=info.get("duration"),
                )
                if thumbnail:
                    database.update_thumbnail(row["id"], str(thumbnail))
                    changed = True
            if changed:
                updated += 1
        except Exception:
            logger.exception(
                "Episode technical backfill failed video_id=%s",
                row["id"],
            )
    return updated


def _process_video(row: dict, logger: logging.Logger) -> None:
    path = Path(row["path"])
    if not path.is_file():
        database.mark_video_missing(row["id"])
        return

    info = probe_video(path)
    thumbnail = generate_thumbnail(
        video_path=path,
        size=row["size"],
        mtime_ns=row["mtime_ns"],
        duration=info.get("duration"),
    )
    status = "ready" if info or thumbnail else "unavailable"
    database.update_metadata(
        video_id=row["id"],
        info=info,
        thumbnail_path=str(thumbnail) if thumbnail else None,
        status=status,
        error=None if status == "ready" else "ffprobe/ffmpeg unavailable or unsupported media",
    )
    logger.info(
        "Indexed metadata: %s (%sx%s)",
        path.name,
        info.get("width") or "?",
        info.get("height") or "?",
    )
