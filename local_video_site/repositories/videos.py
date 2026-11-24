from __future__ import annotations

import json
import sqlite3
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from local_video_site.core import config
from ..db.connection import connect
from ..db.transaction import now_iso as _now
from local_video_site.episodes.naming import is_generic_episode_title, meaningful_episode_title, season_number_from_label
from local_video_site.core.search import parse_search_query
from ..db.schema import PLAYBACK_NORMALIZATION_VERSION


def _ensure_video_satellites(
    connection: sqlite3.Connection,
    video_id: int,
    now: Optional[str] = None,
) -> None:
    timestamp = now or _now()
    connection.execute(
        """
        INSERT OR IGNORE INTO video_media_info (video_id, updated_at)
        VALUES (?, ?)
        """,
        (int(video_id), timestamp),
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO video_assets (video_id, updated_at)
        VALUES (?, ?)
        """,
        (int(video_id), timestamp),
    )


def video_with_preview_state(
    row,
    include_preview_availability: bool = True,
) -> Dict:
    item = dict(row)
    if not include_preview_availability:
        item["preview_available"] = item.get("preview_status") == "ready"
        return item
    preview_path = str(item.get("preview_path") or "").strip()
    item["preview_available"] = bool(
        item.get("preview_status") == "ready"
        and preview_path
        and (config.PREVIEW_DIR / Path(preview_path).name).is_file()
    )
    return item


class VideoRepository:
    def get(self, video_id: int) -> Optional[Dict]:
        with connect() as connection:
            row = connection.execute(
                "SELECT * FROM video_catalog "
                "WHERE id = ? AND missing = 0 AND is_deleted = 0",
                (int(video_id),),
            ).fetchone()
        return video_with_preview_state(row) if row else None

    def get_admin(self, video_id: int) -> Optional[Dict]:
        with connect() as connection:
            row = connection.execute(
                "SELECT * FROM video_catalog WHERE id = ?",
                (int(video_id),),
            ).fetchone()
        return video_with_preview_state(row) if row else None

    def get_by_path(self, path: Path) -> Optional[Dict]:
        with connect() as connection:
            row = connection.execute(
                "SELECT * FROM video_catalog "
                "WHERE path = ? AND missing = 0 AND is_deleted = 0",
                (str(path),),
            ).fetchone()
        return video_with_preview_state(row) if row else None


video_repository = VideoRepository()


def upsert_video(
    path: Path,
    title: str,
    directory: str,
    size: int,
    mtime_ns: int,
    original_url: Optional[str] = None,
    download_source_url: Optional[str] = None,
    media_origin: str = "download",
) -> str:
    from .episodes import _apply_planned_episode_policy

    now = _now()
    extension = path.suffix.lower()
    with connect() as connection:
        row = connection.execute(
            "SELECT id, size, mtime_ns, is_deleted "
            "FROM videos WHERE path = ?",
            (str(path),),
        ).fetchone()
        if row is None:
            cursor = connection.execute(
                """
                INSERT INTO videos (
                    path, title, original_url, download_source_url,
                    media_origin, directory, extension, size, mtime_ns,
                    missing, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    str(path),
                    title,
                    original_url,
                    download_source_url,
                    media_origin or "download",
                    directory,
                    extension,
                    size,
                    mtime_ns,
                    now,
                    now,
                ),
            )
            video_id = int(cursor.lastrowid or 0)
            _ensure_video_satellites(connection, video_id, now)
            _apply_planned_episode_policy(
                connection,
                path,
                video_id,
            )
            return "added"

        _ensure_video_satellites(connection, int(row["id"]), now)
        restored = bool(row["is_deleted"])
        changed = (
            restored
            or row["size"] != size
            or row["mtime_ns"] != mtime_ns
        )
        if changed:
            _ensure_video_satellites(connection, int(row["id"]), now)
            connection.execute(
                """
                UPDATE videos
                SET title = ?, directory = ?, extension = ?, size = ?,
                    mtime_ns = ?, media_origin = ?, missing = 0,
                    is_deleted = 0, deleted_at = NULL,
                    original_url = COALESCE(NULLIF(?, ''), original_url),
                    download_source_url = COALESCE(
                        NULLIF(?, ''),
                        download_source_url
                    ),
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    title,
                    directory,
                    extension,
                    size,
                    mtime_ns,
                    media_origin or "download",
                    original_url,
                    download_source_url,
                    now,
                    row["id"],
                ),
            )
            connection.execute(
                """
                UPDATE video_media_info
                SET duration = NULL, width = NULL, height = NULL,
                    video_codec = NULL, audio_codec = NULL,
                    metadata_status = 'pending', metadata_error = NULL,
                    updated_at = ?
                WHERE video_id = ?
                """,
                (now, int(row["id"])),
            )
            connection.execute(
                """
                UPDATE video_assets
                SET thumbnail_path = NULL, preview_path = NULL,
                    preview_status = 'pending', preview_error = NULL,
                    preview_source_size = NULL,
                    preview_source_mtime_ns = NULL, cover_source = 'auto',
                    cover_timestamp = NULL, cover_updated_at = ?,
                    updated_at = ?
                WHERE video_id = ?
                """,
                (now, now, int(row["id"])),
            )
        else:
            connection.execute(
                """
                UPDATE videos
                SET directory = ?, missing = 0, is_deleted = 0,
                    deleted_at = NULL,
                    original_url = COALESCE(NULLIF(?, ''), original_url),
                    download_source_url = COALESCE(
                        NULLIF(?, ''),
                        download_source_url
                    )
                WHERE id = ?
                """,
                (
                    directory,
                    original_url,
                    download_source_url,
                    row["id"],
                ),
            )
        _apply_planned_episode_policy(connection, path, int(row["id"]))
        return "changed" if changed else "unchanged"
def update_original_urls(mapping: Dict[str, str]) -> int:
    now = _now()
    updated = 0
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT id, path FROM videos
            WHERE original_url IS NULL OR original_url = ''
            """
        ).fetchall()
        for row in rows:
            resolved_path = str(Path(row["path"]).resolve())
            original_url = (
                mapping.get(row["path"])
                or mapping.get(resolved_path)
            )
            if not original_url:
                continue
            cursor = connection.execute(
                """
                UPDATE videos
                SET original_url = ?,
                    download_source_url = COALESCE(
                        download_source_url,
                        ?
                    ),
                    updated_at = ?
                WHERE id = ?
                """,
                (original_url, original_url, now, row["id"]),
            )
            updated += max(0, cursor.rowcount)
    return updated
def mark_missing_except(root_paths: List[Path], seen_paths: set) -> int:
    if not root_paths:
        return 0
    missing_ids = []
    with connect() as connection:
        rows = connection.execute(
            "SELECT id, path FROM videos "
            "WHERE missing = 0 AND is_deleted = 0"
        ).fetchall()
        for row in rows:
            path = Path(row["path"])
            under_root = any(
                path == root or root in path.parents for root in root_paths
            )
            if under_root and str(path) not in seen_paths:
                missing_ids.append(row["id"])
        if missing_ids:
            connection.executemany(
                "UPDATE videos SET missing = 1, updated_at = ? WHERE id = ?",
                [(_now(), video_id) for video_id in missing_ids],
            )
    return len(missing_ids)
def pending_metadata(limit: Optional[int] = None) -> List[Dict]:
    sql = """
        SELECT * FROM video_catalog
        WHERE missing = 0 AND is_deleted = 0
          AND metadata_status = 'pending'
        ORDER BY updated_at DESC, id DESC
    """
    params: Tuple = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (int(limit),)
    with connect() as connection:
        return [dict(row) for row in connection.execute(sql, params).fetchall()]
def claim_pending_metadata() -> Optional[Dict]:
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT * FROM video_catalog
            WHERE missing = 0 AND is_deleted = 0
              AND metadata_status = 'pending'
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            connection.commit()
            return None
        connection.execute(
            """
            UPDATE video_media_info SET metadata_status = 'processing'
            WHERE video_id = ?
            """,
            (row["id"],),
        )
        connection.commit()
        value = dict(row)
        value["metadata_status"] = "processing"
        return value
def update_metadata(
    video_id: int,
    info: Dict,
    thumbnail_path: Optional[str],
    status: str = "ready",
    error: Optional[str] = None,
) -> None:
    with connect() as connection:
        now = _now()
        _ensure_video_satellites(connection, int(video_id), now)
        connection.execute(
            """
            UPDATE video_media_info
            SET duration = ?, width = ?, height = ?, video_codec = ?,
                audio_codec = ?,
                container_format = COALESCE(?, container_format),
                metadata_status = ?, metadata_error = ?, updated_at = ?
            WHERE video_id = ?
            """,
            (
                info.get("duration"),
                info.get("width"),
                info.get("height"),
                info.get("video_codec"),
                info.get("audio_codec"),
                info.get("container_format"),
                status,
                error,
                now,
                int(video_id),
            ),
        )
        connection.execute(
            """
            UPDATE video_assets
            SET thumbnail_path = ?,
                preview_status = CASE
                    WHEN ? = 'ready' THEN 'pending'
                    ELSE preview_status
                END,
                preview_error = CASE
                    WHEN ? = 'ready' THEN NULL
                    ELSE preview_error
                END,
                updated_at = ?
            WHERE video_id = ?
            """,
            (
                thumbnail_path,
                status,
                status,
                now,
                video_id,
            ),
        )
    from .episodes import update_season_assets_from_video

    update_season_assets_from_video(int(video_id))
def update_video_technical_info(
    video_id: int,
    info: Dict,
) -> None:
    with connect() as connection:
        now = _now()
        _ensure_video_satellites(connection, int(video_id), now)
        connection.execute(
            """
            UPDATE video_media_info
            SET duration = ?, width = ?, height = ?,
                video_codec = ?, audio_codec = ?,
                container_format = COALESCE(?, container_format),
                updated_at = ?
            WHERE video_id = ?
            """,
            (
                info.get("duration"),
                info.get("width"),
                info.get("height"),
                info.get("video_codec"),
                info.get("audio_codec"),
                info.get("container_format"),
                now,
                int(video_id),
            ),
        )
def delete_video_record(video_id: int) -> bool:
    now = _now()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        video = connection.execute(
            "SELECT id, is_deleted FROM videos WHERE id = ?",
            (int(video_id),),
        ).fetchone()
        if video is None:
            connection.rollback()
            return False
        connection.execute(
            "DELETE FROM playback_cache_entries WHERE video_id = ?",
            (int(video_id),),
        )
        cursor = connection.execute(
            """
            UPDATE videos
            SET is_deleted = 1,
                deleted_at = ?,
                updated_at = ?
            WHERE id = ?
              AND is_deleted = 0
            """,
            (now, now, int(video_id)),
        )
        connection.execute(
            """
            UPDATE video_episodes
            SET status = 'deleted', updated_at = ?
            WHERE video_id = ?
            """,
            (now, int(video_id)),
        )
        connection.commit()
        return cursor.rowcount > 0
def videos_without_thumbnails(limit: Optional[int] = None) -> List[Dict]:
    sql = """
        SELECT * FROM video_catalog
        WHERE missing = 0 AND is_deleted = 0
          AND metadata_status <> 'inherited'
          AND (thumbnail_path IS NULL OR thumbnail_path = '')
        ORDER BY id ASC
    """
    params: Tuple = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (int(limit),)
    with connect() as connection:
        return [dict(row) for row in connection.execute(sql, params).fetchall()]
def episode_videos_missing_technical_info(
    limit: Optional[int] = None,
) -> List[Dict]:
    sql = """
        SELECT DISTINCT v.*
        FROM video_catalog v
        JOIN video_episodes e ON e.video_id = v.id
        WHERE v.missing = 0 AND v.is_deleted = 0
          AND v.metadata_status = 'inherited'
          AND (
              v.duration IS NULL
              OR v.width IS NULL
              OR v.height IS NULL
              OR v.video_codec IS NULL
              OR v.audio_codec IS NULL
              OR v.thumbnail_path IS NULL
              OR TRIM(v.thumbnail_path) = ''
          )
        ORDER BY v.id ASC
    """
    params: Tuple = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (int(limit),)
    with connect() as connection:
        return [
            dict(row)
            for row in connection.execute(sql, params).fetchall()
        ]
def mark_video_missing(video_id: int) -> None:
    with connect() as connection:
        connection.execute(
            "UPDATE videos SET missing = 1, updated_at = ? WHERE id = ?",
            (_now(), video_id),
        )
        connection.execute(
            """
            UPDATE video_episodes
            SET status = 'missing', updated_at = ?
            WHERE video_id = ?
            """,
            (_now(), int(video_id)),
        )
def active_videos() -> List[Dict]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM video_catalog "
            "WHERE missing = 0 AND is_deleted = 0 ORDER BY id ASC"
        ).fetchall()
    return [dict(row) for row in rows]
def update_normalization_status(
    video_id: int,
    status: str,
    error: Optional[str] = None,
) -> None:
    now = _now()
    completed_at = now if status == "ready" else None
    with connect() as connection:
        _ensure_video_satellites(connection, int(video_id), now)
        connection.execute(
            """
            UPDATE video_media_info
            SET normalization_status = ?,
                normalization_error = ?,
                normalized_at = COALESCE(?, normalized_at),
                normalization_version = ?,
                updated_at = ?
            WHERE video_id = ?
            """,
            (
                status,
                error,
                completed_at,
                (
                    PLAYBACK_NORMALIZATION_VERSION
                    if status == "ready"
                    else 0
                ),
                now,
                int(video_id),
            ),
        )
def update_video_after_normalization(
    video_id: int,
    size: int,
    mtime_ns: int,
    info: Optional[Dict] = None,
    new_path: Optional[Path] = None,
    mark_normalized: Optional[bool] = True,
) -> None:
    info = info or {}
    now = _now()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        _ensure_video_satellites(connection, int(video_id), now)
        normalized_path = (
            Path(new_path).expanduser().resolve()
            if new_path is not None
            else None
        )
        storage_relative_path = None
        normalization_row = connection.execute(
            """
            SELECT normalization_status, normalized_at,
                   normalization_version
            FROM video_media_info WHERE video_id = ?
            """,
            (int(video_id),),
        ).fetchone()
        normalization_status = (
            normalization_row["normalization_status"]
            if normalization_row is not None
            else "none"
        )
        normalized_at = (
            normalization_row["normalized_at"]
            if normalization_row is not None
            else None
        )
        normalization_version = (
            int(normalization_row["normalization_version"] or 0)
            if normalization_row is not None
            else 0
        )
        if mark_normalized is True:
            normalization_status = "ready"
            normalized_at = now
            normalization_version = PLAYBACK_NORMALIZATION_VERSION
        elif mark_normalized is False:
            normalization_status = "none"
            normalization_version = 0
        if normalized_path is not None:
            video = connection.execute(
                """
                SELECT storage_volume_id
                FROM videos WHERE id = ?
                """,
                (int(video_id),),
            ).fetchone()
            if video is None:
                connection.rollback()
                raise ValueError("视频不存在")
            if video["storage_volume_id"] is not None:
                volume = connection.execute(
                    """
                    SELECT mount_path, root_relative_path
                    FROM video_storage_volumes WHERE id = ?
                    """,
                    (int(video["storage_volume_id"]),),
                ).fetchone()
                if volume is None:
                    connection.rollback()
                    raise ValueError("视频存储卷不存在")
                try:
                    volume_root = (
                        Path(volume["mount_path"]).resolve()
                        / str(volume["root_relative_path"] or "")
                    )
                    storage_relative_path = str(
                        normalized_path.relative_to(volume_root)
                    )
                except ValueError as exc:
                    connection.rollback()
                    raise ValueError(
                        "标准化视频路径超出存储卷"
                    ) from exc
        connection.execute(
            """
            UPDATE videos
            SET path = COALESCE(?, path),
                extension = COALESCE(?, extension),
                directory = COALESCE(?, directory),
                storage_relative_path = COALESCE(
                    ?,
                    storage_relative_path
                ),
                size = ?,
                mtime_ns = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                (
                    str(normalized_path)
                    if normalized_path is not None
                    else None
                ),
                (
                    normalized_path.suffix.lower()
                    if normalized_path is not None
                    else None
                ),
                (
                    normalized_path.parent.name
                    if normalized_path is not None
                    else None
                ),
                storage_relative_path,
                int(size),
                int(mtime_ns),
                now,
                int(video_id),
            ),
        )
        if normalized_path is not None:
            episode_rows = connection.execute(
                """
                SELECT id, relative_path, storage_relative_path
                FROM video_episodes
                WHERE video_id = ?
                """,
                (int(video_id),),
            ).fetchall()
            for episode in episode_rows:
                episode_relative = (
                    str(Path(episode["relative_path"]).with_suffix(".mp4"))
                    if episode["relative_path"]
                    else None
                )
                episode_storage_relative = (
                    storage_relative_path
                    or (
                        str(
                            Path(
                                episode["storage_relative_path"]
                            ).with_suffix(".mp4")
                        )
                        if episode["storage_relative_path"]
                        else None
                    )
                )
                connection.execute(
                    """
                    UPDATE video_episodes
                    SET absolute_path = ?, relative_path = COALESCE(
                            ?, relative_path
                        ),
                        storage_relative_path = COALESCE(
                            ?,
                            storage_relative_path
                        ),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        str(normalized_path),
                        episode_relative,
                        episode_storage_relative,
                        now,
                        int(episode["id"]),
                    ),
                )
            connection.execute(
                """
                UPDATE download_tasks
                SET file_path = ?, file_name = ?, updated_at = ?
                WHERE video_id = ? AND deleted_at IS NULL
                """,
                (
                    str(normalized_path),
                    normalized_path.name,
                    now,
                    int(video_id),
                ),
            )
            connection.execute(
                """
                UPDATE video_import_jobs
                SET destination_path = ?, updated_at = ?
                WHERE video_id = ?
                """,
                (
                    str(normalized_path),
                    now,
                    int(video_id),
                ),
            )
            connection.execute(
                """
                UPDATE video_migrations
                SET destination_path = ?, updated_at = ?
                WHERE video_id = ?
                  AND direction = 'to_external'
                  AND status = 'success'
                """,
                (
                    str(normalized_path),
                    now,
                    int(video_id),
                ),
            )
            connection.execute(
                """
                UPDATE video_assets
                SET preview_source_size = ?,
                    preview_source_mtime_ns = ?,
                    updated_at = ?
                WHERE video_id = ?
                  AND preview_path IS NOT NULL
                  AND preview_status = 'ready'
                """,
                (
                    int(size),
                    int(mtime_ns),
                    now,
                    int(video_id),
                ),
            )
        connection.execute(
            """
            UPDATE video_media_info
            SET duration = COALESCE(?, duration),
                width = COALESCE(?, width),
                height = COALESCE(?, height),
                video_codec = COALESCE(?, video_codec),
                audio_codec = COALESCE(?, audio_codec),
                container_format = COALESCE(?, container_format),
                normalization_status = ?,
                normalization_error = NULL,
                normalized_at = ?,
                normalization_version = ?,
                updated_at = ?
            WHERE video_id = ?
            """,
            (
                info.get("duration"),
                info.get("width"),
                info.get("height"),
                info.get("video_codec"),
                info.get("audio_codec"),
                info.get("container_format"),
                normalization_status,
                normalized_at,
                normalization_version,
                now,
                int(video_id),
            ),
        )
        connection.commit()
def get_video(video_id: int) -> Optional[Dict]:
    return video_repository.get(video_id)
def get_video_admin(video_id: int) -> Optional[Dict]:
    return video_repository.get_admin(video_id)
def get_video_by_path(path: Path) -> Optional[Dict]:
    return video_repository.get_by_path(path)
def set_video_display_title(
    video_id: int,
    display_title_override: Optional[str],
    expected_updated_at: str = "",
) -> Dict:
    now = _now()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        video = connection.execute(
            """
            SELECT id, title, display_title_override, updated_at,
                   is_deleted
            FROM videos WHERE id = ?
            """,
            (int(video_id),),
        ).fetchone()
        if video is None:
            connection.rollback()
            raise ValueError("视频不存在")
        if int(video["is_deleted"] or 0):
            connection.rollback()
            raise ValueError("视频已删除")
        if (
            expected_updated_at
            and str(video["updated_at"]) != str(expected_updated_at)
        ):
            connection.rollback()
            raise ValueError("视频信息已发生变化")
        old_title = (
            str(video["display_title_override"] or "").strip()
            or str(video["title"] or "")
        )
        connection.execute(
            """
            UPDATE videos
            SET display_title_override = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                display_title_override or None,
                now,
                int(video_id),
            ),
        )
        connection.commit()
    updated = get_video_admin(int(video_id))
    if updated is None:
        raise ValueError("视频不存在")
    return {
        "status": "ok",
        "id": int(video_id),
        "old_title": old_title,
        "title": updated["title"],
        "display_title_override": updated.get(
            "display_title_override"
        ),
        "default_title": updated.get("default_title"),
        "path": updated["path"],
        "file_name": Path(updated["path"]).name,
        "updated_at": updated["updated_at"],
    }
def set_video_access_level(video_id: int, access_level: int) -> Dict:
    level = int(access_level)
    if level not in (0, 1, 2):
        raise ValueError("访问级别必须是 0、1 或 2")
    now = _now()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        video = connection.execute(
            "SELECT * FROM videos WHERE id = ?",
            (int(video_id),),
        ).fetchone()
        if video is None:
            connection.rollback()
            raise ValueError("视频不存在")
        season = connection.execute(
            """
            SELECT season_id FROM video_episodes
            WHERE video_id = ?
            LIMIT 1
            """,
            (int(video_id),),
        ).fetchone()
        if season is None:
            affected_ids = [int(video_id)]
            season_id = None
        else:
            season_id = int(season["season_id"])
            affected_ids = [
                int(row["video_id"])
                for row in connection.execute(
                    """
                    SELECT video_id FROM video_episodes
                    WHERE season_id = ? AND video_id IS NOT NULL
                    ORDER BY episode_number
                    """,
                    (season_id,),
                ).fetchall()
            ]
        connection.executemany(
            """
            UPDATE videos
            SET access_level = ?, updated_at = ?
            WHERE id = ?
            """,
            [
                (level, now, affected_id)
                for affected_id in affected_ids
            ],
        )
        connection.commit()
    return {
        "video_id": int(video_id),
        "old_access_level": int(video["access_level"] or 0),
        "access_level": level,
        "season_id": season_id,
        "affected_video_ids": affected_ids,
        "affected_count": len(affected_ids),
    }


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
