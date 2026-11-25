from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ..db.connection import connect


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def upsert_subtitle_asset(
    video_id: int,
    source_stream_index: int,
    language: str,
    title: str,
    extension: str,
    storage_path: Path,
    source_size: int,
    source_mtime_ns: int,
    default_track: bool = False,
) -> Dict:
    now = _now()
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO video_subtitle_assets (
                video_id, source_stream_index, language, title,
                extension, storage_path, default_track,
                source_size, source_mtime_ns, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id, source_stream_index, extension)
            DO UPDATE SET
                language = excluded.language,
                title = excluded.title,
                storage_path = excluded.storage_path,
                default_track = excluded.default_track,
                source_size = excluded.source_size,
                source_mtime_ns = excluded.source_mtime_ns,
                updated_at = excluded.updated_at
            """,
            (
                int(video_id),
                int(source_stream_index),
                str(language or ""),
                str(title or ""),
                str(extension).lstrip(".").lower(),
                str(storage_path),
                1 if default_track else 0,
                int(source_size),
                int(source_mtime_ns),
                now,
                now,
            ),
        )
        row = connection.execute(
            """
            SELECT *
            FROM video_subtitle_assets
            WHERE video_id = ?
              AND source_stream_index = ?
              AND extension = ?
            """,
            (
                int(video_id),
                int(source_stream_index),
                str(extension).lstrip(".").lower(),
            ),
        ).fetchone()
    return dict(row) if row else {}


def list_video_subtitle_assets(
    video_id: int,
    playback_only: bool = False,
) -> List[Dict]:
    clauses = ["video_id = ?"]
    params = [int(video_id)]
    if playback_only:
        clauses.append("lower(extension) = 'vtt'")
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM video_subtitle_assets
            WHERE {}
            ORDER BY default_track DESC, source_stream_index ASC, id ASC
            """.format(" AND ".join(clauses)),
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def get_subtitle_asset(asset_id: int) -> Optional[Dict]:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT a.*, v.access_level, v.missing, v.is_deleted
            FROM video_subtitle_assets a
            JOIN videos v ON v.id = a.video_id
            WHERE a.id = ?
            """,
            (int(asset_id),),
        ).fetchone()
    return dict(row) if row else None


def delete_subtitle_asset(asset_id: int) -> bool:
    with connect() as connection:
        cursor = connection.execute(
            "DELETE FROM video_subtitle_assets WHERE id = ?",
            (int(asset_id),),
        )
    return bool(cursor.rowcount)


def delete_video_subtitle_assets(video_id: int) -> int:
    with connect() as connection:
        cursor = connection.execute(
            "DELETE FROM video_subtitle_assets WHERE video_id = ?",
            (int(video_id),),
        )
    return max(0, int(cursor.rowcount))
