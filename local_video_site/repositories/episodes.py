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
from .videos import (
    _ensure_video_satellites,
    video_repository,
    video_with_preview_state,
)


def create_video_episode(
    season_id: int,
    episode_number: int,
    display_title: str,
    source_url: str,
    normalized_source_url: str,
    relative_path: str,
    absolute_path: str,
    download_task_id: Optional[int],
    episode_title: Optional[str] = None,
) -> int:
    now = _now()
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO video_episodes (
                season_id, episode_number, display_title, episode_title,
                source_url,
                normalized_source_url, relative_path, absolute_path,
                download_task_id, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?)
            ON CONFLICT(season_id, episode_number) DO UPDATE SET
                display_title = excluded.display_title,
                episode_title = COALESCE(
                    excluded.episode_title,
                    video_episodes.episode_title
                ),
                source_url = excluded.source_url,
                normalized_source_url = excluded.normalized_source_url,
                relative_path = excluded.relative_path,
                absolute_path = excluded.absolute_path,
                download_task_id = COALESCE(
                    video_episodes.download_task_id,
                    excluded.download_task_id
                ),
                updated_at = excluded.updated_at
            """,
            (
                int(season_id),
                int(episode_number),
                display_title,
                episode_title,
                source_url,
                normalized_source_url,
                relative_path,
                absolute_path,
                download_task_id,
                now,
                now,
            ),
        )
        row = connection.execute(
            """
            SELECT id FROM video_episodes
            WHERE season_id = ? AND episode_number = ?
            """,
            (int(season_id), int(episode_number)),
        ).fetchone()
    return int(row["id"])
def get_video_episode_by_source_url(
    normalized_source_url: str,
) -> Optional[Dict]:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT e.*, s.name AS season_name, s.season_number,
                   sr.name AS series_name
            FROM video_episodes e
            JOIN video_seasons s ON s.id = e.season_id
            JOIN video_series sr ON sr.id = s.series_id
            WHERE e.normalized_source_url = ?
            LIMIT 1
            """,
            (normalized_source_url,),
        ).fetchone()
    return dict(row) if row else None
def set_episode_download_task(
    batch_id: str,
    normalized_source_url: str,
    download_task_id: int,
    episode_id: int,
) -> None:
    now = _now()
    with connect() as connection:
        connection.execute(
            """
            UPDATE episode_download_items
            SET download_task_id = ?, updated_at = ?
            WHERE batch_id = ? AND normalized_source_url = ?
            """,
            (
                int(download_task_id),
                now,
                batch_id,
                normalized_source_url,
            ),
        )
        connection.execute(
            """
            UPDATE video_episodes
            SET download_task_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (int(download_task_id), now, int(episode_id)),
        )
def link_episode_download_task(
    task_id: int,
    video_id: int,
    absolute_path: Path,
) -> Optional[Dict]:
    now = _now()
    with connect() as connection:
        row = connection.execute(
            """
            SELECT e.id AS episode_id, e.season_id
            FROM video_episodes e
            WHERE e.download_task_id = ? OR e.absolute_path = ?
            ORDER BY CASE WHEN e.download_task_id = ? THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (int(task_id), str(absolute_path), int(task_id)),
        ).fetchone()
        if row is None:
            return None
        connection.execute(
            """
            UPDATE video_episodes
            SET video_id = ?, download_task_id = ?,
                status = 'available', updated_at = ?
            WHERE id = ?
            """,
            (
                int(video_id),
                int(task_id),
                now,
                int(row["episode_id"]),
            ),
        )
        _apply_planned_episode_policy(
            connection,
            absolute_path,
            int(video_id),
        )
        episode = connection.execute(
            """
            SELECT e.*, s.name AS season_name, s.season_number,
                   sr.name AS series_name
            FROM video_episodes e
            JOIN video_seasons s ON s.id = e.season_id
            JOIN video_series sr ON sr.id = s.series_id
            WHERE e.id = ?
            """,
            (int(row["episode_id"]),),
        ).fetchone()
    return dict(episode) if episode else None
def attach_episode_video(
    episode_id: int,
    video_id: int,
    absolute_path: Path,
) -> None:
    with connect() as connection:
        connection.execute(
            """
            UPDATE video_episodes
            SET video_id = ?, status = 'available', updated_at = ?
            WHERE id = ?
            """,
            (int(video_id), _now(), int(episode_id)),
        )
        _apply_planned_episode_policy(
            connection,
            absolute_path,
            int(video_id),
        )
def season_context_for_video(
    video_id: int,
    max_access_level: Optional[int] = None,
) -> Optional[Dict]:
    with connect() as connection:
        season = connection.execute(
            """
            SELECT s.*,
                   COALESCE(s.display_name_override, s.name) AS season_name,
                   COALESCE(sr.display_name_override, sr.name) AS series_name,
                   sr.provider
            FROM video_episodes current
            JOIN video_seasons s ON s.id = current.season_id
            JOIN video_series sr ON sr.id = s.series_id
            WHERE current.video_id = ?
              AND s.is_deleted = 0
              AND sr.is_deleted = 0
            LIMIT 1
            """,
            (int(video_id),),
        ).fetchone()
        if season is None:
            return None
        episode_where = "e.season_id = ?"
        episode_params = [int(season["id"])]
        episode_where += """
            AND (
                NOT EXISTS (
                    SELECT 1
                    FROM episode_download_items item
                    JOIN episode_download_batches batch
                      ON batch.batch_id = item.batch_id
                    WHERE item.normalized_source_url =
                          e.normalized_source_url
                      AND batch.status = 'confirmed'
                )
                OR EXISTS (
                    SELECT 1
                    FROM episode_download_items item
                    JOIN episode_download_batches batch
                      ON batch.batch_id = item.batch_id
                    WHERE item.normalized_source_url =
                          e.normalized_source_url
                      AND item.selected = 1
                      AND batch.status = 'confirmed'
                )
            )
        """
        if max_access_level is not None:
            episode_where += " AND (v.id IS NULL OR v.access_level <= ?)"
            episode_params.append(int(max_access_level))
        episodes = connection.execute(
            """
            SELECT e.*, v.id AS resolved_video_id,
                   v.title AS video_title, v.path AS video_path,
                   v.missing AS video_missing,
                   v.is_deleted AS video_is_deleted,
                   v.storage_volume_id, v.storage_relative_path,
                   v.storage_changed_at, v.thumbnail_path,
                   v.preview_path, v.preview_status, v.preview_updated_at,
                   v.updated_at AS video_updated_at
            FROM video_episodes e
            LEFT JOIN video_catalog v ON v.id = e.video_id
            WHERE {}
            ORDER BY e.episode_number
            """.format(episode_where),
            episode_params,
        ).fetchall()
    result = dict(season)
    result["episodes"] = [dict(item) for item in episodes]
    return result
def repair_generic_episode_titles_from_paths() -> int:
    rows = []
    with connect() as connection:
        candidates = connection.execute(
            """
            SELECT id, episode_title, relative_path,
                   normalized_source_url
            FROM video_episodes
            ORDER BY id
            """
        ).fetchall()
    for row in candidates:
        current = str(row["episode_title"] or "").strip()
        if current and not is_generic_episode_title(current):
            continue
        title = meaningful_episode_title(
            Path(str(row["relative_path"] or "")).stem
        )
        if title and title != current:
            rows.append((int(row["id"]), title, current, row))
    if not rows:
        return 0
    now = _now()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for _, title, previous, row in rows:
            connection.execute(
                """
                UPDATE video_episodes
                SET episode_title = ?, updated_at = ?
                WHERE id = ?
                """,
                (title, now, int(row["id"])),
            )
            connection.execute(
                """
                UPDATE episode_download_items
                SET episode_title = ?, updated_at = ?
                WHERE normalized_source_url = ?
                  AND COALESCE(episode_title, '') = ?
                """,
                (
                    title,
                    now,
                    str(row["normalized_source_url"] or ""),
                    previous,
                ),
            )
        connection.commit()
    return len(rows)
def episode_seasons_missing_titles() -> List[Dict]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT s.id, s.name, s.source_url, sr.name AS series_name,
                   sr.provider
            FROM video_seasons s
            JOIN video_series sr ON sr.id = s.series_id
            WHERE s.is_deleted = 0
              AND sr.is_deleted = 0
              AND COALESCE(s.source_url, '') <> ''
              AND EXISTS (
                  SELECT 1 FROM video_episodes e
                  WHERE e.season_id = s.id
                    AND (
                        e.episode_title IS NULL
                        OR TRIM(e.episode_title) = ''
                        OR LOWER(TRIM(e.episode_title)) IN (
                            '正片', '正剧', 'tv', 'episode',
                            '原版', '中文版', '国语版', '粤语版'
                        )
                    )
              )
            ORDER BY s.id
            """
        ).fetchall()
    return [dict(row) for row in rows]
def update_episode_titles(
    season_id: int,
    titles: Dict[int, str],
) -> int:
    updated = 0
    now = _now()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            """
            SELECT episode_number, episode_title, relative_path
            FROM video_episodes
            WHERE season_id = ?
            """,
            (int(season_id),),
        ).fetchall()
        for row in rows:
            title = str(
                titles.get(int(row["episode_number"])) or ""
            ).strip()
            if not title:
                continue
            current = str(row["episode_title"] or "").strip()
            if current and not is_generic_episode_title(current):
                continue
            cursor = connection.execute(
                """
                UPDATE video_episodes
                SET episode_title = ?, updated_at = ?
                WHERE season_id = ? AND episode_number = ?
                """,
                (
                    title,
                    now,
                    int(season_id),
                    int(row["episode_number"]),
                ),
            )
            updated += max(0, cursor.rowcount)
            connection.execute(
                """
                UPDATE episode_download_items
                SET episode_title = ?, updated_at = ?
                WHERE relative_path = ?
                  AND (
                      episode_title IS NULL
                      OR TRIM(episode_title) = ''
                  )
                """,
                (title, now, row["relative_path"]),
            )
        connection.commit()
    return updated
def update_season_assets_from_video(video_id: int) -> None:
    with connect() as connection:
        video = connection.execute(
            """
            SELECT thumbnail_path, preview_path, preview_status
            FROM video_catalog WHERE id = ?
            """,
            (int(video_id),),
        ).fetchone()
        if video is None:
            return
        connection.execute(
            """
            UPDATE video_seasons
            SET cover_path = COALESCE(?, cover_path),
                preview_path = COALESCE(?, preview_path),
                preview_status = CASE
                    WHEN ? = 'ready' THEN 'ready'
                    ELSE preview_status
                END,
                updated_at = ?
            WHERE metadata_video_id = ?
            """,
            (
                video["thumbnail_path"],
                video["preview_path"],
                video["preview_status"],
                _now(),
                int(video_id),
            ),
        )
def _apply_planned_episode_policy(
    connection: sqlite3.Connection,
    path: Path,
    video_id: int,
) -> None:
    """Attach a planned episode to a newly indexed file and set resource policy."""

    row = connection.execute(
        """
        SELECT e.id AS episode_id, e.season_id, e.episode_number,
               e.display_title, s.representative_video_id,
               s.metadata_video_id
        FROM video_episodes e
        JOIN video_seasons s ON s.id = e.season_id
        WHERE e.absolute_path IN (?, ?)
        """,
        (
            str(path),
            str(path.expanduser().resolve()),
        ),
    ).fetchone()
    if row is None:
        return
    now = _now()
    connection.execute(
        """
        UPDATE video_episodes
        SET video_id = ?, status = 'available', updated_at = ?
        WHERE id = ?
        """,
        (int(video_id), now, int(row["episode_id"])),
    )
    representative_id = row["representative_video_id"]
    metadata_id = row["metadata_video_id"]
    if representative_id is None:
        representative_id = int(video_id)
    if int(row["episode_number"]) == 1:
        representative_id = int(video_id)
        metadata_id = int(video_id)
    if metadata_id is None:
        metadata_id = int(representative_id)
    connection.execute(
        """
        UPDATE video_seasons
        SET representative_video_id = ?,
            metadata_video_id = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            int(representative_id),
            int(metadata_id),
            now,
            int(row["season_id"]),
        ),
    )
    is_metadata_source = int(metadata_id) == int(video_id)
    connection.execute(
        """
        UPDATE videos
        SET title = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            row["display_title"],
            now,
            int(video_id),
        ),
    )
    if not is_metadata_source:
        _ensure_video_satellites(connection, int(video_id), now)
        connection.execute(
            """
            UPDATE video_media_info
            SET metadata_status = 'inherited', updated_at = ?
            WHERE video_id = ?
            """,
            (now, int(video_id)),
        )
        connection.execute(
            """
            UPDATE video_assets
            SET preview_status = 'not_required', updated_at = ?
            WHERE video_id = ?
            """,
            (now, int(video_id)),
        )
