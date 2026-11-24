from __future__ import annotations

import unicodedata
from typing import Dict, Optional

from local_video_site import database
from local_video_site.episodes.naming import is_generic_episode_title
from local_video_site.core.search import parse_search_query


class SeriesEditError(RuntimeError):
    def __init__(self, message: str, code: str = "series_edit_failed"):
        self.code = code
        super().__init__(message)


class EpisodeEditError(RuntimeError):
    def __init__(self, message: str, code: str = "episode_edit_failed"):
        self.code = code
        super().__init__(message)


def _page(query: str, page: int, page_size: int) -> tuple:
    return max(1, int(page)), max(1, min(100, int(page_size)))


def list_series(
    query: str = "",
    page: int = 1,
    page_size: int = 30,
) -> Dict:
    clauses = ["sr.is_deleted = 0"]
    params = []
    parsed = parse_search_query(query)
    if parsed.is_id:
        if parsed.id_value is None:
            clauses.append("0 = 1")
        else:
            clauses.append("sr.id = ?")
            params.append(parsed.id_value)
    elif parsed.raw:
        escaped = (
            parsed.raw.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        clauses.append(
            """
            (
                sr.name LIKE ? ESCAPE '\\'
                OR COALESCE(sr.display_name_override, '') LIKE ? ESCAPE '\\'
                OR sr.provider LIKE ? ESCAPE '\\'
                OR COALESCE(sr.source_url, '') LIKE ? ESCAPE '\\'
            )
            """
        )
        like = "%{}%".format(escaped)
        params.extend([like, like, like, like])
    page, page_size = _page(query, page, page_size)
    where = " AND ".join(clauses)
    with database.connect() as connection:
        total = connection.execute(
            "SELECT COUNT(*) FROM video_series sr WHERE {}".format(where),
            params,
        ).fetchone()[0]
        pages = max(1, (int(total) + page_size - 1) // page_size)
        page = min(page, pages)
        rows = connection.execute(
            """
            SELECT sr.*,
                   COALESCE(sr.display_name_override, sr.name) AS display_name,
                   (
                       SELECT COUNT(*) FROM video_seasons s
                       WHERE s.series_id = sr.id AND s.is_deleted = 0
                   ) AS season_count,
                   (
                       SELECT COUNT(*) FROM video_episodes e
                       JOIN video_seasons s ON s.id = e.season_id
                       WHERE s.series_id = sr.id AND s.is_deleted = 0
                   ) AS episode_count,
                   (
                       SELECT COUNT(*) FROM video_episodes e
                       JOIN video_seasons s ON s.id = e.season_id
                       JOIN videos v ON v.id = e.video_id
                       WHERE s.series_id = sr.id
                         AND s.is_deleted = 0
                         AND v.is_deleted = 0
                   ) AS video_count,
                   (
                       SELECT COALESCE(SUM(v.size), 0)
                       FROM video_episodes e
                       JOIN video_seasons s ON s.id = e.season_id
                       JOIN videos v ON v.id = e.video_id
                       WHERE s.series_id = sr.id
                         AND s.is_deleted = 0
                         AND v.is_deleted = 0
                   ) AS video_bytes,
                   (
                       SELECT j.id FROM video_delete_jobs j
                       WHERE j.scope_type = 'series'
                         AND j.target_id = sr.id
                         AND j.status IN ('pending', 'running', 'blocked')
                       ORDER BY j.id DESC LIMIT 1
                   ) AS active_delete_job_id
            FROM video_series sr
            WHERE {}
            ORDER BY COALESCE(
                sr.display_name_override, sr.name
            ) COLLATE NOCASE, sr.id DESC
            LIMIT ? OFFSET ?
            """.format(where),
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    return {
        "items": [dict(row) for row in rows],
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


def get_series(series_id: int) -> Optional[Dict]:
    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT sr.*,
                   COALESCE(sr.display_name_override, sr.name) AS display_name,
                   (
                       SELECT COUNT(*) FROM video_seasons s
                       WHERE s.series_id = sr.id AND s.is_deleted = 0
                   ) AS season_count,
                   (
                       SELECT COUNT(*) FROM video_episodes e
                       JOIN video_seasons s ON s.id = e.season_id
                       JOIN videos v ON v.id = e.video_id
                       WHERE s.series_id = sr.id
                         AND s.is_deleted = 0
                         AND v.is_deleted = 0
                   ) AS video_count,
                   (
                       SELECT COALESCE(SUM(v.size), 0)
                       FROM video_episodes e
                       JOIN video_seasons s ON s.id = e.season_id
                       JOIN videos v ON v.id = e.video_id
                       WHERE s.series_id = sr.id
                         AND s.is_deleted = 0
                         AND v.is_deleted = 0
                   ) AS video_bytes,
                   (
                       SELECT j.id FROM video_delete_jobs j
                       WHERE j.scope_type = 'series'
                         AND j.target_id = sr.id
                         AND j.status IN ('pending', 'running', 'blocked')
                       ORDER BY j.id DESC LIMIT 1
                   ) AS active_delete_job_id
            FROM video_series sr
            WHERE sr.id = ? AND sr.is_deleted = 0
            """,
            (int(series_id),),
        ).fetchone()
    return dict(row) if row else None


def list_seasons(
    series_id: int,
    query: str = "",
    page: int = 1,
    page_size: int = 30,
) -> Dict:
    clauses = ["s.series_id = ?", "s.is_deleted = 0"]
    params = [int(series_id)]
    parsed = parse_search_query(query)
    if parsed.is_id:
        if parsed.id_value is None:
            clauses.append("0 = 1")
        else:
            clauses.append("s.id = ?")
            params.append(parsed.id_value)
    elif parsed.raw:
        escaped = (
            parsed.raw.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        clauses.append(
            "s.name LIKE ? ESCAPE '\\' "
            "OR COALESCE(s.display_name_override, '') LIKE ? ESCAPE '\\' "
            "OR s.relative_directory LIKE ? ESCAPE '\\'"
        )
        like = "%{}%".format(escaped)
        params.extend([like, like, like])
    page, page_size = _page(query, page, page_size)
    where = " AND ".join(clauses)
    with database.connect() as connection:
        total = connection.execute(
            "SELECT COUNT(*) FROM video_seasons s WHERE {}".format(where),
            params,
        ).fetchone()[0]
        pages = max(1, (int(total) + page_size - 1) // page_size)
        page = min(page, pages)
        rows = connection.execute(
            """
            SELECT s.*,
                   COALESCE(s.display_name_override, s.name) AS display_name,
                   (
                       SELECT COUNT(*) FROM video_episodes e
                       WHERE e.season_id = s.id
                   ) AS episode_count,
                   (
                       SELECT COUNT(*) FROM video_episodes e
                       JOIN videos v ON v.id = e.video_id
                       WHERE e.season_id = s.id AND v.is_deleted = 0
                   ) AS video_count,
                   (
                       SELECT COALESCE(SUM(v.size), 0)
                       FROM video_episodes e
                       JOIN videos v ON v.id = e.video_id
                       WHERE e.season_id = s.id AND v.is_deleted = 0
                   ) AS video_bytes,
                   (
                       SELECT j.id FROM video_delete_jobs j
                       WHERE j.scope_type = 'season'
                         AND j.target_id = s.id
                         AND j.status IN ('pending', 'running', 'blocked')
                       ORDER BY j.id DESC LIMIT 1
                   ) AS active_delete_job_id
            FROM video_seasons s
            WHERE {}
            ORDER BY s.season_number, s.id
            LIMIT ? OFFSET ?
            """.format(where),
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    return {
        "items": [dict(row) for row in rows],
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


def get_season(series_id: int, season_id: int) -> Optional[Dict]:
    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT s.*,
                   COALESCE(s.display_name_override, s.name) AS display_name,
                   COALESCE(sr.display_name_override, sr.name) AS series_name,
                   sr.provider,
                   (
                       SELECT j.id FROM video_delete_jobs j
                       WHERE j.scope_type = 'season'
                         AND j.target_id = s.id
                         AND j.status IN ('pending', 'running', 'blocked')
                       ORDER BY j.id DESC LIMIT 1
                   ) AS active_delete_job_id
            FROM video_seasons s
            JOIN video_series sr ON sr.id = s.series_id
            WHERE s.id = ? AND s.series_id = ?
              AND s.is_deleted = 0 AND sr.is_deleted = 0
            """,
            (int(season_id), int(series_id)),
        ).fetchone()
    return dict(row) if row else None


def _display_name_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _validated_display_name(value: str, maximum: int) -> str:
    name = " ".join(str(value or "").split())
    if not name:
        raise SeriesEditError("展示名称不能为空", "display_name_required")
    if len(name) > maximum:
        raise SeriesEditError(
            "展示名称不能超过 {} 个字符".format(maximum),
            "display_name_too_long",
        )
    if any(ord(character) < 32 for character in name):
        raise SeriesEditError(
            "展示名称包含无效控制字符",
            "display_name_invalid",
        )
    return name


def _ensure_not_deleting(
    connection,
    scope_type: str,
    target_id: int,
    error_type = SeriesEditError,
) -> None:
    row = connection.execute(
        """
        SELECT 1 FROM video_delete_jobs
        WHERE scope_type = ? AND target_id = ?
          AND status IN ('pending', 'running', 'blocked')
        LIMIT 1
        """,
        (scope_type, int(target_id)),
    ).fetchone()
    if row:
        raise error_type(
            "存在活动删除任务，不能修改数据",
            "active_delete_task",
        )


def update_series_display_name(
    series_id: int,
    display_name: str,
    expected_updated_at: str = "",
) -> Dict:
    name = _validated_display_name(display_name, 120)
    desired_key = _display_name_key(name)
    now = database._now()
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            """
            SELECT * FROM video_series
            WHERE id = ? AND is_deleted = 0
            """,
            (int(series_id),),
        ).fetchone()
        if current is None:
            connection.rollback()
            raise SeriesEditError("视频集不存在", "series_missing")
        if (
            expected_updated_at
            and str(current["updated_at"]) != str(expected_updated_at)
        ):
            connection.rollback()
            raise SeriesEditError(
                "数据已发生变化，请刷新后重试",
                "data_changed",
            )
        _ensure_not_deleting(connection, "series", series_id)
        siblings = connection.execute(
            """
            SELECT id, name, display_name_override
            FROM video_series
            WHERE provider = ? AND is_deleted = 0 AND id <> ?
            """,
            (str(current["provider"] or ""), int(series_id)),
        ).fetchall()
        if any(
            _display_name_key(
                row["display_name_override"] or row["name"]
            ) == desired_key
            for row in siblings
        ):
            connection.rollback()
            raise SeriesEditError(
                "名称已被其他视频集使用",
                "display_name_duplicate",
            )
        previous_display_name = str(
            current["display_name_override"] or current["name"]
        )
        override = None if name == str(current["name"]) else name
        connection.execute(
            """
            UPDATE video_series
            SET display_name_override = ?, updated_at = ?
            WHERE id = ?
            """,
            (override, now, int(series_id)),
        )
        connection.commit()
    return {
        "id": int(series_id),
        "name": str(current["name"]),
        "previous_display_name": previous_display_name,
        "display_name": name,
        "updated_at": now,
    }


def update_season_display_name(
    series_id: int,
    season_id: int,
    display_name: str,
    expected_updated_at: str = "",
) -> Dict:
    name = _validated_display_name(display_name, 80)
    desired_key = _display_name_key(name)
    now = database._now()
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            """
            SELECT s.* FROM video_seasons s
            JOIN video_series sr ON sr.id = s.series_id
            WHERE s.id = ? AND s.series_id = ?
              AND s.is_deleted = 0 AND sr.is_deleted = 0
            """,
            (int(season_id), int(series_id)),
        ).fetchone()
        if current is None:
            connection.rollback()
            raise SeriesEditError("视频季不存在", "season_missing")
        if (
            expected_updated_at
            and str(current["updated_at"]) != str(expected_updated_at)
        ):
            connection.rollback()
            raise SeriesEditError(
                "数据已发生变化，请刷新后重试",
                "data_changed",
            )
        _ensure_not_deleting(connection, "season", season_id)
        siblings = connection.execute(
            """
            SELECT id, name, display_name_override
            FROM video_seasons
            WHERE series_id = ? AND is_deleted = 0 AND id <> ?
            """,
            (int(series_id), int(season_id)),
        ).fetchall()
        if any(
            _display_name_key(
                row["display_name_override"] or row["name"]
            ) == desired_key
            for row in siblings
        ):
            connection.rollback()
            raise SeriesEditError(
                "名称已被该视频集中的其他季使用",
                "display_name_duplicate",
            )
        previous_display_name = str(
            current["display_name_override"] or current["name"]
        )
        override = None if name == str(current["name"]) else name
        connection.execute(
            """
            UPDATE video_seasons
            SET display_name_override = ?, updated_at = ?
            WHERE id = ?
            """,
            (override, now, int(season_id)),
        )
        connection.commit()
    return {
        "id": int(season_id),
        "series_id": int(series_id),
        "name": str(current["name"]),
        "previous_display_name": previous_display_name,
        "display_name": name,
        "updated_at": now,
    }


def _validated_episode_title(value: str) -> str:
    raw = str(value or "")
    if any(ord(character) < 32 or ord(character) == 127 for character in raw):
        raise EpisodeEditError(
            "剧集标题包含无效控制字符",
            "episode_title_invalid",
        )
    title = " ".join(raw.split())
    if not title:
        raise EpisodeEditError(
            "剧集标题不能为空",
            "episode_title_required",
        )
    if len(title) > 200:
        raise EpisodeEditError(
            "剧集标题不能超过 200 个字符",
            "episode_title_too_long",
        )
    if is_generic_episode_title(title):
        raise EpisodeEditError(
            "请输入有意义的剧集标题，不能只使用“正片”或“第N集”等占位内容",
            "episode_title_generic",
        )
    return title


def update_episode_title(
    series_id: int,
    season_id: int,
    episode_id: int,
    episode_title: str,
    expected_updated_at: str = "",
) -> Dict:
    title = _validated_episode_title(episode_title)
    now = database._now()
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = connection.execute(
            """
            SELECT e.id, e.season_id, e.episode_number, e.episode_title,
                   e.display_title, e.video_id, e.updated_at,
                   s.series_id
            FROM video_episodes e
            JOIN video_seasons s ON s.id = e.season_id
            JOIN video_series sr ON sr.id = s.series_id
            WHERE e.id = ? AND e.season_id = ? AND s.series_id = ?
              AND s.is_deleted = 0 AND sr.is_deleted = 0
            """,
            (int(episode_id), int(season_id), int(series_id)),
        ).fetchone()
        if current is None:
            connection.rollback()
            raise EpisodeEditError(
                "剧集不存在或不属于当前视频季",
                "episode_missing",
            )
        if (
            expected_updated_at
            and str(current["updated_at"]) != str(expected_updated_at)
        ):
            connection.rollback()
            raise EpisodeEditError(
                "剧集数据已经发生变化，请刷新后重试",
                "data_changed",
            )
        _ensure_not_deleting(
            connection,
            "season",
            season_id,
            EpisodeEditError,
        )
        _ensure_not_deleting(
            connection,
            "series",
            series_id,
            EpisodeEditError,
        )
        if current["video_id"] is not None:
            _ensure_not_deleting(
                connection,
                "video",
                int(current["video_id"]),
                EpisodeEditError,
            )
        old_title = str(current["episode_title"] or "")
        connection.execute(
            """
            UPDATE video_episodes
            SET episode_title = ?, updated_at = ?
            WHERE id = ? AND season_id = ?
            """,
            (title, now, int(episode_id), int(season_id)),
        )
        connection.commit()
    return {
        "status": "ok",
        "series_id": int(series_id),
        "season_id": int(season_id),
        "episode_id": int(episode_id),
        "video_id": (
            int(current["video_id"])
            if current["video_id"] is not None
            else None
        ),
        "episode_number": int(current["episode_number"]),
        "old_episode_title": old_title,
        "episode_title": title,
        "display_title": str(current["display_title"] or ""),
        "updated_at": now,
    }


def list_season_videos(
    series_id: int,
    season_id: int,
    query: str = "",
    record_state: str = "all",
    page: int = 1,
    page_size: int = 30,
) -> Dict:
    clauses = [
        "s.id = ?",
        "s.series_id = ?",
        "s.is_deleted = 0",
        "sr.is_deleted = 0",
    ]
    params = [int(season_id), int(series_id)]
    record_state = str(record_state or "all").strip().lower()
    if record_state not in {"all", "active", "deleted"}:
        record_state = "all"
    if record_state == "deleted":
        clauses.append("v.is_deleted = 1")
    elif record_state == "active":
        clauses.append("(v.id IS NULL OR v.is_deleted = 0)")
    parsed = parse_search_query(query)
    if parsed.is_id:
        if parsed.id_value is None:
            clauses.append("0 = 1")
        else:
            clauses.append("v.id = ?")
            params.append(parsed.id_value)
    elif parsed.raw:
        escaped = (
            parsed.raw.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        clauses.append(
            """
            (
                COALESCE(v.title, '') LIKE ? ESCAPE '\\'
                OR e.display_title LIKE ? ESCAPE '\\'
                OR COALESCE(e.episode_title, '') LIKE ? ESCAPE '\\'
                OR COALESCE(v.path, '') LIKE ? ESCAPE '\\'
            )
            """
        )
        like = "%{}%".format(escaped)
        params.extend([like, like, like, like])
    page, page_size = _page(query, page, page_size)
    where = " AND ".join(clauses)
    with database.connect() as connection:
        total = connection.execute(
            """
            SELECT COUNT(*)
            FROM video_episodes e
            JOIN video_seasons s ON s.id = e.season_id
            JOIN video_series sr ON sr.id = s.series_id
            LEFT JOIN video_catalog v ON v.id = e.video_id
            WHERE {}
            """.format(where),
            params,
        ).fetchone()[0]
        pages = max(1, (int(total) + page_size - 1) // page_size)
        page = min(page, pages)
        rows = connection.execute(
            """
            SELECT e.id AS episode_id, e.episode_number,
                   e.display_title, e.episode_title, e.status,
                   e.relative_path, e.absolute_path,
                   e.updated_at AS episode_updated_at,
                   COALESCE(v.updated_at, e.updated_at) AS updated_at,
                   v.id AS video_id, v.title AS video_title,
                   v.size, v.duration, v.width, v.height,
                   v.video_codec, v.audio_codec, v.access_level,
                   v.missing, v.storage_volume_id,
                   v.storage_relative_path, v.thumbnail_path,
                   v.path AS video_path, v.is_deleted,
                   v.deleted_at, v.metadata_status,
                   sv.online_state AS storage_online_state
            FROM video_episodes e
            JOIN video_seasons s ON s.id = e.season_id
            JOIN video_series sr ON sr.id = s.series_id
            LEFT JOIN video_catalog v ON v.id = e.video_id
            LEFT JOIN video_storage_volumes sv
              ON sv.id = v.storage_volume_id
            WHERE {}
            ORDER BY e.episode_number
            LIMIT ? OFFSET ?
            """.format(where),
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    return {
        "items": [dict(row) for row in rows],
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "record_state": record_state,
    }
