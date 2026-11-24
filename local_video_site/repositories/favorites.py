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


def add_user_favorite(user_id: int, video_id: int) -> bool:
    with connect() as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO user_favorites (
                user_id, video_id, created_at
            )
            SELECT ?, id, ? FROM videos
            WHERE id = ? AND missing = 0 AND is_deleted = 0
            """,
            (int(user_id), _now(), int(video_id)),
        )
        return cursor.rowcount > 0
def remove_user_favorite(user_id: int, video_id: int) -> bool:
    with connect() as connection:
        cursor = connection.execute(
            """
            DELETE FROM user_favorites
            WHERE user_id = ? AND video_id = ?
            """,
            (int(user_id), int(video_id)),
        )
        return cursor.rowcount > 0
def is_user_favorite(user_id: int, video_id: int) -> bool:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT 1 FROM user_favorites
            WHERE user_id = ? AND video_id = ?
            """,
            (int(user_id), int(video_id)),
        ).fetchone()
    return row is not None
def user_favorite_count(
    user_id: int,
    max_access_level: Optional[int] = None,
) -> int:
    access_clause = ""
    params = [int(user_id)]
    if max_access_level is not None:
        access_clause = " AND v.access_level <= ?"
        params.append(int(max_access_level))
    with connect() as connection:
        return int(connection.execute(
            """
            SELECT COUNT(*)
            FROM user_favorites f
            JOIN videos v ON v.id = f.video_id
            WHERE f.user_id = ?
              AND v.missing = 0 AND v.is_deleted = 0
              {}
            """.format(access_clause),
            params,
        ).fetchone()[0])
def migrate_legacy_favorites(user_id: int) -> int:
    with connect() as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO user_favorites (
                user_id, video_id, created_at
            )
            SELECT ?, video_id, created_at FROM favorites
            """,
            (int(user_id),),
        )
        return max(0, cursor.rowcount)
def add_favorite(video_id: int) -> bool:
    with connect() as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO favorites (video_id, created_at)
            SELECT id, ? FROM videos
            WHERE id = ? AND missing = 0 AND is_deleted = 0
            """,
            (_now(), int(video_id)),
        )
        return cursor.rowcount > 0
def remove_favorite(video_id: int) -> bool:
    with connect() as connection:
        cursor = connection.execute(
            "DELETE FROM favorites WHERE video_id = ?",
            (int(video_id),),
        )
        return cursor.rowcount > 0
def is_favorite(video_id: int) -> bool:
    with connect() as connection:
        row = connection.execute(
            "SELECT 1 FROM favorites WHERE video_id = ?",
            (int(video_id),),
        ).fetchone()
        return row is not None
def favorite_count() -> int:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM favorites f
            JOIN videos v ON v.id = f.video_id
            WHERE v.missing = 0 AND v.is_deleted = 0
            """
        ).fetchone()
        return int(row[0])
