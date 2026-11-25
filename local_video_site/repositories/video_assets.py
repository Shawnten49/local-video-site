from __future__ import annotations

import json
import sqlite3
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from local_video_site.core import config
from ..db.connection import connect
from ..db.transaction import now_iso as _now
from local_video_site.episodes.naming import is_generic_episode_title, meaningful_episode_title, season_number_from_label
from local_video_site.core.search import parse_search_query
from .videos import video_repository, video_with_preview_state
from .episodes import _ensure_video_satellites, update_season_assets_from_video


PREVIEW_VERSION = 1
_video_with_preview_state = video_with_preview_state


def update_thumbnail(video_id: int, thumbnail_path: str) -> None:
    with connect() as connection:
        now = _now()
        _ensure_video_satellites(connection, int(video_id), now)
        connection.execute(
            """
            UPDATE video_assets
            SET thumbnail_path = ?, updated_at = ?
            WHERE video_id = ?
            """,
            (thumbnail_path, now, video_id),
        )
    update_season_assets_from_video(int(video_id))
def update_custom_cover(
    video_id: int,
    thumbnail_path: str,
    timestamp: float,
) -> None:
    now = _now()
    with connect() as connection:
        _ensure_video_satellites(connection, int(video_id), now)
        connection.execute(
            """
            UPDATE video_assets
            SET thumbnail_path = ?, cover_source = 'custom',
                cover_timestamp = ?, cover_updated_at = ?, updated_at = ?
            WHERE video_id = ?
            """,
            (thumbnail_path, timestamp, now, now, video_id),
        )
    update_season_assets_from_video(int(video_id))
def reset_auto_cover(
    video_id: int,
    thumbnail_path: Optional[str],
) -> None:
    now = _now()
    with connect() as connection:
        _ensure_video_satellites(connection, int(video_id), now)
        connection.execute(
            """
            UPDATE video_assets
            SET thumbnail_path = ?, cover_source = 'auto',
                cover_timestamp = NULL, cover_updated_at = ?, updated_at = ?
            WHERE video_id = ?
            """,
            (thumbnail_path, now, now, video_id),
        )
    update_season_assets_from_video(int(video_id))
def register_playback_cache(
    cache_path: Path,
    video: Dict,
    cache_size: int,
    preparation_mode: str = "legacy",
    decision_reason: str = "",
    decision_version: int = 1,
) -> None:
    now = _now()
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO playback_cache_entries (
                cache_path, video_id, source_path, source_size,
                source_mtime_ns, cache_size, preparation_mode,
                source_video_codec, source_audio_codec, decision_reason,
                decision_version, created_at, last_accessed_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready')
            ON CONFLICT(cache_path) DO UPDATE SET
                video_id = excluded.video_id,
                source_path = excluded.source_path,
                source_size = excluded.source_size,
                source_mtime_ns = excluded.source_mtime_ns,
                cache_size = excluded.cache_size,
                preparation_mode = excluded.preparation_mode,
                source_video_codec = excluded.source_video_codec,
                source_audio_codec = excluded.source_audio_codec,
                decision_reason = excluded.decision_reason,
                decision_version = excluded.decision_version,
                last_accessed_at = excluded.last_accessed_at,
                status = 'ready'
            """,
            (
                str(cache_path),
                int(video["id"]),
                str(video["path"]),
                int(video["size"]),
                int(video["mtime_ns"]),
                int(cache_size),
                str(preparation_mode or "legacy"),
                video.get("video_codec"),
                video.get("audio_codec"),
                str(decision_reason or ""),
                int(decision_version or 1),
                now,
                now,
            ),
        )
def touch_playback_cache(cache_path: Path) -> None:
    with connect() as connection:
        connection.execute(
            """
            UPDATE playback_cache_entries
            SET last_accessed_at = ?
            WHERE cache_path = ?
            """,
            (_now(), str(cache_path)),
        )
def list_playback_cache_entries(
    video_id: Optional[int] = None,
) -> List[Dict]:
    with connect() as connection:
        if video_id is None:
            rows = connection.execute(
                """
                SELECT * FROM playback_cache_entries
                ORDER BY created_at ASC, cache_path ASC
                """
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT * FROM playback_cache_entries
                WHERE video_id = ?
                ORDER BY created_at ASC, cache_path ASC
                """,
                (int(video_id),),
            ).fetchall()
    return [dict(row) for row in rows]
def delete_playback_cache_entry(cache_path: Path) -> None:
    with connect() as connection:
        connection.execute(
            "DELETE FROM playback_cache_entries WHERE cache_path = ?",
            (str(cache_path),),
        )
def recover_interrupted_previews() -> int:
    with connect() as connection:
        cursor = connection.execute(
            """
            UPDATE video_assets
            SET preview_status = 'pending',
                preview_error = NULL,
                updated_at = ?
            WHERE preview_status = 'processing'
            """,
            (_now(),),
        )
        return cursor.rowcount
def claim_preview_video(retry_failed: bool = False) -> Optional[Dict]:
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        clauses = [
            "missing = 0",
            "is_deleted = 0",
            "metadata_status = 'ready'",
            "duration IS NOT NULL",
            "duration > 0",
            "(preview_status IN ('pending', 'none') "
            "OR preview_version < ? "
            "OR preview_source_size IS NULL "
            "OR preview_source_size <> size "
            "OR preview_source_mtime_ns IS NULL "
            "OR preview_source_mtime_ns <> mtime_ns)",
        ]
        params: List = [PREVIEW_VERSION]
        if retry_failed:
            clauses[-1] = "(" + clauses[-1] + " OR preview_status = 'failed')"
        row = connection.execute(
            """
            SELECT * FROM video_catalog
            WHERE {}
            ORDER BY id ASC
            LIMIT 1
            """.format(" AND ".join(clauses)),
            params,
        ).fetchone()
        if row is None:
            connection.commit()
            return None
        connection.execute(
            """
            UPDATE video_assets
            SET preview_status = 'processing',
                preview_error = NULL,
                updated_at = ?
            WHERE video_id = ?
            """,
            (_now(), row["id"]),
        )
        connection.commit()
    video = _video_with_preview_state(row)
    video["preview_status"] = "processing"
    return video
def save_preview_result(
    video_id: int,
    status: str,
    preview_path: Optional[str] = None,
    start: Optional[float] = None,
    duration: Optional[float] = None,
    source_size: Optional[int] = None,
    source_mtime_ns: Optional[int] = None,
    error: Optional[str] = None,
) -> None:
    now = _now()
    with connect() as connection:
        _ensure_video_satellites(connection, int(video_id), now)
        connection.execute(
            """
            UPDATE video_assets
            SET preview_path = COALESCE(?, preview_path),
                preview_status = ?,
                preview_error = ?,
                preview_start = COALESCE(?, preview_start),
                preview_duration = COALESCE(?, preview_duration),
                preview_updated_at = CASE
                    WHEN ? = 'ready' THEN ?
                    ELSE preview_updated_at
                END,
                preview_version = CASE
                    WHEN ? = 'ready' THEN ?
                    ELSE preview_version
                END,
                preview_source_size = COALESCE(?, preview_source_size),
                preview_source_mtime_ns = COALESCE(
                    ?,
                    preview_source_mtime_ns
                ),
                updated_at = ?
            WHERE video_id = ?
            """,
            (
                preview_path,
                status,
                error,
                start,
                duration,
                status,
                now,
                status,
                PREVIEW_VERSION,
                source_size,
                source_mtime_ns,
                now,
                int(video_id),
            ),
        )
    update_season_assets_from_video(int(video_id))
def touch_preview_access(video_id: int) -> None:
    with connect() as connection:
        connection.execute(
            """
            UPDATE video_assets
            SET preview_last_accessed_at = ?
            WHERE video_id = ?
            """,
            (_now(), int(video_id)),
        )
def preview_status_counts() -> Dict:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT preview_status, COUNT(*) AS count
            FROM video_catalog
            WHERE missing = 0 AND is_deleted = 0
            GROUP BY preview_status
            """
        ).fetchall()
    return {
        row["preview_status"]: int(row["count"])
        for row in rows
    }
def reset_previews_pending() -> int:
    with connect() as connection:
        cursor = connection.execute(
            """
            UPDATE video_assets
            SET preview_status = 'pending',
                preview_error = NULL,
                preview_source_size = NULL,
                preview_source_mtime_ns = NULL,
                updated_at = ?
            WHERE video_id IN (
                SELECT v.id FROM video_catalog v
                WHERE v.missing = 0
                  AND v.is_deleted = 0
                  AND v.metadata_status = 'ready'
            )
            """,
            (_now(),),
        )
        return cursor.rowcount
