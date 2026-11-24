from __future__ import annotations

import logging
import os
import threading
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from local_video_site.downloader.naming import (
    truncate_at_hash,
    truncate_readable_filename,
)

from local_video_site.core import config
from local_video_site.storage import volumes as video_storage
from local_video_site import database
from local_video_site.media.playback.engine import playback_cache_path
from local_video_site.ingestion.library_scan import ScanAlreadyRunning, acquire_library_write_lock, release_library_write_lock


LOGGER = logging.getLogger(__name__)
VIDEO_RENAME_LOCK = threading.Lock()


class VideoRenameError(RuntimeError):
    def __init__(self, message: str, status_code: int = 409, code: str = "rename_failed"):
        self.status_code = int(status_code)
        self.code = code
        super().__init__(message)


def normalize_video_title(value: str, extension: str) -> str:
    title = unicodedata.normalize("NFKC", str(value or ""))
    title = truncate_at_hash(title)
    title = " ".join(title.split()).strip()
    suffix = str(extension or "")
    if suffix and title.lower().endswith(suffix.lower()):
        title = title[:-len(suffix)].rstrip()
    if not title:
        raise VideoRenameError("请输入视频名称", 422, "empty_title")
    if title in {".", ".."} or title.startswith("."):
        raise VideoRenameError(
            "文件名不能以点开头，也不能是 . 或 ..",
            422,
            "invalid_title",
        )
    if title.endswith((" ", ".")):
        raise VideoRenameError(
            "文件名不能以空格或点结尾",
            422,
            "invalid_title",
        )
    if "/" in title or "\\" in title or "\x00" in title:
        raise VideoRenameError(
            "文件名不能包含 /、\\ 或空字符",
            422,
            "invalid_title",
        )
    if any(ord(character) < 32 for character in title):
        raise VideoRenameError(
            "文件名不能包含控制字符",
            422,
            "invalid_title",
        )
    title = truncate_readable_filename(title)
    if not title:
        raise VideoRenameError("视频名称无效", 422, "invalid_title")
    return title


def normalize_display_title(value: str) -> str:
    title = unicodedata.normalize("NFKC", str(value or ""))
    title = " ".join(title.split()).strip()
    if not title:
        raise VideoRenameError(
            "请输入展示标题",
            422,
            "empty_display_title",
        )
    if any(ord(character) < 32 for character in title):
        raise VideoRenameError(
            "展示标题不能包含控制字符",
            422,
            "invalid_display_title",
        )
    maximum = config.max_display_title_chars()
    if len(title) > maximum:
        raise VideoRenameError(
            "展示标题不能超过 {} 个字符".format(
                maximum
            ),
            422,
            "display_title_too_long",
        )
    return title


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _is_regular_library_file(path: Path, video: Dict) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    return video_storage.is_managed_video_path(video, path)


def _casefold_same_parent(path: Path, directory: Path) -> bool:
    try:
        return Path(path).parent.resolve() == directory.resolve()
    except OSError:
        return False


def _find_disk_collision(
    old_path: Path,
    target_path: Path,
) -> Optional[Path]:
    directory = old_path.parent
    target_key = target_path.name.casefold()
    try:
        entries = list(os.scandir(directory))
    except OSError as exc:
        raise VideoRenameError(
            "无法读取视频目录: {}".format(exc),
            500,
            "directory_unreadable",
        ) from exc
    for entry in entries:
        if entry.name.casefold() != target_key:
            continue
        candidate = Path(entry.path)
        if candidate == old_path:
            continue
        return candidate
    return None


def _find_database_collision(
    video_id: int,
    target_path: Path,
) -> Optional[Dict]:
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT id, path FROM videos WHERE id <> ?",
            (int(video_id),),
        ).fetchall()
    for row in rows:
        candidate = Path(row["path"])
        if (
            _casefold_same_parent(candidate, target_path.parent)
            and candidate.name.casefold() == target_path.name.casefold()
        ):
            return dict(row)
    return None


def _create_journal(
    video_id: int,
    old_path: Path,
    new_path: Path,
    title: str,
) -> int:
    now = _now()
    with database.connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO video_rename_operations (
                video_id, old_path, new_path, new_title, status,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'prepared', ?, ?)
            """,
            (
                int(video_id),
                str(old_path),
                str(new_path),
                title,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)


def _delete_journal(journal_id: int) -> None:
    with database.connect() as connection:
        connection.execute(
            "DELETE FROM video_rename_operations WHERE id = ?",
            (int(journal_id),),
        )


def _update_database(
    journal_id: int,
    video_id: int,
    old_path: Path,
    new_path: Path,
) -> None:
    now = _now()
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        volume = connection.execute(
            """
            SELECT sv.*
            FROM videos v
            LEFT JOIN video_storage_volumes sv
              ON sv.id = v.storage_volume_id
            WHERE v.id = ?
            """,
            (int(video_id),),
        ).fetchone()
        storage_relative_path = None
        if volume is not None and volume["id"] is not None:
            try:
                storage_relative_path = str(
                    new_path.resolve().relative_to(
                        video_storage.volume_root(dict(volume)).resolve()
                    )
                )
            except (OSError, ValueError):
                raise VideoRenameError(
                    "外置视频路径超出存储卷",
                    409,
                    "storage_path_invalid",
                )
        cursor = connection.execute(
            """
            UPDATE videos
            SET path = ?, directory = ?, extension = ?,
                display_title_override = COALESCE(
                    NULLIF(display_title_override, ''),
                    title
                ),
                storage_relative_path = COALESCE(?, storage_relative_path),
                updated_at = ?
            WHERE id = ? AND missing = 0 AND is_deleted = 0
            """,
            (
                str(new_path),
                new_path.parent.name,
                new_path.suffix.lower(),
                storage_relative_path,
                now,
                int(video_id),
            ),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            raise VideoRenameError(
                "视频路径已发生变化，请刷新页面后重试",
                409,
                "stale_video_state",
            )
        connection.execute(
            """
            UPDATE download_tasks
            SET file_path = ?, file_name = ?, updated_at = ?
            WHERE video_id = ? AND deleted_at IS NULL
            """,
            (
                str(new_path),
                new_path.name,
                now,
                int(video_id),
            ),
        )
        connection.execute(
            """
            UPDATE video_episodes
            SET absolute_path = ?,
                relative_path = (
                    SELECT s.relative_directory || '/' || ?
                    FROM video_seasons s
                    WHERE s.id = video_episodes.season_id
                ),
                updated_at = ?
            WHERE video_id = ?
            """,
            (
                str(new_path),
                new_path.name,
                now,
                int(video_id),
            ),
        )
        if storage_relative_path is None:
            connection.execute(
                """
                UPDATE video_migrations
                SET destination_path = ?, updated_at = ?
                WHERE video_id = ?
                  AND direction = 'to_local'
                  AND status = 'success'
                """,
                (str(new_path), now, int(video_id)),
            )
            imported = connection.execute(
                """
                SELECT id, destination_root
                FROM video_import_jobs
                WHERE video_id = ?
                  AND status = 'success'
                ORDER BY id DESC
                LIMIT 1
                """,
                (int(video_id),),
            ).fetchone()
            if imported is not None:
                try:
                    imported_relative = new_path.resolve().relative_to(
                        Path(imported["destination_root"]).resolve()
                    )
                except (OSError, ValueError):
                    raise VideoRenameError(
                        "导入视频路径超出目标目录",
                        409,
                        "import_path_invalid",
                    )
                connection.execute(
                    """
                    UPDATE video_import_jobs
                    SET destination_relative_path = ?,
                        destination_path = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        str(imported_relative),
                        str(new_path),
                        now,
                        int(imported["id"]),
                    ),
                )
        connection.execute(
            "DELETE FROM playback_cache_entries WHERE video_id = ?",
            (int(video_id),),
        )
        connection.execute(
            """
            UPDATE video_rename_operations
            SET status = 'db_committed', updated_at = ?
            WHERE id = ?
            """,
            (now, int(journal_id)),
        )
        connection.commit()


def _rollback_file_change(
    old_path: Path,
    new_path: Path,
    *,
    linked: bool,
) -> None:
    try:
        if linked and new_path.exists():
            os.unlink(new_path)
        elif new_path.exists() and not old_path.exists():
            os.rename(new_path, old_path)
    except OSError:
        LOGGER.exception(
            "Video rename file rollback failed old=%s new=%s",
            old_path,
            new_path,
        )


def _cleanup_temporary_paths(directory: Path) -> None:
    try:
        for path in directory.glob(".video-rename-*.tmp"):
            try:
                path.unlink()
            except OSError:
                continue
    except OSError:
        return


def update_video_display_title(
    video_id: int,
    requested_title: Optional[str],
    expected_updated_at: str = "",
) -> Dict:
    display_title = (
        None
        if requested_title is None
        else normalize_display_title(requested_title)
    )
    try:
        return database.set_video_display_title(
            int(video_id),
            display_title,
            expected_updated_at=expected_updated_at,
        )
    except ValueError as exc:
        message = str(exc)
        if message == "视频不存在":
            raise VideoRenameError(message, 404, "video_not_found") from exc
        if message == "视频已删除":
            raise VideoRenameError(message, 409, "video_deleted") from exc
        raise VideoRenameError(
            "视频信息已发生变化，请刷新页面后重试",
            409,
            "stale_video_state",
        ) from exc


def rename_video(
    video_id: int,
    requested_title: str,
    expected_path: str = "",
) -> Dict:
    if not VIDEO_RENAME_LOCK.acquire(blocking=False):
        raise VideoRenameError(
            "另一个视频正在重命名，请稍后重试",
            409,
            "rename_busy",
        )
    lock_handle = None
    journal_id = None
    linked = False
    try:
        try:
            lock_handle = acquire_library_write_lock()
        except ScanAlreadyRunning as exc:
            raise VideoRenameError(
                "视频库正在扫描，请稍后重试",
                409,
                "scan_running",
            ) from exc

        video = database.get_video_admin(int(video_id))
        if video is None:
            raise VideoRenameError("视频不存在", 404, "video_not_found")
        if video.get("is_deleted"):
            raise VideoRenameError(
                "视频已删除，不能重命名",
                409,
                "video_deleted",
            )
        resolution = video_storage.resolve_video_storage(video)
        raw_old_path = Path(str(video["path"]))
        try:
            same_path = raw_old_path.resolve() == resolution["path"].resolve()
        except OSError:
            same_path = False
        old_path = raw_old_path if same_path else resolution["path"]
        if resolution["state"] == "external_offline":
            raise VideoRenameError(
                "外置存储未连接，暂不能重命名",
                409,
                "storage_offline",
            )
        if expected_path and (
            old_path.resolve() != Path(expected_path).expanduser().resolve()
        ):
            raise VideoRenameError(
                "视频路径已发生变化，请刷新页面后重试",
                409,
                "stale_video_state",
            )
        if not _is_regular_library_file(old_path, video):
            raise VideoRenameError(
                "视频文件不存在、不是普通文件或不在视频库目录中",
                404,
                "video_file_missing",
            )
        extension = old_path.suffix
        title = normalize_video_title(requested_title, extension)
        target_path = old_path.with_name(title + extension)

        if target_path == old_path:
            return {
                "status": "unchanged",
                "id": int(video_id),
                "title": video["title"],
                "title_changed": False,
                "path": str(old_path),
                "file_name": old_path.name,
                "file_stem": old_path.stem,
            }

        collision = _find_disk_collision(old_path, target_path)
        if collision is not None:
            raise VideoRenameError(
                "当前目录已经存在同名文件：{}".format(collision.name),
                409,
                "duplicate_file",
            )
        database_collision = _find_database_collision(
            int(video_id),
            target_path,
        )
        if database_collision is not None:
            raise VideoRenameError(
                "已有视频记录使用相同路径",
                409,
                "duplicate_database_path",
            )

        old_cache = playback_cache_path(video)
        journal_id = _create_journal(
            int(video_id),
            old_path,
            target_path,
            title,
        )
        case_only = (
            old_path.name.casefold() == target_path.name.casefold()
            and old_path.name != target_path.name
        )
        try:
            if case_only:
                temporary = old_path.with_name(
                    ".video-rename-{}{}".format(
                        uuid.uuid4().hex,
                        extension,
                    )
                )
                os.rename(old_path, temporary)
                os.rename(temporary, target_path)
            else:
                os.link(old_path, target_path)
                linked = True
        except FileExistsError as exc:
            _rollback_file_change(
                old_path,
                target_path,
                linked=linked,
            )
            raise VideoRenameError(
                "当前目录已经存在同名文件",
                409,
                "duplicate_file",
            ) from exc
        except OSError as exc:
            _rollback_file_change(
                old_path,
                target_path,
                linked=linked,
            )
            raise VideoRenameError(
                "无法重命名视频文件: {}".format(exc),
                500,
                "file_rename_failed",
            ) from exc

        try:
            _update_database(
                journal_id,
                int(video_id),
                old_path,
                target_path,
            )
        except Exception:
            _rollback_file_change(
                old_path,
                target_path,
                linked=linked,
            )
            if old_path.exists():
                _delete_journal(journal_id)
            raise

        journal_can_clear = True
        if linked:
            try:
                os.unlink(old_path)
            except OSError:
                journal_can_clear = False
                LOGGER.exception(
                    "Video rename left old hardlink for recovery video_id=%s "
                    "old_path=%s new_path=%s",
                    video_id,
                    old_path,
                    target_path,
                )

        try:
            old_cache.unlink(missing_ok=True)
        except OSError:
            LOGGER.warning(
                "Could not remove old playback cache: %s",
                old_cache,
            )
        _cleanup_temporary_paths(old_path.parent)
        if journal_can_clear:
            _delete_journal(journal_id)
        LOGGER.info(
            "Video file renamed video_id=%s old_path=%s new_path=%s",
            video_id,
            old_path,
            target_path,
            extra={"operation": "video.rename.success"},
        )
        return {
            "status": "ok",
            "id": int(video_id),
            "title": video["title"],
            "title_changed": False,
            "path": str(target_path),
            "file_name": target_path.name,
            "file_stem": target_path.stem,
        }
    finally:
        if lock_handle is not None:
            release_library_write_lock(lock_handle)
        VIDEO_RENAME_LOCK.release()


def recover_interrupted_renames(
    logger: Optional[logging.Logger] = None,
) -> int:
    logger = logger or LOGGER
    try:
        lock_handle = acquire_library_write_lock()
    except ScanAlreadyRunning:
        return 0
    recovered = 0
    try:
        with database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM video_rename_operations ORDER BY id ASC"
            ).fetchall()
            journal = [dict(row) for row in rows]
        for item in journal:
            old_path = Path(item["old_path"])
            new_path = Path(item["new_path"])
            video = database.get_video(int(item["video_id"]))
            current_path = Path(video["path"]) if video else None
            try:
                if current_path == new_path:
                    if old_path.exists() and old_path != new_path:
                        os.unlink(old_path)
                elif current_path == old_path:
                    if new_path.exists() and new_path != old_path:
                        os.unlink(new_path)
                _delete_journal(int(item["id"]))
                recovered += 1
            except OSError:
                logger.exception(
                    "Video rename recovery failed journal_id=%s",
                    item["id"],
                )
        if recovered:
            logger.info(
                "Recovered interrupted video renames count=%s",
                recovered,
                extra={"operation": "video.rename.recovery"},
            )
        return recovered
    finally:
        release_library_write_lock(lock_handle)
