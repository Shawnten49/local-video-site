from __future__ import annotations

import json
import hashlib
import os
import plistlib
import shutil
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from local_video_site.core import config
from local_video_site import database


STORAGE_MARKER = ".local-video-site-storage.json"
_VOLUME_REFRESH_LOCK = threading.Lock()
STORAGE_STATE_LABELS = {
    "local_online": "本地",
    "external_online": "外置盘在线",
    "external_offline": "外置盘离线",
    "external_unknown": "外置盘待检测",
    "file_missing": "文件缺失",
    "migrating": "迁移中",
    "deleted": "已删除",
}


class StorageError(RuntimeError):
    def __init__(self, message: str, code: str = "storage_error"):
        self.code = code
        super().__init__(message)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _is_under(path: Path, root: Path) -> bool:
    try:
        path = path.resolve()
        root = root.resolve()
    except OSError:
        return False
    return path == root or root in path.parents


def _selected_volume_root(path: Path) -> Path:
    resolved = path.resolve()
    parts = resolved.parts
    if len(parts) >= 3 and parts[1] == "Volumes":
        return Path(parts[0]) / parts[1] / parts[2]
    return resolved


def _external_mount_for(path: Path) -> Optional[Path]:
    try:
        resolved = path.resolve()
    except OSError:
        return None
    for root in config.external_volume_roots():
        try:
            root = root.resolve()
            relative = resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        if not relative.parts:
            continue
        return root / relative.parts[0]
    return None


def _volume_uuid(mount_path: Path) -> str:
    if shutil.which("diskutil") is None:
        return ""
    try:
        completed = subprocess.run(
            ["diskutil", "info", "-plist", str(mount_path)],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0 or not completed.stdout:
        return ""
    try:
        payload = plistlib.loads(completed.stdout)
    except (ValueError, plistlib.InvalidFileException):
        return ""
    return str(
        payload.get("VolumeUUID")
        or payload.get("MediaUUID")
        or ""
    ).strip()


def _fallback_volume_key(mount_path: Path) -> str:
    try:
        device = str(os.stat(mount_path).st_dev)
    except OSError:
        device = ""
    token = "{}:{}".format(device, mount_path.name.casefold()).encode(
        "utf-8"
    )
    return "readonly-{}-{}".format(
        device or "unknown",
        hashlib.sha256(token).hexdigest()[:16],
    )


def _readonly_identity(mount_path: Path) -> Dict:
    marker = _read_marker(mount_path)
    if marker:
        return {
            "storage_key": marker["storage_key"],
            "identity_source": "marker",
            "volume_uuid": _volume_uuid(mount_path),
        }
    volume_uuid = _volume_uuid(mount_path)
    if volume_uuid:
        return {
            "storage_key": "volume-{}".format(
                hashlib.sha256(volume_uuid.encode("utf-8")).hexdigest()
            ),
            "identity_source": "volume_uuid",
            "volume_uuid": volume_uuid,
        }
    return {
        "storage_key": _fallback_volume_key(mount_path),
        "identity_source": "device_label",
        "volume_uuid": "",
    }


def register_external_source(raw_path: str) -> Dict:
    path = Path(raw_path or "").expanduser()
    if not path.is_absolute():
        raise StorageError("来源路径必须是绝对路径", "source_not_absolute")
    try:
        path = path.resolve()
    except OSError as exc:
        raise StorageError(
            "无法解析来源路径: {}".format(exc),
            "source_unreadable",
        ) from exc
    if not path.exists() or path.is_symlink():
        raise StorageError("来源文件或目录不存在", "source_missing")
    mount_path = _external_mount_for(path)
    if mount_path is None or not mount_path.is_dir():
        raise StorageError(
            "请选择外置存储中的文件或目录",
            "source_not_external",
        )
    if path == mount_path:
        raise StorageError(
            "请进入外置盘中的具体目录后再选择",
            "source_is_volume_root",
        )
    identity = _readonly_identity(mount_path)
    try:
        source_relative = str(path.relative_to(mount_path))
    except ValueError as exc:
        raise StorageError(
            "无法计算外盘相对路径",
            "source_relative_path_invalid",
        ) from exc
    try:
        volume_uuid = str(os.stat(mount_path).st_dev)
    except OSError:
        volume_uuid = identity.get("volume_uuid") or ""
    now = _now()
    access_mode = "read_write" if os.access(mount_path, os.W_OK) else "read_only"
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM video_storage_volumes WHERE storage_key = ?",
            (identity["storage_key"],),
        ).fetchone()
        if row is None:
            cursor = connection.execute(
                """
                INSERT INTO video_storage_volumes (
                    storage_key, label, volume_uuid, mount_path,
                    root_relative_path, identity_source, access_mode,
                    online_state, last_probe_at, is_active, last_seen_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, '', ?, ?, 'online', ?, 1, ?, ?, ?)
                """,
                (
                    identity["storage_key"],
                    mount_path.name or "外置存储",
                    identity.get("volume_uuid") or volume_uuid,
                    str(mount_path),
                    identity["identity_source"],
                    access_mode,
                    now,
                    now,
                    now,
                    now,
                ),
            )
            volume_id = int(cursor.lastrowid)
        else:
            volume_id = int(row["id"])
            connection.execute(
                """
                UPDATE video_storage_volumes
                SET label = ?, volume_uuid = COALESCE(NULLIF(?, ''), volume_uuid),
                    mount_path = ?, identity_source = ?, access_mode = ?,
                    online_state = 'online', last_probe_at = ?,
                    last_probe_error = NULL, is_active = 1,
                    last_seen_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    mount_path.name or "外置存储",
                    identity.get("volume_uuid") or volume_uuid,
                    str(mount_path),
                    identity["identity_source"],
                    access_mode,
                    now,
                    now,
                    now,
                    volume_id,
                ),
            )
        connection.commit()
        volume = connection.execute(
            "SELECT * FROM video_storage_volumes WHERE id = ?",
            (volume_id,),
        ).fetchone()
    return {
        "volume": dict(volume),
        "mount_path": mount_path,
        "source_relative_path": source_relative,
    }


def _validate_writable_directory(path: Path) -> Path:
    raw = str(path or "").strip()
    if not raw:
        raise StorageError("请选择目标文件夹", "target_required")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise StorageError("目标文件夹必须是绝对路径", "target_not_absolute")
    path = path.resolve()
    if path == Path("/"):
        raise StorageError("不能直接使用系统根目录", "target_unsafe")
    if not path.is_dir():
        raise StorageError("目标文件夹不存在", "target_not_directory")
    probe = path / ".video-migration-write-test-{}".format(uuid.uuid4().hex)
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as exc:
        raise StorageError(
            "目标文件夹不可写: {}".format(exc),
            "target_not_writable",
        ) from exc
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    return path


def local_root_for(path: Path) -> Optional[Path]:
    try:
        resolved = path.resolve()
    except OSError:
        return None
    candidates = []
    for root in config.configured_roots():
        try:
            root = root.resolve()
        except OSError:
            continue
        if _is_under(resolved, root):
            candidates.append(root)
    if not candidates:
        return None
    return max(candidates, key=lambda item: len(item.parts))


def validate_target(
    direction: str,
    raw_path: str,
    source_volume: Optional[Dict] = None,
) -> Path:
    path = _validate_writable_directory(Path(raw_path))
    if direction not in {"to_external", "to_local"}:
        raise StorageError("不支持的迁移方向", "invalid_direction")
    if direction == "to_external":
        unsafe_roots = [
            config.DATA_DIR,
            config.PROJECT_ROOT / ".git",
            config.PROJECT_ROOT / ".venv",
            *config.configured_roots(),
        ]
        if any(_is_under(path, root) for root in unsafe_roots):
            raise StorageError(
                "迁移目标不能位于本地视频目录或应用数据目录",
                "target_inside_local_storage",
            )
    elif source_volume is not None:
        mount_path = Path(source_volume["mount_path"])
        if _is_under(path, mount_path):
            raise StorageError(
                "迁回本地时不能选择当前外置盘中的目录",
                "target_inside_source_volume",
            )
    return path


def _read_marker(mount_path: Path) -> Optional[Dict]:
    marker = mount_path / STORAGE_MARKER
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    key = str(payload.get("storage_key") or "").strip()
    if not key:
        return None
    return {"storage_key": key}


def _write_marker(mount_path: Path, storage_key: str) -> None:
    marker = mount_path / STORAGE_MARKER
    try:
        existing = _read_marker(mount_path)
    except OSError:
        existing = None
    if existing:
        if existing["storage_key"] != storage_key:
            raise StorageError(
                "目标磁盘已经属于另一个视频存储卷",
                "storage_marker_conflict",
            )
        return
    temporary = mount_path / ".{}.tmp".format(STORAGE_MARKER)
    try:
        temporary.write_text(
            json.dumps(
                {"storage_key": storage_key, "version": 1},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, marker)
    except OSError as exc:
        raise StorageError(
            "无法初始化目标存储卷: {}".format(exc),
            "storage_marker_failed",
        ) from exc
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def register_external_target(raw_path: str) -> Dict:
    target_root = validate_target("to_external", raw_path)
    mount_path = Path(
        _selected_volume_root(target_root)
    ).expanduser().resolve()
    try:
        target_root.relative_to(mount_path)
    except ValueError as exc:
        raise StorageError(
            "迁移目标不在外置存储卷内",
            "storage_root_invalid",
        ) from exc
    marker = _read_marker(mount_path)
    storage_key = (
        marker["storage_key"]
        if marker
        else "storage-{}".format(uuid.uuid4().hex)
    )
    _write_marker(mount_path, storage_key)
    now = _now()
    try:
        volume_uuid = str(os.stat(mount_path).st_dev)
    except OSError:
        volume_uuid = ""
    with database.connect() as connection:
        row = connection.execute(
            "SELECT * FROM video_storage_volumes WHERE storage_key = ?",
            (storage_key,),
        ).fetchone()
        if row is None:
            cursor = connection.execute(
                """
                INSERT INTO video_storage_volumes (
                    storage_key, label, volume_uuid, mount_path,
                    root_relative_path, online_state, last_probe_at,
                    is_active, last_seen_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'online', ?, 1, ?, ?, ?)
                """,
                (
                    storage_key,
                    mount_path.name or "外置存储",
                    volume_uuid,
                    str(mount_path),
                    "",
                    now,
                    now,
                    now,
                    now,
                ),
            )
            volume_id = int(cursor.lastrowid)
        else:
            volume_id = int(row["id"])
            connection.execute(
                """
                UPDATE video_storage_volumes
                SET label = ?, volume_uuid = ?, mount_path = ?,
                    root_relative_path = '', online_state = 'online',
                    last_probe_at = ?, last_probe_error = NULL,
                    is_active = 1, last_seen_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    mount_path.name or row["label"],
                    volume_uuid,
                    str(mount_path),
                    now,
                    now,
                    now,
                    volume_id,
                ),
            )
    volume = database.get_storage_volume(volume_id)
    if volume is None:
        raise StorageError("外置存储卷登记失败", "storage_volume_missing")
    return {
        **volume,
        "selected_path": str(target_root),
    }


def volume_root(volume: Dict) -> Path:
    return (
        Path(volume["mount_path"])
        / str(volume.get("root_relative_path") or "")
    )


def volume_online(volume: Optional[Dict]) -> bool:
    if not volume or not int(volume.get("is_active") or 0):
        return False
    mount_path = Path(volume["mount_path"])
    marker = _read_marker(mount_path)
    if marker and marker["storage_key"] == volume["storage_key"]:
        return volume_root(volume).is_dir()
    identity_source = str(volume.get("identity_source") or "marker")
    if identity_source == "marker":
        return False
    identity = _readonly_identity(mount_path)
    if identity["storage_key"] != volume["storage_key"]:
        return False
    return volume_root(volume).is_dir()


def _update_volume_probe_state(
    volume_id: int,
    online: bool,
    error: Optional[str] = None,
) -> None:
    now = _now()
    with database.connect() as connection:
        connection.execute(
            """
            UPDATE video_storage_volumes
            SET online_state = ?, last_probe_at = ?,
                last_probe_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                "online" if online else "offline",
                now,
                None if online else (error or "存储卷未连接"),
                now,
                int(volume_id),
            ),
        )


def refresh_storage_volumes() -> int:
    if not _VOLUME_REFRESH_LOCK.acquire(blocking=False):
        return 0
    try:
        return _refresh_storage_volumes_locked()
    finally:
        _VOLUME_REFRESH_LOCK.release()


def _refresh_storage_volumes_locked() -> int:
    refreshed = 0
    for volume in database.list_storage_volumes():
        old_mount = Path(volume["mount_path"])
        old_relative = str(volume.get("root_relative_path") or "")
        mount_path = old_mount
        if not volume_online(volume):
            for volume_root_path in config.external_volume_roots():
                try:
                    children = list(volume_root_path.iterdir())
                except OSError:
                    continue
                for child in children:
                    if child.is_symlink() or not child.is_dir():
                        continue
                    marker = _read_marker(child)
                    if (
                        marker
                        and marker["storage_key"] == volume["storage_key"]
                    ):
                        mount_path = child
                        break
                    if marker:
                        continue
                    identity = _readonly_identity(child)
                    if (
                        identity["storage_key"]
                        == volume["storage_key"]
                    ):
                        mount_path = child
                        break
                if mount_path != old_mount:
                    break
        updated_volume = {
            **volume,
            "mount_path": str(mount_path),
        }
        if not volume_online(updated_volume):
            _update_volume_probe_state(
                int(volume["id"]),
                False,
            )
            continue
        _update_volume_probe_state(int(volume["id"]), True)
        with database.connect() as connection:
            connection.execute(
                """
                UPDATE video_storage_volumes
                SET mount_path = ?, online_state = 'online',
                    last_probe_at = ?, last_probe_error = NULL,
                    last_seen_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    str(mount_path),
                    _now(),
                    _now(),
                    _now(),
                    int(volume["id"]),
                ),
            )
            if mount_path != old_mount or old_relative != volume["root_relative_path"]:
                rows = connection.execute(
                    """
                    SELECT id, storage_relative_path
                    FROM videos
                    WHERE storage_volume_id = ?
                      AND storage_relative_path IS NOT NULL
                    """,
                    (int(volume["id"]),),
                ).fetchall()
                for row in rows:
                    new_path = (
                        mount_path
                        / str(volume.get("root_relative_path") or "")
                        / str(row["storage_relative_path"])
                    )
                    connection.execute(
                        "UPDATE videos SET path = ?, updated_at = ? WHERE id = ?",
                        (str(new_path), _now(), int(row["id"])),
                    )
                    connection.execute(
                        """
                        UPDATE video_episodes
                        SET absolute_path = ?, updated_at = ?
                        WHERE video_id = ?
                        """,
                        (str(new_path), _now(), int(row["id"])),
                    )
                    connection.execute(
                        """
                        UPDATE download_tasks
                        SET file_path = ?, updated_at = ?
                        WHERE video_id = ?
                        """,
                        (str(new_path), _now(), int(row["id"])),
                    )
            refreshed += 1
    return refreshed


class StorageVolumeWorker:
    def __init__(
        self,
        stop_event: threading.Event,
        logger=None,
    ) -> None:
        self.stop_event = stop_event
        self.logger = logger
        self.thread: Optional[threading.Thread] = None

    def start(self):
        self.thread = threading.Thread(
            target=self.run,
            name="storage-volume-worker",
            daemon=True,
        )
        self.thread.start()
        return self

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                refreshed = refresh_storage_volumes()
                if self.logger is not None and refreshed:
                    self.logger.info(
                        "Storage volume probe completed online=%s",
                        refreshed,
                        extra={"operation": "storage.volume.probe"},
                    )
            except Exception:
                if self.logger is not None:
                    self.logger.exception(
                        "Storage volume probe failed"
                    )
            self.stop_event.wait(
                config.storage_volume_refresh_seconds()
            )

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=5)


def resolve_video_storage(video: Dict) -> Dict:
    volume_id = video.get("storage_volume_id")
    if volume_id is None:
        path = Path(str(video.get("path") or "")).resolve()
        state = "local_online" if path.is_file() else "file_missing"
        return {
            "path": path,
            "state": state,
            "storage_kind": "local",
            "volume": None,
            "online": state == "local_online",
        }
    volume = database.get_storage_volume(int(volume_id))
    relative = str(video.get("storage_relative_path") or "")
    if volume is None:
        return {
            "path": Path(str(video.get("path") or "")),
            "state": "external_offline",
            "storage_kind": "external",
            "volume": None,
            "online": False,
        }
    path = volume_root(volume) / relative
    if not volume_online(volume):
        state = "external_offline"
    elif path.is_file():
        state = "external_online"
    else:
        state = "file_missing"
    return {
        "path": path,
        "state": state,
        "storage_kind": "external",
        "volume": volume,
        "online": state == "external_online",
    }


def decorate_video(video: Dict, active_ids: Optional[set] = None) -> Dict:
    item = dict(video)
    resolution = resolve_video_storage(item)
    state = "deleted" if int(item.get("is_deleted") or 0) else resolution["state"]
    if not item.get("is_deleted") and active_ids and int(item["id"]) in active_ids:
        state = "migrating"
    item.update({
        "resolved_path": str(resolution["path"]),
        "storage_state": state,
        "storage_state_label": STORAGE_STATE_LABELS.get(state, state),
        "storage_online": bool(resolution["online"]),
        "storage_volume": resolution["volume"],
    })
    return item


def decorate_video_cached(
    video: Dict,
    active_ids: Optional[set] = None,
) -> Dict:
    """Decorate for list views without probing files or external volumes."""

    item = dict(video)
    volume_id = item.get("storage_volume_id")
    if int(item.get("is_deleted") or 0):
        state = "deleted"
    elif active_ids and int(item["id"]) in active_ids:
        state = "migrating"
    elif volume_id is not None:
        cached_state = str(item.get("storage_online_state") or "unknown")
        if cached_state == "online":
            state = "external_online"
        elif cached_state == "offline":
            state = "external_offline"
        else:
            state = "external_unknown"
    elif int(item.get("missing") or 0):
        state = "file_missing"
    else:
        state = "local_online"
    if volume_id is None:
        resolved_path = Path(str(item.get("path") or ""))
    else:
        resolved_path = (
            Path(str(item.get("storage_mount_path") or ""))
            / str(item.get("storage_root_relative_path") or "")
            / str(item.get("storage_relative_path") or "")
        )
    item.update({
        "resolved_path": str(resolved_path),
        "storage_state": state,
        "storage_state_label": STORAGE_STATE_LABELS.get(state, state),
        "storage_online": state in {"local_online", "external_online"},
    })
    return item


def active_migration_video_ids() -> set:
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT video_id FROM video_migrations
            WHERE video_id IS NOT NULL
              AND status IN ('pending', 'running', 'needs_cleanup')
            """
        ).fetchall()
    return {int(row["video_id"]) for row in rows}


def logical_relative_path(video: Dict) -> Tuple[str, str]:
    resolution = resolve_video_storage(video)
    source_path = resolution["path"]
    if resolution["storage_kind"] == "external":
        volume = resolution["volume"]
        if volume is None:
            raise StorageError("外置存储卷不可用", "storage_offline")
        relative = str(video.get("storage_relative_path") or "")
    else:
        root = local_root_for(source_path)
        if root is None:
            raise StorageError(
                "本地视频不在受管理的视频目录中",
                "source_not_managed",
            )
        relative = str(Path(root.name) / source_path.relative_to(root))
    if not relative or Path(relative).is_absolute():
        raise StorageError("视频相对路径无效", "invalid_relative_path")
    if ".." in Path(relative).parts:
        raise StorageError("视频相对路径不能包含上级目录", "invalid_relative_path")
    return relative, str(source_path)


def destination_path_for(
    direction: str,
    video: Dict,
    target_root: Path,
) -> Tuple[Path, str]:
    relative, _ = logical_relative_path(video)
    target_root = target_root.resolve()
    destination = target_root / relative
    try:
        destination.resolve().relative_to(target_root)
    except (OSError, ValueError) as exc:
        raise StorageError(
            "目标路径超出了所选目录",
            "destination_outside_root",
        ) from exc
    return destination, relative


def is_managed_video_path(video: Dict, path: Path) -> bool:
    resolution = resolve_video_storage(video)
    if not resolution["online"]:
        return False
    try:
        if path.resolve() != resolution["path"].resolve():
            return False
    except OSError:
        return False
    if resolution["storage_kind"] == "external":
        return True
    root = local_root_for(path)
    if root is not None:
        return True
    with database.connect() as connection:
        row = connection.execute(
            """
            SELECT 1 FROM video_migrations
            WHERE video_id = ?
              AND direction = 'to_local'
              AND status = 'success'
              AND destination_path = ?
            UNION ALL
            SELECT 1 FROM video_import_jobs
            WHERE video_id = ?
              AND status = 'success'
              AND destination_path = ?
            LIMIT 1
            """,
            (
                int(video["id"]),
                str(path),
                int(video["id"]),
                str(path),
            ),
        ).fetchone()
    return row is not None


def list_directories(raw_path: str) -> Dict:
    path = Path(raw_path or "/Volumes").expanduser()
    if not path.is_absolute():
        raise StorageError("目录路径必须是绝对路径", "path_not_absolute")
    path = path.resolve()
    if not path.is_dir():
        raise StorageError("目录不存在", "directory_not_found")
    items = []
    try:
        entries = sorted(
            path.iterdir(),
            key=lambda item: (not item.is_dir(), item.name.casefold()),
        )
    except OSError as exc:
        raise StorageError(
            "无法读取目录: {}".format(exc),
            "directory_unreadable",
        ) from exc
    for item in entries:
        if not item.is_dir() or item.name.startswith("."):
            continue
        try:
            writable = os.access(item, os.W_OK)
        except OSError:
            writable = False
        items.append({
            "name": item.name,
            "path": str(item),
            "writable": writable,
        })
    usage = shutil.disk_usage(path)
    return {
        "path": str(path),
        "parent": str(path.parent) if path.parent != path else "",
        "items": items,
        "free_bytes": int(usage.free),
    }
