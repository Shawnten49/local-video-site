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
from ..db.schema import ACTOR_METADATA_VERSION, CATEGORY_PARSER_VERSION
from .actors import _actor_with_avatar_state
from .videos import video_repository, video_with_preview_state


_video_with_preview_state = video_with_preview_state

TV_CATEGORY_KEYS = (
    ("tv", "电视剧"),
    ("movie", "电影"),
    ("anime", "动漫"),
    ("documentary", "纪录片"),
    ("other", "其它"),
)


def category_fetch_cache_map() -> Dict[int, Dict]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM category_fetch_cache"
        ).fetchall()
    result = {}
    for row in rows:
        item = dict(row)
        raw = item.get("categories_json")
        try:
            item["categories"] = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            item["categories"] = []
        actors_raw = item.get("actors_json")
        try:
            item["actors"] = json.loads(actors_raw) if actors_raw else []
        except (TypeError, ValueError):
            item["actors"] = []
        result[int(item["video_id"])] = item
    return result
def save_category_fetch_cache(
    video_id: int,
    source_url: str,
    status: str,
    categories: Optional[List[Dict]] = None,
    actors: Optional[List[Dict]] = None,
    http_status: Optional[int] = None,
    error: Optional[str] = None,
    parser_version: int = CATEGORY_PARSER_VERSION,
    actor_metadata_version: int = ACTOR_METADATA_VERSION,
    metadata_language: str = "",
) -> None:
    with connect() as connection:
        previous = connection.execute(
            "SELECT source_url, attempts FROM category_fetch_cache "
            "WHERE video_id = ?",
            (int(video_id),),
        ).fetchone()
        attempts = 1
        if previous and previous["source_url"] == source_url:
            attempts = int(previous["attempts"] or 0) + 1
        payload = json.dumps(
            categories or [],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        actors_payload = json.dumps(
            actors or [],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        connection.execute(
            """
            INSERT INTO category_fetch_cache (
                video_id, source_url, status, categories_json, actors_json,
                parser_version, actor_metadata_version,
                metadata_language, fetched_at, attempts,
                http_status, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                source_url = excluded.source_url,
                status = excluded.status,
                categories_json = excluded.categories_json,
                actors_json = excluded.actors_json,
                parser_version = excluded.parser_version,
                actor_metadata_version = excluded.actor_metadata_version,
                metadata_language = excluded.metadata_language,
                fetched_at = excluded.fetched_at,
                attempts = excluded.attempts,
                http_status = excluded.http_status,
                error = excluded.error
            """,
            (
                int(video_id),
                source_url,
                status,
                payload if (categories or status == "fetched") else None,
                actors_payload if (actors or status == "fetched") else None,
                int(parser_version),
                int(actor_metadata_version),
                str(metadata_language or "").strip().lower(),
                _now(),
                attempts,
                http_status,
                error,
            ),
        )
def clear_category_fetch_cache() -> None:
    with connect() as connection:
        connection.execute("DELETE FROM category_fetch_cache")
def list_categories(
    query: str = "",
    page: int = 1,
    page_size: int = 160,
) -> Dict:
    clauses = ["v.missing = 0", "v.is_deleted = 0"]
    params: List = []
    if query:
        escaped = (
            query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        clauses.append("c.normalized_name LIKE ? ESCAPE '\\'")
        params.append("%{}%".format(escaped.lower()))
    where = " AND ".join(clauses)
    page = max(1, int(page))
    page_size = max(1, min(500, int(page_size)))
    with connect() as connection:
        total = connection.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT c.id
                FROM categories c
                JOIN video_categories vc ON vc.category_id = c.id
                JOIN videos v ON v.id = vc.video_id
                WHERE {}
                GROUP BY c.id
            )
            """.format(where),
            params,
        ).fetchone()[0]
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        offset = (page - 1) * page_size
        rows = connection.execute(
            """
            SELECT c.id, c.name, c.normalized_name,
                   COUNT(DISTINCT vc.video_id) AS video_count
            FROM categories c
            JOIN video_categories vc ON vc.category_id = c.id
            JOIN videos v ON v.id = vc.video_id
            WHERE {}
            GROUP BY c.id
            ORDER BY video_count DESC, c.normalized_name COLLATE NOCASE ASC
            LIMIT ? OFFSET ?
            """.format(where),
            [*params, page_size, offset],
        ).fetchall()
    return {
        "items": [dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }
def get_category(category_id: int) -> Optional[Dict]:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT c.id, c.name, c.normalized_name,
                   COUNT(DISTINCT vc.video_id) AS video_count
            FROM categories c
            JOIN video_categories vc ON vc.category_id = c.id
            JOIN videos v ON v.id = vc.video_id
            WHERE c.id = ?
              AND v.missing = 0
              AND v.is_deleted = 0
            GROUP BY c.id
            """,
            (int(category_id),),
        ).fetchone()
        return _video_with_preview_state(row) if row else None
def _normalize_manual_category_name(value: str) -> str:
    name = " ".join(
        unicodedata.normalize("NFKC", str(value or "")).split()
    ).strip()
    if not name:
        raise ValueError("人工标签名称不能为空")
    if len(name) > 50:
        raise ValueError("人工标签名称不能超过 50 个字符")
    return name
def list_manual_categories(query: str = "") -> List[Dict]:
    params: List = []
    query_clause = ""
    if query:
        escaped = (
            query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        query_clause = "AND mc.normalized_name LIKE ? ESCAPE '\\'"
        params.append("%{}%".format(escaped.casefold()))
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT mc.id, mc.name, mc.normalized_name,
                   mc.system_key, mc.is_system,
                   COUNT(DISTINCT v.id) AS video_count,
                   COUNT(DISTINCT smc.series_id) AS series_count
            FROM manual_categories mc
            LEFT JOIN video_manual_categories vmc
                ON vmc.manual_category_id = mc.id
            LEFT JOIN videos v
                ON v.id = vmc.video_id
               AND v.missing = 0
               AND v.is_deleted = 0
            LEFT JOIN video_series_manual_categories smc
                ON smc.manual_category_id = mc.id
            WHERE 1 = 1 {}
            GROUP BY mc.id
            ORDER BY mc.is_system DESC, video_count DESC,
                     mc.normalized_name COLLATE NOCASE ASC
            """.format(query_clause),
            params,
        ).fetchall()
    return [dict(row) for row in rows]
def get_manual_category(manual_category_id: int) -> Optional[Dict]:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT mc.id, mc.name, mc.normalized_name,
                   mc.system_key, mc.is_system,
                   COUNT(DISTINCT v.id) AS video_count,
                   COUNT(DISTINCT smc.series_id) AS series_count
            FROM manual_categories mc
            LEFT JOIN video_manual_categories vmc
                ON vmc.manual_category_id = mc.id
            LEFT JOIN videos v
                ON v.id = vmc.video_id
               AND v.missing = 0
               AND v.is_deleted = 0
            LEFT JOIN video_series_manual_categories smc
                ON smc.manual_category_id = mc.id
            WHERE mc.id = ?
            GROUP BY mc.id
            """,
            (int(manual_category_id),),
        ).fetchone()
    return dict(row) if row else None
def create_manual_category(name: str) -> Dict:
    normalized = _normalize_manual_category_name(name)
    display_name = " ".join(
        unicodedata.normalize("NFKC", str(name or "")).split()
    ).strip()
    now = _now()
    with connect() as connection:
        existing = connection.execute(
            "SELECT * FROM manual_categories WHERE normalized_name = ?",
            (normalized.casefold(),),
        ).fetchone()
        if existing:
            raise ValueError("该人工标签已经存在")
        cursor = connection.execute(
            """
            INSERT INTO manual_categories (
                name, normalized_name, created_at, updated_at
            ) VALUES (?, ?, ?, ?)
            """,
            (display_name, normalized.casefold(), now, now),
        )
        manual_category_id = int(cursor.lastrowid or 0)
    category = get_manual_category(manual_category_id)
    if category is None:
        raise RuntimeError("Manual category was not persisted")
    return category
def delete_manual_category(manual_category_id: int) -> Optional[Dict]:
    """Delete only through an explicit user action; never call from cleanup."""

    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM manual_categories WHERE id = ?",
            (int(manual_category_id),),
        ).fetchone()
        if row is None:
            connection.rollback()
            return None
        if int(row["is_system"] or 0):
            connection.rollback()
            raise ValueError("TV 首页固定分类不能删除")
        connection.execute(
            """
            DELETE FROM video_manual_categories
            WHERE manual_category_id = ?
            """,
            (int(manual_category_id),),
        )
        connection.execute(
            """
            DELETE FROM video_series_manual_categories
            WHERE manual_category_id = ?
            """,
            (int(manual_category_id),),
        )
        connection.execute(
            "DELETE FROM manual_categories WHERE id = ?",
            (int(manual_category_id),),
        )
        connection.commit()
        return dict(row)


def manual_categories_for_series(series_id: int) -> List[Dict]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT mc.id, mc.name, mc.normalized_name,
                   mc.system_key, mc.is_system
            FROM video_series_manual_categories smc
            JOIN manual_categories mc
                ON mc.id = smc.manual_category_id
            JOIN video_series sr
                ON sr.id = smc.series_id
            WHERE smc.series_id = ?
              AND sr.is_deleted = 0
            ORDER BY mc.is_system DESC,
                     mc.normalized_name COLLATE NOCASE ASC
            """,
            (int(series_id),),
        ).fetchall()
    return [dict(row) for row in rows]


def set_series_manual_categories(
    series_id: int,
    manual_category_ids: List[int],
    expected_updated_at: str = "",
) -> List[Dict]:
    ids = sorted({int(value) for value in manual_category_ids})
    now = _now()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        series = connection.execute(
            """
            SELECT id, updated_at
            FROM video_series
            WHERE id = ? AND is_deleted = 0
            """,
            (int(series_id),),
        ).fetchone()
        if series is None:
            connection.rollback()
            raise ValueError("视频集不存在")
        if (
            expected_updated_at
            and str(series["updated_at"]) != str(expected_updated_at)
        ):
            connection.rollback()
            raise ValueError("视频集已被其他操作修改，请刷新后重试")
        if ids:
            placeholders = ",".join("?" for _ in ids)
            found = connection.execute(
                """
                SELECT id FROM manual_categories
                WHERE id IN ({})
                """.format(placeholders),
                ids,
            ).fetchall()
            if {int(row["id"]) for row in found} != set(ids):
                connection.rollback()
                raise ValueError("部分人工标签不存在")
        connection.execute(
            """
            DELETE FROM video_series_manual_categories
            WHERE series_id = ?
            """,
            (int(series_id),),
        )
        for manual_category_id in ids:
            connection.execute(
                """
                INSERT INTO video_series_manual_categories (
                    series_id, manual_category_id, created_at
                ) VALUES (?, ?, ?)
                """,
                (int(series_id), manual_category_id, now),
            )
        connection.execute(
            """
            UPDATE video_series
            SET updated_at = ?
            WHERE id = ?
            """,
            (now, int(series_id)),
        )
        connection.commit()
    return manual_categories_for_series(series_id)
def manual_categories_for_video(video_id: int) -> List[Dict]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT mc.id, mc.name, mc.normalized_name
            FROM video_manual_categories vmc
            JOIN manual_categories mc
                ON mc.id = vmc.manual_category_id
            JOIN videos v
                ON v.id = vmc.video_id
            WHERE vmc.video_id = ?
              AND v.missing = 0
              AND v.is_deleted = 0
            ORDER BY mc.normalized_name COLLATE NOCASE ASC
            """,
            (int(video_id),),
        ).fetchall()
    return [dict(row) for row in rows]
def set_video_manual_categories(
    video_id: int,
    manual_category_ids: List[int],
) -> List[Dict]:
    ids = sorted({int(value) for value in manual_category_ids})
    now = _now()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        video = connection.execute(
            "SELECT id FROM videos "
            "WHERE id = ? AND missing = 0 AND is_deleted = 0",
            (int(video_id),),
        ).fetchone()
        if video is None:
            connection.rollback()
            raise ValueError("视频不存在")
        if ids:
            placeholders = ",".join("?" for _ in ids)
            found = connection.execute(
                """
                SELECT id FROM manual_categories
                WHERE id IN ({})
                """.format(placeholders),
                ids,
            ).fetchall()
            found_ids = {int(row["id"]) for row in found}
            if found_ids != set(ids):
                connection.rollback()
                raise ValueError("部分人工标签不存在")
        connection.execute(
            "DELETE FROM video_manual_categories WHERE video_id = ?",
            (int(video_id),),
        )
        for manual_category_id in ids:
            connection.execute(
                """
                INSERT INTO video_manual_categories (
                    video_id, manual_category_id, created_at
                ) VALUES (?, ?, ?)
                """,
                (int(video_id), manual_category_id, now),
            )
        connection.commit()
    return manual_categories_for_video(video_id)
def categories_for_video(video_id: int) -> List[Dict]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT
                c.id,
                c.name,
                c.normalized_name,
                c.external_slug,
                c.external_type,
                c.source_domain,
                vc.source_kind
            FROM video_categories vc
            JOIN categories c ON c.id = vc.category_id
            JOIN videos v ON v.id = vc.video_id
            WHERE vc.video_id = ?
              AND v.missing = 0
              AND v.is_deleted = 0
            ORDER BY c.normalized_name COLLATE NOCASE ASC
            """,
            (int(video_id),),
        ).fetchall()
    return [dict(row) for row in rows]
def actors_for_video(video_id: int) -> List[Dict]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT
                a.id,
                a.name,
                a.normalized_name,
                a.external_id,
                a.external_slug,
                a.source_domain,
                a.profile_url,
                a.avatar_path,
                a.avatar_status,
                a.avatar_fetched_at
            FROM video_actors va
            JOIN actors a ON a.id = va.actor_id
            JOIN videos v ON v.id = va.video_id
            WHERE va.video_id = ?
              AND v.missing = 0
              AND v.is_deleted = 0
            ORDER BY a.normalized_name COLLATE NOCASE ASC
            """,
            (int(video_id),),
        ).fetchall()
    return [_actor_with_avatar_state(row) for row in rows]
