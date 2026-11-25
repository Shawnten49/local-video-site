from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote

from local_video_site import database
from local_video_site.core import config
from local_video_site.repositories.categories import TV_CATEGORY_KEYS


CATEGORY_META = {
    "tv": {"title": "电视剧", "accent": "blue"},
    "movie": {"title": "电影", "accent": "amber"},
    "anime": {"title": "动漫", "accent": "magenta"},
    "documentary": {"title": "纪录片", "accent": "emerald"},
    "other": {"title": "其它", "accent": "slate"},
}


def _version(row: Dict, key: str = "cover_updated_at") -> str:
    return str(
        row.get(key)
        or row.get("updated_at")
        or row.get("mtime_ns")
        or ""
    )


def series_cover_url(row: Dict) -> str:
    path_value = str(row.get("cover_path") or "")
    if path_value and Path(path_value).is_file():
        return "/api/tv/v1/series/{}/cover?v={}".format(
            int(row["id"]),
            quote(_version(row), safe=""),
        )
    video_id = row.get("entry_video_id")
    if video_id is not None:
        return "/thumb/{}?v={}".format(
            int(video_id),
            quote(_version(row), safe=""),
        )
    return ""


def season_cover_url(row: Dict) -> str:
    path_value = str(row.get("cover_path") or "")
    if path_value and Path(path_value).is_file():
        return "/api/tv/v1/seasons/{}/cover?v={}".format(
            int(row["id"]),
            quote(_version(row), safe=""),
        )
    video_id = row.get("entry_video_id")
    if video_id is not None:
        return "/thumb/{}?v={}".format(
            int(video_id),
            quote(_version(row), safe=""),
        )
    return ""


def tv_home_categories() -> Dict:
    items = []
    with database.connect() as connection:
        for key, fallback_title in TV_CATEGORY_KEYS:
            row = connection.execute(
                """
                SELECT
                    COUNT(DISTINCT sr.id) AS series_count,
                    COUNT(DISTINCT s.id) AS season_count
                FROM video_series sr
                JOIN video_series_manual_categories smc
                  ON smc.series_id = sr.id
                JOIN manual_categories mc
                  ON mc.id = smc.manual_category_id
                LEFT JOIN video_seasons s
                  ON s.series_id = sr.id AND s.is_deleted = 0
                WHERE sr.is_deleted = 0
                  AND mc.system_key = ?
                  AND EXISTS (
                      SELECT 1
                      FROM video_seasons available_season
                      JOIN video_episodes episode
                        ON episode.season_id = available_season.id
                      JOIN video_catalog video
                        ON video.id = episode.video_id
                      WHERE available_season.series_id = sr.id
                        AND available_season.is_deleted = 0
                        AND video.missing = 0
                        AND video.is_deleted = 0
                        AND video.access_level = 0
                  )
                """,
                (key,),
            ).fetchone()
            meta = CATEGORY_META.get(key, {})
            items.append({
                "key": key,
                "title": str(meta.get("title") or fallback_title),
                "accent": str(meta.get("accent") or "slate"),
                "series_count": int(row["series_count"] or 0),
                "season_count": int(row["season_count"] or 0),
            })
    return {"categories": items}


def _series_select() -> str:
    return """
        SELECT sr.*,
               COALESCE(sr.display_name_override, sr.name) AS title,
               (
                   SELECT COUNT(*)
                   FROM video_seasons s
                   WHERE s.series_id = sr.id AND s.is_deleted = 0
               ) AS season_count,
               (
                   SELECT COUNT(*)
                   FROM video_seasons s
                   WHERE s.series_id = sr.id
                     AND s.is_deleted = 0
                     AND EXISTS (
                         SELECT 1
                         FROM video_episodes e
                         JOIN video_catalog v ON v.id = e.video_id
                         WHERE e.season_id = s.id
                           AND v.missing = 0
                           AND v.is_deleted = 0
                           AND v.access_level = 0
                     )
               ) AS available_season_count,
               (
                   SELECT COUNT(*)
                   FROM video_episodes e
                   JOIN video_seasons s ON s.id = e.season_id
                   WHERE s.series_id = sr.id AND s.is_deleted = 0
               ) AS episode_count,
               (
                   SELECT COUNT(*)
                   FROM video_episodes e
                   JOIN video_seasons s ON s.id = e.season_id
                   JOIN video_catalog v ON v.id = e.video_id
                   WHERE s.series_id = sr.id
                     AND s.is_deleted = 0
                     AND v.missing = 0
                     AND v.is_deleted = 0
                     AND v.access_level = 0
               ) AS available_episode_count,
               (
                   SELECT v.id
                   FROM video_episodes e
                   JOIN video_seasons s ON s.id = e.season_id
                   JOIN video_catalog v ON v.id = e.video_id
                   WHERE s.series_id = sr.id
                     AND s.is_deleted = 0
                     AND v.missing = 0
                     AND v.is_deleted = 0
                     AND v.access_level = 0
                   ORDER BY s.season_number, e.episode_number, v.id
                   LIMIT 1
               ) AS entry_video_id,
               (
                   SELECT GROUP_CONCAT(mc.system_key, ',')
                   FROM video_series_manual_categories smc
                   JOIN manual_categories mc
                     ON mc.id = smc.manual_category_id
                   WHERE smc.series_id = sr.id
                     AND mc.system_key IS NOT NULL
               ) AS system_category_keys
        FROM video_series sr
    """


def _series_payload(row: Dict) -> Dict:
    category_keys = [
        value
        for value in str(row.get("system_category_keys") or "").split(",")
        if value
    ]
    season_count = int(row.get("available_season_count") or 0)
    entry_video_id = row.get("entry_video_id")
    return {
        "series_id": int(row["id"]),
        "title": str(row.get("title") or row.get("name") or ""),
        "cover_url": series_cover_url(row),
        "category_keys": category_keys,
        "season_count": int(row.get("season_count") or 0),
        "available_season_count": season_count,
        "episode_count": int(row.get("episode_count") or 0),
        "available_episode_count": int(
            row.get("available_episode_count") or 0
        ),
        "single_season_entry_video_id": (
            int(entry_video_id)
            if season_count == 1 and entry_video_id is not None
            else None
        ),
    }


def list_tv_series(
    category_key: str,
    page: int = 1,
    page_size: int = 20,
) -> Dict:
    category_key = str(category_key or "").strip().lower()
    if category_key not in CATEGORY_META:
        raise ValueError("不支持的 TV 分类")
    page = max(1, int(page))
    page_size = max(1, min(60, int(page_size)))
    availability = """
        EXISTS (
            SELECT 1
            FROM video_seasons available_season
            JOIN video_episodes available_episode
              ON available_episode.season_id = available_season.id
            JOIN video_catalog available_video
              ON available_video.id = available_episode.video_id
            WHERE available_season.series_id = sr.id
              AND available_season.is_deleted = 0
              AND available_video.missing = 0
              AND available_video.is_deleted = 0
              AND available_video.access_level = 0
        )
    """
    filter_sql = """
        sr.is_deleted = 0
        AND EXISTS (
            SELECT 1
            FROM video_series_manual_categories smc
            JOIN manual_categories mc
              ON mc.id = smc.manual_category_id
            WHERE smc.series_id = sr.id AND mc.system_key = ?
        )
        AND {}
    """.format(availability)
    with database.connect() as connection:
        total = int(connection.execute(
            """
            SELECT COUNT(*)
            FROM video_series sr
            WHERE {}
            """.format(filter_sql),
            (category_key,),
        ).fetchone()[0])
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        rows = connection.execute(
            """
            {}
            WHERE {}
            ORDER BY sr.updated_at DESC, sr.id DESC
            LIMIT ? OFFSET ?
            """.format(_series_select(), filter_sql),
            (category_key, page_size, (page - 1) * page_size),
        ).fetchall()
    return {
        "category_key": category_key,
        "items": [_series_payload(dict(row)) for row in rows],
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "total": total,
        "has_next": page < pages,
    }


def get_tv_series(series_id: int) -> Optional[Dict]:
    with database.connect() as connection:
        row = connection.execute(
            """
            {}
            WHERE sr.id = ? AND sr.is_deleted = 0
            """.format(_series_select()),
            (int(series_id),),
        ).fetchone()
        if row is None:
            return None
        season_rows = connection.execute(
            """
            SELECT s.*,
                   COALESCE(s.display_name_override, s.name) AS title,
                   (
                       SELECT COUNT(*)
                       FROM video_episodes e
                       WHERE e.season_id = s.id
                   ) AS episode_count,
                   (
                       SELECT COUNT(*)
                       FROM video_episodes e
                       JOIN video_catalog v ON v.id = e.video_id
                       WHERE e.season_id = s.id
                         AND v.missing = 0
                         AND v.is_deleted = 0
                         AND v.access_level = 0
                   ) AS available_episode_count,
                   (
                       SELECT v.id
                       FROM video_episodes e
                       JOIN video_catalog v ON v.id = e.video_id
                       WHERE e.season_id = s.id
                         AND v.missing = 0
                         AND v.is_deleted = 0
                         AND v.access_level = 0
                       ORDER BY e.episode_number, v.id
                       LIMIT 1
                   ) AS entry_video_id
            FROM video_seasons s
            WHERE s.series_id = ? AND s.is_deleted = 0
            ORDER BY s.season_number, s.id
            """,
            (int(series_id),),
        ).fetchall()
    seasons = []
    for season_row in season_rows:
        item = dict(season_row)
        if item.get("entry_video_id") is None:
            continue
        seasons.append({
            "season_id": int(item["id"]),
            "title": str(item.get("title") or item.get("name") or ""),
            "season_number": int(item.get("season_number") or 0),
            "cover_url": season_cover_url(item),
            "episode_count": int(item.get("episode_count") or 0),
            "available_episode_count": int(
                item.get("available_episode_count") or 0
            ),
            "entry_video_id": int(item["entry_video_id"]),
        })
    series = _series_payload(dict(row))
    series["seasons"] = seasons
    if len(seasons) == 1:
        series["single_season_entry_video_id"] = seasons[0]["entry_video_id"]
    return series


def series_cover_editor(series_id: int) -> Optional[Dict]:
    series = get_tv_series(series_id)
    if series is None:
        return None
    with database.connect() as connection:
        season_rows = connection.execute(
            """
            SELECT s.*,
                   COALESCE(s.display_name_override, s.name) AS title
            FROM video_seasons s
            WHERE s.series_id = ? AND s.is_deleted = 0
            ORDER BY s.season_number, s.id
            """,
            (int(series_id),),
        ).fetchall()
        seasons = []
        for season_row in season_rows:
            season = dict(season_row)
            episode_rows = connection.execute(
                """
                SELECT e.id AS episode_id, e.episode_number,
                       COALESCE(e.episode_title, e.display_title)
                           AS title,
                       v.id AS video_id, v.title AS video_title,
                       v.duration
                FROM video_episodes e
                JOIN video_catalog v ON v.id = e.video_id
                WHERE e.season_id = ?
                  AND v.missing = 0
                  AND v.is_deleted = 0
                ORDER BY e.episode_number
                """,
                (int(season["id"]),),
            ).fetchall()
            representative = (
                int(episode_rows[0]["video_id"])
                if episode_rows
                else None
            )
            season_preview = dict(season)
            season_preview["entry_video_id"] = representative
            seasons.append({
                "season_id": int(season["id"]),
                "title": str(season.get("title") or season.get("name") or ""),
                "season_number": int(season.get("season_number") or 0),
                "cover_url": season_cover_url(season_preview),
                "episodes": [
                    {
                        "episode_id": int(item["episode_id"]),
                        "episode_number": int(item["episode_number"]),
                        "title": str(
                            item["title"] or item["video_title"] or ""
                        ),
                        "video_id": int(item["video_id"]),
                        "duration": (
                            float(item["duration"])
                            if item["duration"] is not None
                            else None
                        ),
                    }
                    for item in episode_rows
                ],
            })
    return {**series, "seasons": seasons}


def series_cover_file(series_id: int) -> Optional[Path]:
    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT cover_path
            FROM video_series
            WHERE id = ? AND is_deleted = 0
            """,
            (int(series_id),),
        ).fetchone()
    if not row or not row["cover_path"]:
        return None
    path = Path(str(row["cover_path"]))
    try:
        path.resolve().relative_to(config.SERIES_COVER_DIR.resolve())
    except (OSError, ValueError):
        return None
    return path if path.is_file() else None


def season_cover_file(season_id: int) -> Optional[Path]:
    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT cover_path
            FROM video_seasons
            WHERE id = ? AND is_deleted = 0
            """,
            (int(season_id),),
        ).fetchone()
    if not row or not row["cover_path"]:
        return None
    path = Path(str(row["cover_path"]))
    roots = (
        config.COVER_DIR.resolve(),
        config.THUMBNAIL_DIR.resolve(),
        config.SERIES_COVER_DIR.resolve(),
        config.SEASON_COVER_DIR.resolve(),
    )
    if not any(
        path.resolve() == root or root in path.resolve().parents
        for root in roots
    ):
        return None
    return path if path.is_file() else None
