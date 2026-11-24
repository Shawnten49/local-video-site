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


_video_with_preview_state = video_with_preview_state
_VIDEO_ORDER_BY = {
    "recent": "v.mtime_ns DESC, v.id DESC",
    "name": "v.title COLLATE NOCASE ASC, v.id ASC",
    "oldest": "v.updated_at ASC, v.id ASC",
    "duration": "v.duration IS NULL, v.duration DESC, v.id DESC",
    "size": "v.size DESC, v.id DESC",
    "favorited": (
        "(SELECT fr.created_at FROM favorites fr "
        "WHERE fr.video_id = v.id) DESC, v.mtime_ns DESC, v.id DESC"
    ),
}


def admin_video_rows(
    query: str = "",
    directory: str = "",
) -> List[Dict]:
    clauses = ["1 = 1"]
    params: List = []
    parsed_query = parse_search_query(query)
    if parsed_query.is_id:
        if parsed_query.id_value is None:
            clauses.append("0 = 1")
        else:
            clauses.append("v.id = ?")
            params.append(parsed_query.id_value)
    elif parsed_query.raw:
        query = parsed_query.raw
        clauses.append(
            """
            (
                v.title LIKE ? ESCAPE '\\'
                OR v.default_title LIKE ? ESCAPE '\\'
                OR v.path LIKE ? ESCAPE '\\'
                OR COALESCE(v.original_url, '') LIKE ? ESCAPE '\\'
            )
            """
        )
        escaped = (
            query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        params.extend([
            "%{}%".format(escaped),
            "%{}%".format(escaped),
            "%{}%".format(escaped),
            "%{}%".format(escaped),
        ])
    if directory:
        clauses.append("v.directory = ?")
        params.append(directory)
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT v.*,
                   sv.label AS storage_label,
                   sv.mount_path AS storage_mount_path,
                   sv.root_relative_path AS storage_root_relative_path,
                   sv.is_active AS storage_is_active,
                   sv.online_state AS storage_online_state,
                   sv.last_probe_at AS storage_last_probe_at,
                   sv.last_probe_error AS storage_last_probe_error,
                   (
                       SELECT j.id FROM video_format_jobs j
                       WHERE j.video_id = v.id
                         AND j.status IN ('pending', 'running')
                       ORDER BY j.id DESC LIMIT 1
                   ) AS active_format_job_id,
                   (
                       SELECT j.status FROM video_format_jobs j
                       WHERE j.video_id = v.id
                         AND j.status IN ('pending', 'running')
                       ORDER BY j.id DESC LIMIT 1
                   ) AS active_format_job_status
            FROM video_catalog v
            LEFT JOIN video_storage_volumes sv
              ON sv.id = v.storage_volume_id
            WHERE {}
            ORDER BY v.created_at DESC, v.id DESC
            """.format(" AND ".join(clauses)),
            params,
        ).fetchall()
    return [_video_with_preview_state(row) for row in rows]
def _admin_video_filters(
    query: str = "",
    record_state: str = "all",
    access_level: Optional[int] = None,
    storage_state: str = "",
) -> Tuple[List[str], List]:
    clauses = ["1 = 1"]
    params: List = []
    parsed_query = parse_search_query(query)
    if parsed_query.is_id:
        if parsed_query.id_value is None:
            clauses.append("0 = 1")
        else:
            clauses.append("v.id = ?")
            params.append(parsed_query.id_value)
    elif parsed_query.raw:
        escaped = (
            parsed_query.raw.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        clauses.append(
            """
            (
                v.title LIKE ? ESCAPE '\\'
                OR v.default_title LIKE ? ESCAPE '\\'
                OR v.path LIKE ? ESCAPE '\\'
                OR COALESCE(v.original_url, '') LIKE ? ESCAPE '\\'
            )
            """
        )
        params.extend([
            "%{}%".format(escaped),
            "%{}%".format(escaped),
            "%{}%".format(escaped),
            "%{}%".format(escaped),
        ])
    if record_state == "active":
        clauses.append("v.is_deleted = 0")
    elif record_state == "deleted":
        clauses.append("v.is_deleted = 1")
    if access_level is not None:
        clauses.append("v.access_level = ?")
        params.append(int(access_level))
    if storage_state == "external":
        clauses.append("v.storage_volume_id IS NOT NULL")
    elif storage_state == "local":
        clauses.append("v.storage_volume_id IS NULL")
    elif storage_state == "external_online":
        clauses.append("sv.online_state = 'online'")
    elif storage_state == "external_offline":
        clauses.append("sv.online_state = 'offline'")
    elif storage_state == "file_missing":
        clauses.append("v.missing = 1")
    elif storage_state == "migrating":
        clauses.append(
            """
            EXISTS (
                SELECT 1 FROM video_migrations vm
                WHERE vm.video_id = v.id
                  AND vm.status IN ('pending', 'running', 'needs_cleanup')
            )
            """
        )
    elif storage_state == "deleted":
        clauses.append("v.is_deleted = 1")
    return clauses, params
def admin_video_page(
    query: str = "",
    record_state: str = "all",
    access_level: Optional[int] = None,
    storage_state: str = "",
    page: int = 1,
    page_size: int = 50,
) -> Dict:
    page = max(1, int(page))
    page_size = max(1, min(100, int(page_size)))
    clauses, params = _admin_video_filters(
        query=query,
        record_state=record_state,
        access_level=access_level,
        storage_state=storage_state,
    )
    where = " AND ".join(clauses)
    summary_clauses, summary_params = _admin_video_filters(
        query=query,
        record_state="all",
    )
    summary_where = " AND ".join(summary_clauses)
    with connect() as connection:
        summary = connection.execute(
            """
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(CASE WHEN v.is_deleted = 0 THEN 1 ELSE 0 END), 0)
                       AS active_count,
                   COALESCE(SUM(CASE WHEN v.is_deleted = 1 THEN 1 ELSE 0 END), 0)
                       AS deleted_count
            FROM video_catalog v
            LEFT JOIN video_storage_volumes sv
              ON sv.id = v.storage_volume_id
            WHERE {}
            """.format(summary_where),
            summary_params,
        ).fetchone()
        filtered_total = int(connection.execute(
            """
            SELECT COUNT(*)
            FROM video_catalog v
            LEFT JOIN video_storage_volumes sv
              ON sv.id = v.storage_volume_id
            WHERE {}
            """.format(where),
            params,
        ).fetchone()[0])
        pages = max(1, (filtered_total + page_size - 1) // page_size)
        page = min(page, pages)
        rows = connection.execute(
            """
            SELECT v.*,
                   sv.label AS storage_label,
                   sv.mount_path AS storage_mount_path,
                   sv.root_relative_path AS storage_root_relative_path,
                   sv.is_active AS storage_is_active,
                   sv.online_state AS storage_online_state,
                   sv.last_probe_at AS storage_last_probe_at,
                   sv.last_probe_error AS storage_last_probe_error,
                   (
                       SELECT j.id FROM video_format_jobs j
                       WHERE j.video_id = v.id
                         AND j.status IN ('pending', 'running')
                       ORDER BY j.id DESC LIMIT 1
                   ) AS active_format_job_id,
                   (
                       SELECT j.status FROM video_format_jobs j
                       WHERE j.video_id = v.id
                         AND j.status IN ('pending', 'running')
                       ORDER BY j.id DESC LIMIT 1
                   ) AS active_format_job_status
            FROM video_catalog v
            LEFT JOIN video_storage_volumes sv
              ON sv.id = v.storage_volume_id
            WHERE {}
            ORDER BY v.created_at DESC, v.id DESC
            LIMIT ? OFFSET ?
            """.format(where),
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    return {
        "items": [
            _video_with_preview_state(row, include_preview_availability=False)
            for row in rows
        ],
        "total": filtered_total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "active_count": int(summary["active_count"]),
        "deleted_count": int(summary["deleted_count"]),
    }
def _video_filter(
    query: str = "",
    directory: str = "",
    category_id: Optional[int] = None,
    manual_category_id: Optional[int] = None,
    actor_id: Optional[int] = None,
    favorite_only: bool = False,
    favorite_user_id: Optional[int] = None,
    max_access_level: Optional[int] = None,
    exact_access_level: Optional[int] = None,
) -> Tuple[str, List]:
    clauses = ["v.missing = 0", "v.is_deleted = 0"]
    params: List = []
    if query:
        clauses.append(
            """
            (
                v.title LIKE ? ESCAPE '\\'
                OR v.default_title LIKE ? ESCAPE '\\'
                OR v.path LIKE ? ESCAPE '\\'
            )
            """
        )
        escaped = (
            query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        params.extend([
            "%{}%".format(escaped),
            "%{}%".format(escaped),
            "%{}%".format(escaped),
        ])
    if directory:
        clauses.append("v.directory = ?")
        params.append(directory)
    if category_id is not None:
        clauses.append(
            """
            v.id IN (
                SELECT video_id FROM video_categories
                WHERE category_id = ?
            )
            """
        )
        params.append(int(category_id))
    if manual_category_id is not None:
        clauses.append(
            """
            v.id IN (
                SELECT video_id FROM video_manual_categories
                WHERE manual_category_id = ?
            )
            """
        )
        params.append(int(manual_category_id))
    if actor_id is not None:
        clauses.append(
            """
            v.id IN (
                SELECT video_id FROM video_actors
                WHERE actor_id = ?
            )
            """
        )
        params.append(int(actor_id))
    if favorite_only:
        if favorite_user_id is None:
            clauses.append("v.id IN (SELECT video_id FROM favorites)")
        else:
            clauses.append(
                "v.id IN (SELECT video_id FROM user_favorites "
                "WHERE user_id = ?)"
            )
            params.append(int(favorite_user_id))
    if exact_access_level is not None:
        clauses.append("v.access_level = ?")
        params.append(int(exact_access_level))
    elif max_access_level is not None:
        clauses.append("v.access_level <= ?")
        params.append(int(max_access_level))
    return " AND ".join(clauses), params
def list_videos(
    query: str = "",
    directory: str = "",
    category_id: Optional[int] = None,
    manual_category_id: Optional[int] = None,
    actor_id: Optional[int] = None,
    sort: str = "recent",
    page: int = 1,
    page_size: int = 36,
    favorite_only: bool = False,
    favorite_user_id: Optional[int] = None,
    group_seasons: bool = False,
    max_access_level: Optional[int] = None,
    exact_access_level: Optional[int] = None,
) -> Dict:
    if group_seasons:
        return list_library_cards(
            query=query,
            directory=directory,
            sort=sort,
            page=page,
            page_size=page_size,
            max_access_level=max_access_level,
            exact_access_level=exact_access_level,
        )
    where, params = _video_filter(
        query=query,
        directory=directory,
        category_id=category_id,
        manual_category_id=manual_category_id,
        actor_id=actor_id,
        favorite_only=favorite_only,
        favorite_user_id=favorite_user_id,
        max_access_level=max_access_level,
        exact_access_level=exact_access_level,
    )
    if sort == "favorited" and favorite_user_id is not None:
        order_by = (
            "(SELECT fr.created_at FROM user_favorites fr "
            "WHERE fr.video_id = v.id AND fr.user_id = {}) DESC, "
            "v.mtime_ns DESC, v.id DESC"
        ).format(int(favorite_user_id))
    else:
        order_by = _VIDEO_ORDER_BY.get(sort, "v.updated_at DESC, v.id DESC")
    page = max(1, int(page))
    page_size = max(1, min(120, int(page_size)))

    with connect() as connection:
        total = connection.execute(
            "SELECT COUNT(*) FROM video_catalog v WHERE {}".format(where),
            params,
        ).fetchone()[0]
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        offset = (page - 1) * page_size
        rows = connection.execute(
            """
            SELECT v.* FROM video_catalog v
            WHERE {}
            ORDER BY {}
            LIMIT ? OFFSET ?
            """.format(where, order_by),
            [*params, page_size, offset],
        ).fetchall()
        directory_params = []
        directory_where = "missing = 0 AND is_deleted = 0"
        if exact_access_level is not None:
            directory_where += " AND access_level = ?"
            directory_params.append(int(exact_access_level))
        elif max_access_level is not None:
            directory_where += " AND access_level <= ?"
            directory_params.append(int(max_access_level))
        directories = [
            row[0]
            for row in connection.execute(
                """
                SELECT DISTINCT directory FROM videos
                WHERE {}
                ORDER BY directory COLLATE NOCASE
                """.format(directory_where),
                directory_params,
            ).fetchall()
        ]
    return {
        "items": [_video_with_preview_state(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "directories": directories,
    }
def list_library_cards(
    query: str = "",
    directory: str = "",
    sort: str = "recent",
    page: int = 1,
    page_size: int = 36,
    max_access_level: Optional[int] = None,
    exact_access_level: Optional[int] = None,
) -> Dict:
    """List physical videos and one logical card for every available season."""

    with connect() as connection:
        video_where = "missing = 0 AND is_deleted = 0"
        video_params = []
        if exact_access_level is not None:
            video_where += " AND access_level = ?"
            video_params.append(int(exact_access_level))
        elif max_access_level is not None:
            video_where += " AND access_level <= ?"
            video_params.append(int(max_access_level))
        video_rows = connection.execute(
            """
            SELECT * FROM video_catalog
            WHERE {}
            ORDER BY mtime_ns DESC, id DESC
            """.format(video_where),
            video_params,
        ).fetchall()
        episode_rows = connection.execute(
            """
            SELECT e.*,
                   COALESCE(s.display_name_override, s.name) AS season_name,
                   s.season_number,
                   s.relative_directory, s.representative_video_id,
                   COALESCE(sr.display_name_override, sr.name) AS series_name,
                   sr.provider
            FROM video_episodes e
            JOIN video_seasons s ON s.id = e.season_id
            JOIN video_series sr ON sr.id = s.series_id
            WHERE s.is_deleted = 0 AND sr.is_deleted = 0
            ORDER BY s.id, e.episode_number
            """
        ).fetchall()
    videos = {
        int(row["id"]): _video_with_preview_state(row)
        for row in video_rows
    }
    episodes_by_season: Dict[int, List[Dict]] = {}
    for row in episode_rows:
        item = dict(row)
        episodes_by_season.setdefault(int(item["season_id"]), []).append(item)

    grouped_video_ids = {
        int(row["video_id"])
        for row in episode_rows
        if row["video_id"] is not None and int(row["video_id"]) in videos
    }
    cards = [
        {**item, "card_kind": "video"}
        for video_id, item in videos.items()
        if video_id not in grouped_video_ids
    ]
    for season_id, episodes in episodes_by_season.items():
        available = [
            episode
            for episode in episodes
            if episode.get("video_id") is not None
            and int(episode["video_id"]) in videos
        ]
        if not available:
            continue
        visible_episodes = [
            episode
            for episode in episodes
            if (
                episode.get("video_id") is None
                or int(episode["video_id"]) in videos
            )
        ]
        first = visible_episodes[0]
        representative = next(
            (
                episode for episode in available
                if int(episode["episode_number"]) == 1
            ),
            available[0],
        )
        video = videos[int(representative["video_id"])]
        display_title = "{} {}".format(
            first["series_name"],
            first["season_name"],
        )
        cards.append({
            **video,
            "card_kind": "season",
            "season_id": int(season_id),
            "series_name": first["series_name"],
            "season_name": first["season_name"],
            "season_number": int(first["season_number"]),
            "display_title": display_title,
            "title": display_title,
            "directory": first["relative_directory"],
            "episode_count": len(visible_episodes),
            "available_episode_count": len(available),
            "search_text": " ".join(
                [display_title]
                + [
                    str(item.get("display_title") or "")
                    for item in visible_episodes
                ]
            ).casefold(),
        })

    parsed_query = parse_search_query(query)
    if parsed_query.is_id:
        cards = [
            card for card in cards
            if int(card["id"]) == int(parsed_query.id_value or -1)
        ]
    elif parsed_query.raw:
        needle = parsed_query.raw.casefold()
        cards = [
            card for card in cards
            if needle in str(
                card.get("search_text")
                or card.get("display_title")
                or card.get("title")
                or ""
            ).casefold()
        ]
    if directory:
        cards = [
            card for card in cards
            if str(card.get("directory") or "") == directory
        ]
    if sort == "name":
        cards.sort(
            key=lambda item: (
                str(item.get("display_title") or item.get("title") or "").casefold(),
                int(item["id"]),
            )
        )
    elif sort == "duration":
        cards.sort(key=lambda item: float(item.get("duration") or 0), reverse=True)
    elif sort == "size":
        cards.sort(key=lambda item: int(item.get("size") or 0), reverse=True)
    elif sort == "oldest":
        cards.sort(key=lambda item: str(item.get("updated_at") or ""))
    else:
        cards.sort(
            key=lambda item: (
                int(item.get("mtime_ns") or 0),
                int(item["id"]),
            ),
            reverse=True,
        )

    page = max(1, int(page))
    page_size = max(1, min(120, int(page_size)))
    total = len(cards)
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, pages)
    offset = (page - 1) * page_size
    directories = sorted(
        {
            str(item.get("directory") or "")
            for item in cards
            if item.get("directory")
        },
        key=str.casefold,
    )
    return {
        "items": cards[offset:offset + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "directories": directories,
    }
def get_video_neighbors(
    current_video_id: int,
    query: str = "",
    directory: str = "",
    category_id: Optional[int] = None,
    manual_category_id: Optional[int] = None,
    actor_id: Optional[int] = None,
    favorite_only: bool = False,
    favorite_user_id: Optional[int] = None,
    max_access_level: Optional[int] = None,
    exact_access_level: Optional[int] = None,
    sort: str = "recent",
) -> Tuple[Optional[Dict], Optional[Dict]]:
    where, params = _video_filter(
        query=query,
        directory=directory,
        category_id=category_id,
        manual_category_id=manual_category_id,
        actor_id=actor_id,
        favorite_only=favorite_only,
        favorite_user_id=favorite_user_id,
        max_access_level=max_access_level,
        exact_access_level=exact_access_level,
    )
    if sort == "favorited" and favorite_user_id is not None:
        order_by = (
            "(SELECT fr.created_at FROM user_favorites fr "
            "WHERE fr.video_id = v.id AND fr.user_id = {}) DESC, "
            "v.mtime_ns DESC, v.id DESC"
        ).format(int(favorite_user_id))
    else:
        order_by = _VIDEO_ORDER_BY.get(sort, "v.updated_at DESC, v.id DESC")
    sql = """
        WITH ordered AS (
            SELECT
                v.id,
                LAG(v.id) OVER (ORDER BY {order_by}) AS previous_id,
                LAG(v.title) OVER (ORDER BY {order_by})
                    AS previous_title,
                LEAD(v.id) OVER (ORDER BY {order_by}) AS next_id,
                LEAD(v.title) OVER (ORDER BY {order_by})
                    AS next_title
            FROM video_catalog v
            WHERE {where}
        ),
        target AS (
            SELECT previous_id, previous_title, next_id, next_title
            FROM ordered
            WHERE id = ?
        )
        SELECT
            target.previous_id,
            target.previous_title,
            target.next_id,
            target.next_title
        FROM target
    """.format(
        order_by=order_by,
        where=where,
    )
    with connect() as connection:
        row = connection.execute(
            sql,
            [*params, int(current_video_id)],
        ).fetchone()
    if row is None:
        return None, None
    previous = (
        {
            "id": row["previous_id"],
            "title": row["previous_title"],
        }
        if row["previous_id"] is not None
        else None
    )
    following = (
        {
            "id": row["next_id"],
            "title": row["next_title"],
        }
        if row["next_id"] is not None
        else None
    )
    return previous, following
def library_stats(
    max_access_level: Optional[int] = None,
    exact_access_level: Optional[int] = None,
) -> Dict:
    where = "missing = 0 AND is_deleted = 0"
    params = []
    if exact_access_level is not None:
        where += " AND access_level = ?"
        params.append(int(exact_access_level))
    elif max_access_level is not None:
        where += " AND access_level <= ?"
        params.append(int(max_access_level))
    with connect() as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(size), 0) AS total_bytes,
                SUM(CASE WHEN metadata_status = 'pending' THEN 1 ELSE 0 END)
                    AS pending
            FROM video_catalog
            WHERE {}
            """.format(where),
            params,
        ).fetchone()
    return dict(row)
def resource_stats() -> Dict:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN metadata_status = 'pending' THEN 1 ELSE 0 END)
                    AS metadata_pending,
                SUM(CASE WHEN metadata_status = 'ready' THEN 1 ELSE 0 END)
                    AS metadata_ready,
                SUM(CASE WHEN metadata_status = 'unavailable' THEN 1 ELSE 0 END)
                    AS metadata_failed,
                SUM(
                    CASE
                        WHEN thumbnail_path IS NULL OR thumbnail_path = ''
                        THEN 1 ELSE 0
                    END
                ) AS thumbnails_missing
            FROM video_catalog
            WHERE missing = 0 AND is_deleted = 0
            """
        ).fetchone()
    return {
        key: int(row[key] or 0)
        for key in (
            "total",
            "metadata_pending",
            "metadata_ready",
            "metadata_failed",
            "thumbnails_missing",
        )
    }
