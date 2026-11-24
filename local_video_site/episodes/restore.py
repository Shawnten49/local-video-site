from __future__ import annotations

import hashlib
import hmac
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Union

from local_video_site.core import config
from local_video_site import database
from local_video_site.media.info import probe_video
from local_video_site.ingestion.library_scan import ScanAlreadyRunning, acquire_library_write_lock, release_library_write_lock
from local_video_site.storage.volumes import StorageError, register_external_source


VIDEO_EXTENSIONS = {
    ".mp4",
    ".m4v",
    ".webm",
    ".mkv",
    ".mov",
    ".ts",
    ".avi",
    ".rmvb",
}


class VideoRestoreError(RuntimeError):
    def __init__(self, message: str, code: str = "restore_failed"):
        self.code = code
        super().__init__(message)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _source_fingerprint(
    video_id: int,
    episode_id: int,
    source_path: Path,
    size: int,
    mtime_ns: int,
) -> str:
    payload = "\0".join(
        [
            str(int(video_id)),
            str(int(episode_id)),
            str(source_path),
            str(int(size)),
            str(int(mtime_ns)),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_under(path: Path, root: Path) -> bool:
    try:
        path = path.resolve()
        root = root.resolve()
    except OSError:
        return False
    return path == root or root in path.parents


def _allowed_roots(scope: str) -> list:
    if scope == "local":
        return [path.expanduser().resolve() for path in config.configured_roots()]
    if scope == "external":
        return [
            path.expanduser().resolve()
            for path in config.external_volume_roots()
        ]
    return []


def browse_restore_entries(scope: str, raw_path: str = "") -> Dict:
    scope = str(scope or "local").strip().lower()
    if scope not in {"local", "external"}:
        raise VideoRestoreError("不支持的目录范围", "scope_invalid")
    roots = _allowed_roots(scope)
    if not roots:
        raise VideoRestoreError("没有可浏览的视频目录", "roots_empty")
    requested = Path(raw_path).expanduser() if raw_path else roots[0]
    try:
        path = requested.resolve()
    except OSError as exc:
        raise VideoRestoreError(
            "无法解析目录: {}".format(exc),
            "directory_unreadable",
        ) from exc
    allowed_root = next(
        (root for root in roots if _is_under(path, root)),
        None,
    )
    if allowed_root is None:
        raise VideoRestoreError(
            "目录不在允许的视频范围内",
            "path_not_allowed",
        )
    if not path.is_dir():
        raise VideoRestoreError("目录不存在", "directory_not_found")
    directories = []
    files = []
    try:
        entries = sorted(
            path.iterdir(),
            key=lambda item: (not item.is_dir(), item.name.casefold()),
        )
    except OSError as exc:
        raise VideoRestoreError(
            "无法读取目录: {}".format(exc),
            "directory_unreadable",
        ) from exc
    for item in entries:
        if item.name.startswith(".") or item.is_symlink():
            continue
        try:
            if item.is_dir():
                directories.append({
                    "name": item.name,
                    "path": str(item.resolve()),
                })
            elif item.is_file() and item.suffix.lower() in VIDEO_EXTENSIONS:
                stat = item.stat()
                files.append({
                    "name": item.name,
                    "path": str(item.resolve()),
                    "size": stat.st_size,
                    "extension": item.suffix.lower(),
                    "mtime_ns": stat.st_mtime_ns,
                })
        except OSError:
            continue
    parent = ""
    if path != allowed_root and allowed_root in path.parents:
        parent = str(path.parent)
    try:
        free_bytes = int(shutil.disk_usage(path).free)
    except OSError:
        free_bytes = 0
    return {
        "scope": scope,
        "path": str(path),
        "parent": parent,
        "roots": [str(root) for root in roots],
        "directories": directories,
        "files": files,
        "free_bytes": free_bytes,
    }


def _target_video_and_episode(video_id: int, episode_id: int) -> tuple:
    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT v.*, e.id AS episode_id, e.season_id,
                   e.episode_number, e.display_title,
                   s.series_id, s.name AS season_name,
                   sr.name AS series_name
            FROM videos v
            JOIN video_episodes e ON e.video_id = v.id
            JOIN video_seasons s ON s.id = e.season_id
            JOIN video_series sr ON sr.id = s.series_id
            WHERE v.id = ? AND e.id = ?
              AND s.is_deleted = 0 AND sr.is_deleted = 0
            """,
            (int(video_id), int(episode_id)),
        ).fetchone()
        if row is None:
            raise VideoRestoreError(
                "视频与剧集关联不存在",
                "episode_mismatch",
            )
        if not int(row["is_deleted"] or 0):
            raise VideoRestoreError(
                "当前视频不是已删除状态",
                "video_not_deleted",
            )
        active_delete = connection.execute(
            """
            SELECT id FROM video_delete_jobs
            WHERE status IN ('pending', 'running', 'blocked')
              AND (
                  (scope_type = 'video' AND target_id = ?)
                  OR (scope_type = 'season' AND target_id = ?)
                  OR (scope_type = 'series' AND target_id = ?)
              )
            LIMIT 1
            """,
            (
                int(video_id),
                int(row["season_id"]),
                int(row["series_id"]),
            ),
        ).fetchone()
        if active_delete:
            raise VideoRestoreError(
                "视频、视频季或视频集存在活动删除任务",
                "target_deleting",
            )
    if database.has_active_migration(int(video_id)):
        raise VideoRestoreError(
            "视频存在活动迁移任务",
            "active_migration",
        )
    return dict(row), dict(row)


def _resolve_source(
    raw_path: str,
    storage_scope: str,
) -> Dict:
    requested = Path(str(raw_path or "")).expanduser()
    if not str(raw_path or "").strip():
        raise VideoRestoreError("请选择视频文件", "source_missing")
    if not requested.is_absolute():
        raise VideoRestoreError(
            "视频路径必须是绝对路径",
            "source_not_absolute",
        )
    if requested.is_symlink():
        raise VideoRestoreError(
            "不允许使用符号链接",
            "source_symlink",
        )
    try:
        source = requested.resolve()
    except OSError as exc:
        raise VideoRestoreError(
            "无法解析视频路径: {}".format(exc),
            "source_unreadable",
        ) from exc
    if not source.is_file():
        raise VideoRestoreError("视频文件不存在", "source_missing")
    if source.suffix.lower() not in VIDEO_EXTENSIONS:
        raise VideoRestoreError(
            "不支持的视频格式",
            "source_not_allowed",
        )
    scope = str(storage_scope or "auto").strip().lower()
    if scope not in {"auto", "local", "external"}:
        raise VideoRestoreError("不支持的存储范围", "scope_invalid")
    local_root = next(
        (
            root for root in _allowed_roots("local")
            if _is_under(source, root)
        ),
        None,
    )
    external_root = next(
        (
            root for root in _allowed_roots("external")
            if _is_under(source, root)
        ),
        None,
    )
    if scope == "local":
        storage_kind = "local" if local_root is not None else ""
    elif scope == "external":
        storage_kind = "external" if external_root is not None else ""
    elif local_root is not None:
        storage_kind = "local"
    elif external_root is not None:
        storage_kind = "external"
    else:
        storage_kind = ""
    if not storage_kind:
        raise VideoRestoreError(
            "文件不在允许的本地或外盘视频目录中",
            "source_not_allowed",
        )
    volume = None
    relative_path = ""
    if storage_kind == "external":
        try:
            registered = register_external_source(str(source))
        except StorageError as exc:
            raise VideoRestoreError(str(exc), exc.code) from exc
        volume = registered["volume"]
        relative_path = str(registered["source_relative_path"])
    else:
        relative_path = str(source.relative_to(local_root))
    stat = source.stat()
    return {
        "path": source,
        "storage_kind": storage_kind,
        "volume": volume,
        "volume_id": int(volume["id"]) if volume else None,
        "relative_path": relative_path,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "extension": source.suffix.lower(),
    }


def _path_conflict(connection, source: Path, video_id: int):
    target = str(source.resolve()).casefold()
    rows = connection.execute(
        "SELECT id, path FROM videos WHERE id <> ?",
        (int(video_id),),
    ).fetchall()
    for row in rows:
        try:
            current = str(Path(str(row["path"])).resolve()).casefold()
        except OSError:
            current = str(row["path"]).casefold()
        if current == target:
            return row
    return None


def preview_restore_source(
    video_id: int,
    episode_id: int,
    source_path: str,
    storage_scope: str = "auto",
) -> Dict:
    video, episode = _target_video_and_episode(video_id, episode_id)
    source = _resolve_source(source_path, storage_scope)
    with database.connect() as connection:
        conflict = _path_conflict(connection, source["path"], video_id)
    if conflict:
        raise VideoRestoreError(
            "该文件已经绑定到视频 #{}".format(conflict["id"]),
            "source_already_bound",
        )
    info = probe_video(source["path"]) or {}
    return {
        "video_id": int(video_id),
        "episode_id": int(episode_id),
        "source_path": str(source["path"]),
        "size": source["size"],
        "mtime_ns": str(source["mtime_ns"]),
        "source_fingerprint": _source_fingerprint(
            video_id,
            episode_id,
            source["path"],
            source["size"],
            source["mtime_ns"],
        ),
        "extension": source["extension"],
        "storage_kind": source["storage_kind"],
        "storage_volume_id": source["volume_id"],
        "storage_volume_label": (
            source["volume"].get("label") if source["volume"] else ""
        ),
        "duration": info.get("duration"),
        "width": info.get("width"),
        "height": info.get("height"),
        "video_codec": info.get("video_codec"),
        "audio_codec": info.get("audio_codec"),
        "original_path": str(video.get("path") or ""),
        "updated_at": str(video.get("updated_at") or ""),
    }


def _legacy_mtime_ns(value: Union[int, str]) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise VideoRestoreError(
            "视频校验参数无效，请重新校验",
            "source_changed",
        ) from exc


def _validate_source_identity(
    video_id: int,
    episode_id: int,
    source_path: Path,
    size: int,
    mtime_ns: int,
    source_fingerprint: str,
    expected_size: Optional[int],
    expected_mtime_ns: Optional[Union[int, str]],
) -> None:
    if (
        not source_fingerprint
        and expected_size is None
        and expected_mtime_ns is None
    ):
        raise VideoRestoreError(
            "请先校验视频文件再重新上线",
            "preview_required",
        )
    if source_fingerprint:
        current_fingerprint = _source_fingerprint(
            video_id,
            episode_id,
            source_path,
            size,
            mtime_ns,
        )
        if not hmac.compare_digest(
            str(source_fingerprint),
            current_fingerprint,
        ):
            raise VideoRestoreError(
                "文件在预览后发生变化，请重新校验",
                "source_changed",
            )
    if expected_size is not None and int(expected_size) != int(size):
        raise VideoRestoreError(
            "视频文件大小已经变化，请重新校验",
            "source_changed",
        )
    if (
        expected_mtime_ns is not None
        and _legacy_mtime_ns(expected_mtime_ns) != int(mtime_ns)
    ):
        raise VideoRestoreError(
            "视频文件修改时间已经变化，请重新校验",
            "source_changed",
        )


def _source_stat(source: Dict) -> tuple:
    try:
        stat = source["path"].stat()
    except OSError as exc:
        raise VideoRestoreError(
            "视频文件不存在或无法读取",
            "source_missing",
        ) from exc
    return int(stat.st_size), int(stat.st_mtime_ns)


def restore_video_online(
    video_id: int,
    episode_id: int,
    source_path: str,
    storage_scope: str = "auto",
    expected_size: Optional[int] = None,
    expected_mtime_ns: Optional[Union[int, str]] = None,
    source_fingerprint: str = "",
    expected_updated_at: str = "",
) -> Dict:
    video, episode = _target_video_and_episode(video_id, episode_id)
    if (
        expected_updated_at
        and str(video["updated_at"]) != str(expected_updated_at)
    ):
        raise VideoRestoreError(
            "视频记录已经发生变化，请刷新后重试",
            "data_changed",
        )
    lock_handle = None
    source = None
    try:
        try:
            lock_handle = acquire_library_write_lock()
        except ScanAlreadyRunning as exc:
            raise VideoRestoreError(
                "视频库正在执行其他写操作，请稍后重试",
                "library_busy",
            ) from exc
        source = _resolve_source(source_path, storage_scope)
        source_size, source_mtime_ns = _source_stat(source)
        _validate_source_identity(
            video_id,
            episode_id,
            source["path"],
            source_size,
            source_mtime_ns,
            source_fingerprint,
            expected_size,
            expected_mtime_ns,
        )
        now = _now()
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM videos WHERE id = ?",
                (int(video_id),),
            ).fetchone()
            if current is None or not int(current["is_deleted"] or 0):
                connection.rollback()
                raise VideoRestoreError(
                    "视频状态已经变化",
                    "data_changed",
                )
            if (
                expected_updated_at
                and str(current["updated_at"]) != str(expected_updated_at)
            ):
                connection.rollback()
                raise VideoRestoreError(
                    "视频记录已经发生变化，请刷新后重试",
                    "data_changed",
                )
            conflict = _path_conflict(
                connection,
                source["path"],
                video_id,
            )
            if conflict:
                connection.rollback()
                raise VideoRestoreError(
                    "该文件已经绑定到视频 #{}".format(conflict["id"]),
                    "source_already_bound",
                )
            source_size, source_mtime_ns = _source_stat(source)
            _validate_source_identity(
                video_id,
                episode_id,
                source["path"],
                source_size,
                source_mtime_ns,
                source_fingerprint,
                expected_size,
                expected_mtime_ns,
            )
            connection.execute(
                """
                UPDATE videos
                SET path = ?, extension = ?, size = ?, mtime_ns = ?,
                    directory = ?, storage_volume_id = ?,
                    storage_relative_path = ?,
                    storage_changed_at = ?, missing = 0,
                    is_deleted = 0, deleted_at = NULL,
                    media_origin = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    str(source["path"]),
                    source["extension"],
                    source_size,
                    source_mtime_ns,
                    source["path"].parent.name,
                    source["volume_id"],
                    source["relative_path"] if source["volume_id"] else None,
                    now if source["volume_id"] else None,
                    (
                        "external_scan"
                        if source["storage_kind"] == "external"
                        else "download"
                    ),
                    now,
                    int(video_id),
                ),
            )
            connection.execute(
                """
                UPDATE video_episodes
                SET status = 'available', absolute_path = ?,
                    relative_path = ?, storage_volume_id = ?,
                    storage_relative_path = ?, updated_at = ?
                WHERE id = ? AND video_id = ?
                """,
                (
                    str(source["path"]),
                    source["relative_path"],
                    source["volume_id"],
                    (
                        source["relative_path"]
                        if source["volume_id"]
                        else None
                    ),
                    now,
                    int(episode_id),
                    int(video_id),
                ),
            )
            connection.execute(
                "DELETE FROM playback_cache_entries WHERE video_id = ?",
                (int(video_id),),
            )
            database._ensure_video_satellites(
                connection,
                int(video_id),
                now,
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
                (now, int(video_id)),
            )
            connection.execute(
                """
                UPDATE video_assets
                SET thumbnail_path = NULL, cover_source = 'auto',
                    cover_timestamp = NULL, cover_updated_at = NULL,
                    preview_path = NULL, preview_status = 'pending',
                    preview_error = NULL, preview_start = NULL,
                    preview_duration = NULL, preview_updated_at = NULL,
                    preview_source_size = NULL,
                    preview_source_mtime_ns = NULL,
                    preview_last_accessed_at = NULL,
                    updated_at = ?
                WHERE video_id = ?
                """,
                (now, int(video_id)),
            )
            season = connection.execute(
                """
                SELECT representative_video_id, metadata_video_id
                FROM video_seasons WHERE id = ?
                """,
                (int(episode["season_id"]),),
            ).fetchone()
            representative = (
                season["representative_video_id"] if season else None
            )
            metadata_video = (
                season["metadata_video_id"] if season else None
            )
            representative_valid = False
            if representative is not None:
                row = connection.execute(
                    """
                    SELECT id, is_deleted, missing
                    FROM videos WHERE id = ?
                    """,
                    (int(representative),),
                ).fetchone()
                representative_valid = bool(
                    row
                    and not int(row["is_deleted"] or 0)
                    and not int(row["missing"] or 0)
                )
            if not representative_valid:
                representative = int(video_id)
            if metadata_video is None or not representative_valid:
                metadata_video = int(video_id)
            connection.execute(
                """
                UPDATE video_seasons
                SET representative_video_id = ?,
                    metadata_video_id = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    int(representative),
                    int(metadata_video),
                    now,
                    int(episode["season_id"]),
                ),
            )
            connection.commit()
    finally:
        if lock_handle is not None:
            release_library_write_lock(lock_handle)
    if source is None:
        raise VideoRestoreError("视频文件不存在", "source_missing")
    restored = database.get_video_admin(int(video_id)) or {}
    return {
        "video_id": int(video_id),
        "episode_id": int(episode_id),
        "status": "restored",
        "old_path": str(video.get("path") or ""),
        "path": str(source["path"]),
        "storage_kind": source["storage_kind"],
        "storage_volume_id": source["volume_id"],
        "access_level": int(video.get("access_level") or 0),
        "storage_state": restored.get("storage_state", ""),
        "asset_regeneration_queued": True,
    }
