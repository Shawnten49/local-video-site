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


def _repair_storage_paths_relative_to_mount(
    connection: sqlite3.Connection,
) -> int:
    """Normalize legacy per-target volume roots to physical mount roots."""

    volumes = connection.execute(
        """
        SELECT * FROM video_storage_volumes
        WHERE COALESCE(root_relative_path, '') <> ''
        """
    ).fetchall()
    repaired = 0
    for volume in volumes:
        mount_path = Path(volume["mount_path"]).expanduser().resolve()
        old_root = str(volume["root_relative_path"] or "")
        migration_rows = connection.execute(
            """
            SELECT video_id, destination_path
            FROM video_migrations
            WHERE destination_path IS NOT NULL
              AND status = 'success'
              AND direction = 'to_external'
            ORDER BY id ASC
            """
        ).fetchall()
        migrations = {
            int(row["video_id"]): Path(row["destination_path"])
            for row in migration_rows
        }
        videos = connection.execute(
            """
            SELECT id, path, storage_relative_path
            FROM videos
            WHERE storage_volume_id = ?
            """,
            (int(volume["id"]),),
        ).fetchall()
        now = _now()
        for video in videos:
            current = Path(str(video["path"] or ""))
            migrated = migrations.get(int(video["id"]))
            candidates = [
                candidate
                for candidate in (current, migrated)
                if candidate is not None
            ]

            def within_mount(candidate: Path) -> bool:
                try:
                    candidate.expanduser().resolve().relative_to(mount_path)
                    return True
                except (OSError, ValueError):
                    return False

            chosen = next(
                (
                    candidate
                    for candidate in candidates
                    if within_mount(candidate)
                    and candidate.expanduser().resolve().is_file()
                ),
                None,
            )
            if chosen is None:
                chosen = next(
                    (
                        candidate
                        for candidate in (
                            migrated,
                            current,
                        )
                        if candidate is not None and within_mount(candidate)
                    ),
                    None,
                )
            if chosen is None:
                legacy_relative = str(
                    video["storage_relative_path"] or ""
                )
                chosen = mount_path / old_root / legacy_relative
            try:
                relative = chosen.expanduser().resolve().relative_to(
                    mount_path
                )
            except (OSError, ValueError):
                continue
            if not relative.parts or ".." in relative.parts:
                continue
            absolute = mount_path / relative
            connection.execute(
                """
                UPDATE videos
                SET path = ?, storage_relative_path = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    str(absolute),
                    str(relative),
                    now,
                    int(video["id"]),
                ),
            )
            connection.execute(
                """
                UPDATE video_episodes
                SET absolute_path = ?, storage_relative_path = ?,
                    updated_at = ?
                WHERE video_id = ?
                """,
                (
                    str(absolute),
                    str(relative),
                    now,
                    int(video["id"]),
                ),
            )
            connection.execute(
                """
                UPDATE download_tasks
                SET file_path = ?, updated_at = ?
                WHERE video_id = ? AND deleted_at IS NULL
                """,
                (
                    str(absolute),
                    now,
                    int(video["id"]),
                ),
            )
            repaired += 1
        connection.execute(
            """
            UPDATE video_storage_volumes
            SET root_relative_path = '', updated_at = ?
            WHERE id = ?
            """,
            (now, int(volume["id"])),
        )
    return repaired
def get_storage_volume(volume_id: int) -> Optional[Dict]:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM video_storage_volumes WHERE id = ?",
            (int(volume_id),),
        ).fetchone()
        return dict(row) if row else None
def list_storage_volumes() -> List[Dict]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM video_storage_volumes
            WHERE is_active = 1
            ORDER BY id ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]
def has_active_migration(video_id: int) -> bool:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT 1 FROM video_migrations
            WHERE video_id = ?
              AND status IN ('pending', 'running', 'needs_cleanup')
            LIMIT 1
            """,
            (int(video_id),),
        ).fetchone()
        return row is not None
