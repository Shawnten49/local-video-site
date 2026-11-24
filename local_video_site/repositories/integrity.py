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


def repair_video_satellite_integrity() -> Dict:
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        deleted_media = connection.execute(
            """
            DELETE FROM video_media_info
            WHERE NOT EXISTS (
                SELECT 1 FROM videos v WHERE v.id = video_media_info.video_id
            )
            """
        ).rowcount
        deleted_assets = connection.execute(
            """
            DELETE FROM video_assets
            WHERE NOT EXISTS (
                SELECT 1 FROM videos v WHERE v.id = video_assets.video_id
            )
            """
        ).rowcount
        inserted_media = connection.execute(
            """
            INSERT OR IGNORE INTO video_media_info (video_id, updated_at)
            SELECT id, updated_at FROM videos
            """
        ).rowcount
        inserted_assets = connection.execute(
            """
            INSERT OR IGNORE INTO video_assets (video_id, updated_at)
            SELECT id, updated_at FROM videos
            """
        ).rowcount
        connection.commit()
    return {
        "deleted_media": max(0, deleted_media),
        "deleted_assets": max(0, deleted_assets),
        "inserted_media": max(0, inserted_media),
        "inserted_assets": max(0, inserted_assets),
    }
def _repair_orphan_video_dependencies(
    connection: sqlite3.Connection,
) -> Dict:
    now = _now()
    task_cursor = connection.execute(
        """
        UPDATE download_tasks
        SET status = 'deleted',
            stage = 'deleted',
            delete_requested = 0,
            pause_requested = 0,
            deleted_at = ?,
            video_id = NULL,
            file_path = NULL,
            partial_path = NULL,
            checkpoint_path = NULL,
            speed_bytes_per_sec = 0,
            eta_seconds = NULL,
            message = '源视频已删除，自动清理孤立下载任务',
            updated_at = ?
        WHERE video_id IS NOT NULL
          AND deleted_at IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM videos
              WHERE videos.id = download_tasks.video_id
          )
        """,
        (now, now),
    )
    relation_count = 0
    for table in (
        "video_categories",
        "video_actors",
        "video_manual_categories",
        "favorites",
        "user_favorites",
        "category_fetch_cache",
        "playback_cache_entries",
    ):
        column = "video_id"
        cursor = connection.execute(
            """
            DELETE FROM {}
            WHERE {} IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM videos
                  WHERE videos.id = {}.{}
              )
            """.format(table, column, table, column)
        )
        relation_count += max(0, cursor.rowcount)
    cursor = connection.execute(
        """
        DELETE FROM user_sessions
        WHERE user_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM users WHERE users.id = user_sessions.user_id
          )
        """
    )
    relation_count += max(0, cursor.rowcount)
    cursor = connection.execute(
        """
        DELETE FROM video_manual_categories
        WHERE manual_category_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM manual_categories
              WHERE manual_categories.id =
                    video_manual_categories.manual_category_id
          )
        """
    )
    relation_count += max(0, cursor.rowcount)
    return {
        "download_tasks": max(0, task_cursor.rowcount),
        "relations": relation_count,
    }
def repair_orphan_video_dependencies() -> Dict:
    with connect() as connection:
        connection.execute(
            """
            UPDATE download_tasks
            SET elapsed_seconds = MAX(
                0,
                CAST(
                    (
                        julianday(
                            COALESCE(
                                last_progress_at,
                                heartbeat_at,
                                updated_at,
                                started_at
                            )
                        ) - julianday(started_at)
                    ) * 86400
                    AS INTEGER
                )
            )
            WHERE status IN ('running', 'pausing')
              AND started_at IS NOT NULL
              AND run_started_at IS NULL
              AND elapsed_seconds = 0
            """
        )
        return _repair_orphan_video_dependencies(connection)
def recover_interrupted_metadata() -> None:
    with connect() as connection:
        connection.execute(
            """
            UPDATE video_media_info SET metadata_status = 'pending'
            WHERE metadata_status = 'processing'
            """
        )
