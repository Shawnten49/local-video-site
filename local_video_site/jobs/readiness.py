from __future__ import annotations

import os
import tempfile
import time
from typing import Dict

from local_video_site.core import config
from local_video_site import database
from local_video_site.media.ffmpeg import ffmpeg_runtime_status
from local_video_site.jobs.integrity import latest_integrity_report
from local_video_site.jobs.leases import job_lease_manager
from local_video_site.settings import get_runtime_settings


_WORKER_SPECS = (
    ("download", "download_worker", config.download_worker_enabled),
    ("preview", "preview_worker", config.preview_generation_enabled),
    ("actor_avatar", "actor_avatar_worker", config.actor_avatar_sync_enabled),
    ("migration", "migration_worker", config.migration_worker_enabled),
    ("external_scan", "external_scan_worker", config.external_scan_worker_enabled),
    ("storage_volume", "storage_volume_worker", lambda: True),
    ("trim", "trim_worker", config.video_trim_worker_enabled),
    ("format", "format_worker", config.video_format_worker_enabled),
    ("delete", "delete_worker", config.video_delete_worker_enabled),
    ("integrity", "integrity_worker", config.integrity_worker_enabled),
)


def _database_check() -> Dict:
    started = time.monotonic()
    try:
        with database.connect() as connection:
            integrity = connection.execute(
                "PRAGMA quick_check(1)"
            ).fetchone()[0]
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE users SET updated_at = updated_at WHERE 0"
            )
            connection.rollback()
            connection.execute("SELECT 1").fetchone()
    except Exception as exc:
        return {
            "ok": False,
            "status": "unavailable",
            "error": type(exc).__name__,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
        }
    return {
        "ok": integrity == "ok",
        "status": "ready" if integrity == "ok" else "corrupt",
        "integrity": str(integrity),
        "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
    }


def _storage_directories() -> Dict:
    directories = {
        "data": config.DATA_DIR,
        "database": config.DATABASE_PATH.parent,
        "thumbnails": config.THUMBNAIL_DIR,
        "covers": config.COVER_DIR,
        "cover_previews": config.COVER_PREVIEW_DIR,
        "series_covers": config.SERIES_COVER_DIR,
        "season_covers": config.SEASON_COVER_DIR,
        "actor_avatars": config.ACTOR_AVATAR_DIR,
        "previews": config.PREVIEW_DIR,
        "playback_cache": config.playback_cache_dir(),
        "download_logs": config.DOWNLOAD_TASK_LOG_DIR,
        "download_controls": config.DOWNLOAD_CONTROL_DIR,
        "delete_quarantine": config.DELETE_QUARANTINE_DIR,
        "logs": config.LOG_PATH.parent,
    }
    results = {}
    try:
        config.ensure_data_directories()
        with tempfile.NamedTemporaryFile(
            prefix=".readiness-",
            dir=config.DATA_DIR,
            delete=True,
        ) as probe:
            probe.write(b"ready")
            probe.flush()
    except OSError as exc:
        probe_result = {
            "ok": False,
            "status": "not_writable",
            "error": type(exc).__name__,
        }
    else:
        probe_result = {"ok": True, "status": "ready"}

    for name, path in directories.items():
        if not path.is_dir():
            results[name] = {"ok": False, "status": "missing"}
        elif not os.access(path, os.W_OK):
            results[name] = {"ok": False, "status": "not_writable"}
        else:
            results[name] = {"ok": True, "status": "ready"}
    ready = probe_result["ok"] and all(
        item["ok"] for item in results.values()
    )
    return {
        "ok": ready,
        "status": "ready" if ready else "not_ready",
        "write_probe": probe_result,
        "directories": results,
    }


def _worker_check(app_state) -> Dict:
    workers = {}
    for name, attribute, enabled in _WORKER_SPECS:
        if not enabled():
            workers[name] = {"ok": True, "status": "disabled"}
            continue
        worker = getattr(app_state, attribute, None)
        thread = getattr(worker, "thread", None) if worker is not None else None
        runner = getattr(worker, "runner", None) if worker is not None else None
        metrics = None
        if runner is not None and hasattr(runner, "snapshot"):
            snapshot = runner.snapshot()
            heartbeat_age = max(
                0.0,
                time.monotonic() - snapshot.last_heartbeat_at,
            )
            metrics = {
                "processed": snapshot.processed,
                "succeeded": snapshot.succeeded,
                "failures": snapshot.failures,
                "retries": snapshot.retries,
                "cancelled": snapshot.cancelled,
                "last_job_id": snapshot.last_job_id,
                "last_outcome": snapshot.last_outcome,
                "heartbeat_age_seconds": round(heartbeat_age, 1),
                "current_job_age_seconds": round(
                    snapshot.current_job_age_seconds,
                    1,
                ),
            }
        if worker is None:
            workers[name] = {"ok": False, "status": "not_started"}
        elif thread is None:
            workers[name] = {"ok": False, "status": "not_running"}
        elif (
            metrics is not None
            and metrics["heartbeat_age_seconds"]
            > max(60, config.job_lease_seconds() * 2)
        ):
            workers[name] = {"ok": False, "status": "heartbeat_stale"}
        elif thread.is_alive():
            workers[name] = {"ok": True, "status": "running"}
        else:
            workers[name] = {"ok": False, "status": "stopped"}
        if metrics is not None:
            workers[name]["metrics"] = metrics
    return {
        "ok": all(item["ok"] for item in workers.values()),
        "workers": workers,
    }


def _settings_check() -> Dict:
    try:
        service = get_runtime_settings()
        service.refresh()
        items = service.list_items()
    except Exception as exc:
        return {
            "ok": False,
            "status": "invalid",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    return {
        "ok": True,
        "status": "ready",
        "count": len(items),
        "environment_locked": sum(
            1 for item in items if item.get("env_locked")
        ),
    }


def readiness_snapshot(app_state) -> Dict:
    lifespan_ready = bool(getattr(app_state, "lifespan_ready", False))
    database_result = _database_check()
    storage_result = _storage_directories()
    settings_result = _settings_check()
    worker_result = (
        _worker_check(app_state)
        if settings_result["ok"]
        else {
            "ok": False,
            "status": "not_checked",
            "workers": {},
        }
    )
    integrity_result = latest_integrity_report()
    if not integrity_result:
        integrity_result = getattr(app_state, "integrity_report", {})
    if not isinstance(integrity_result, dict):
        integrity_result = {}
    ready = (
        lifespan_ready
        and database_result["ok"]
        and storage_result["ok"]
        and settings_result["ok"]
        and worker_result["ok"]
    )
    return {
        "status": "ready" if ready else "not_ready",
        "ready": ready,
        "service": "local-video-site",
        "checks": {
            "lifespan": {
                "ok": lifespan_ready,
                "status": "ready" if lifespan_ready else "not_started",
            },
            "database": database_result,
            "settings": settings_result,
            "storage": storage_result,
            "workers": worker_result,
            "ffmpeg": ffmpeg_runtime_status(),
            "integrity": {
                "ok": bool(integrity_result.get("ok", False)),
                "status": integrity_result.get("status", "not_checked"),
                "issue_count": int(
                    integrity_result.get("issue_count", 0)
                ),
            },
            "job_leases": {
                "active": job_lease_manager.active(),
            },
        },
    }
