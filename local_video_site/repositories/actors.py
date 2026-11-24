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


def _actor_with_avatar_state(row) -> Dict:
    item = dict(row)
    avatar_path = str(item.get("avatar_path") or "").strip()
    item["avatar_available"] = bool(
        avatar_path
        and (config.ACTOR_AVATAR_DIR / Path(avatar_path).name).is_file()
    )
    return item
def list_actors(
    query: str = "",
    page: int = 1,
    page_size: int = 120,
) -> Dict:
    clauses = ["v.missing = 0", "v.is_deleted = 0"]
    params: List = []
    if query:
        escaped = (
            query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        clauses.append("a.normalized_name LIKE ? ESCAPE '\\'")
        params.append("%{}%".format(escaped.lower()))
    where = " AND ".join(clauses)
    page = max(1, int(page))
    page_size = max(1, min(500, int(page_size)))
    with connect() as connection:
        total = connection.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT a.id
                FROM actors a
                JOIN video_actors va ON va.actor_id = a.id
                JOIN videos v ON v.id = va.video_id
                WHERE {}
                GROUP BY a.id
            )
            """.format(where),
            params,
        ).fetchone()[0]
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        rows = connection.execute(
            """
            SELECT a.id, a.name, a.normalized_name, a.source_domain,
                   a.external_id, a.avatar_path, a.avatar_status,
                   a.avatar_fetched_at,
                   COUNT(DISTINCT va.video_id) AS video_count
            FROM actors a
            JOIN video_actors va ON va.actor_id = a.id
            JOIN videos v ON v.id = va.video_id
            WHERE {}
            GROUP BY a.id
            ORDER BY video_count DESC, a.normalized_name COLLATE NOCASE ASC
            LIMIT ? OFFSET ?
            """.format(where),
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    return {
        "items": [_actor_with_avatar_state(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }
def get_actor(actor_id: int) -> Optional[Dict]:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT a.id, a.name, a.normalized_name, a.external_id,
                   a.external_slug, a.source_domain, a.profile_url,
                   a.avatar_source_url, a.avatar_path, a.avatar_status,
                   a.avatar_error, a.avatar_fetched_at, a.avatar_attempts,
                   COUNT(DISTINCT va.video_id) AS video_count
            FROM actors a
            JOIN video_actors va ON va.actor_id = a.id
            JOIN videos v ON v.id = va.video_id
            WHERE a.id = ?
              AND v.missing = 0
              AND v.is_deleted = 0
            GROUP BY a.id
            """,
            (int(actor_id),),
        ).fetchone()
    return _actor_with_avatar_state(row) if row else None
def list_actor_avatar_jobs(actor_id: Optional[int] = None) -> List[Dict]:
    clauses = ["v.missing = 0", "v.is_deleted = 0"]
    params: List = []
    if actor_id is not None:
        clauses.append("a.id = ?")
        params.append(int(actor_id))
    where = " AND ".join(clauses)
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT a.id, a.name, a.normalized_name, a.external_id,
                   a.source_domain, a.profile_url, a.avatar_source_url,
                   a.avatar_path, a.avatar_status, a.avatar_error,
                   a.avatar_fetched_at, a.avatar_attempts, a.updated_at,
                   COUNT(DISTINCT va.video_id) AS video_count
            FROM actors a
            JOIN video_actors va ON va.actor_id = a.id
            JOIN videos v ON v.id = va.video_id
            WHERE {}
            GROUP BY a.id
            ORDER BY video_count DESC, a.normalized_name COLLATE NOCASE ASC
            """.format(where),
            params,
        ).fetchall()
    return [dict(row) for row in rows]
def get_actor_avatar_file(actor_id: int) -> Optional[Dict]:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT id, name, avatar_path, avatar_source_url,
                   avatar_fetched_at
            FROM actors
            WHERE id = ?
            """,
            (int(actor_id),),
        ).fetchone()
    if not row or not row["avatar_path"]:
        return None
    path = config.ACTOR_AVATAR_DIR / Path(row["avatar_path"]).name
    if not path.is_file():
        return None
    item = dict(row)
    item["path"] = path
    return item
def save_actor_metadata(
    actor_id: int,
    external_id: Optional[int] = None,
    avatar_source_url: Optional[str] = None,
) -> None:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT avatar_source_url, avatar_status, avatar_error
            FROM actors
            WHERE id = ?
            """,
            (int(actor_id),),
        ).fetchone()
        if not row:
            return
        source = str(avatar_source_url or "").strip() or None
        source_changed = bool(
            source and source != (row["avatar_source_url"] or "")
        )
        connection.execute(
            """
            UPDATE actors
            SET external_id = COALESCE(?, external_id),
                avatar_source_url = COALESCE(?, avatar_source_url),
                avatar_status = CASE
                    WHEN ? THEN 'pending'
                    ELSE avatar_status
                END,
                avatar_error = CASE
                    WHEN ? THEN NULL
                    ELSE avatar_error
                END,
                updated_at = ?
            WHERE id = ?
            """,
            (
                int(external_id) if external_id is not None else None,
                source,
                1 if source_changed else 0,
                1 if source_changed else 0,
                _now(),
                int(actor_id),
            ),
        )
def save_actor_avatar_result(
    actor_id: int,
    status: str,
    avatar_path: Optional[str] = None,
    error: Optional[str] = None,
    attempts: int = 0,
) -> None:
    now = _now()
    with connect() as connection:
        connection.execute(
            """
            UPDATE actors
            SET avatar_path = COALESCE(?, avatar_path),
                avatar_status = ?,
                avatar_error = ?,
                avatar_fetched_at = CASE
                    WHEN ? = 'fetched' THEN ?
                    ELSE avatar_fetched_at
                END,
                avatar_attempts = avatar_attempts + ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                avatar_path,
                status,
                error,
                status,
                now,
                max(0, int(attempts)),
                now,
                int(actor_id),
            ),
        )
def actor_count() -> int:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT a.id
                FROM actors a
                JOIN video_actors va ON va.actor_id = a.id
                JOIN videos v ON v.id = va.video_id
                WHERE v.missing = 0 AND v.is_deleted = 0
                GROUP BY a.id
            )
            """
        ).fetchone()
    return int(row[0])
