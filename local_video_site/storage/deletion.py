from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from local_video_site.core import config
from local_video_site.storage import volumes as video_storage
from local_video_site import database
from local_video_site.media.playback.engine import playback_cache_path


LOGGER = logging.getLogger(__name__)


def _write_password_file(
    password: str,
    path: Optional[Path] = None,
) -> None:
    path = path or config.DELETE_PASSWORD_PATH
    iterations = config.delete_hash_iterations()
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    payload = {
        "algorithm": "pbkdf2_sha256",
        "iterations": iterations,
        "salt": base64.b64encode(salt).decode("ascii"),
        "password_hash": base64.b64encode(digest).decode("ascii"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(payload, handle, indent=2)
            handle.write("\n")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def ensure_delete_password(path: Optional[Path] = None) -> None:
    """Repair permissions on an existing secret without creating a default."""

    path = path or config.DELETE_PASSWORD_PATH
    if path.is_file():
        try:
            path.chmod(0o600)
        except OSError:
            pass


def delete_password_configured(path: Optional[Path] = None) -> bool:
    return (path or config.DELETE_PASSWORD_PATH).is_file()


def set_delete_password(
    password: str,
    path: Optional[Path] = None,
) -> None:
    if not password:
        raise ValueError("删除校验码不能为空")
    minimum = config.delete_verification_min_length()
    if len(str(password)) < minimum:
        raise ValueError(
            "删除校验码至少需要 {} 个字符".format(minimum)
        )
    _write_password_file(password, path)


def verify_delete_password(
    password: str,
    path: Optional[Path] = None,
) -> bool:
    path = path or config.DELETE_PASSWORD_PATH
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("algorithm") != "pbkdf2_sha256":
            return False
        iterations = int(payload["iterations"])
        salt = base64.b64decode(payload["salt"], validate=True)
        expected = base64.b64decode(
            payload["password_hash"],
            validate=True,
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        (password or "").encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(actual, expected)


def _is_under(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve()
        resolved_root = root.resolve()
    except OSError:
        return False
    return resolved != resolved_root and resolved_root in resolved.parents


def _delete_candidates(video: Dict) -> List[Path]:
    candidates: List[Path] = []
    thumbnail = video.get("thumbnail_path")
    if thumbnail:
        thumbnail_path = Path(thumbnail)
        if thumbnail_path.is_file() and (
            _is_under(thumbnail_path, config.THUMBNAIL_DIR)
            or _is_under(thumbnail_path, config.COVER_DIR)
        ):
            candidates.append(thumbnail_path)

    cache_path = playback_cache_path(video)
    database.delete_playback_cache_entry(cache_path)
    if cache_path.is_file() and _is_under(
        cache_path,
        config.playback_cache_dir(),
    ):
        candidates.append(cache_path)

    preview_path = video.get("preview_path")
    if preview_path:
        preview_file = config.PREVIEW_DIR / Path(preview_path).name
        if preview_file.is_file() and _is_under(
            preview_file,
            config.PREVIEW_DIR,
        ):
            candidates.append(preview_file)

    unique = []
    seen = set()
    for path in candidates:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _move_to_quarantine(
    video_id: int,
    paths: List[Path],
    quarantine_root: Optional[Path] = None,
) -> tuple:
    config.ensure_data_directories()
    quarantine_root = quarantine_root or config.DELETE_QUARANTINE_DIR
    quarantine = (
        quarantine_root
        / "{}-{}".format(video_id, secrets.token_hex(6))
    )
    quarantine.mkdir(parents=True, exist_ok=False)
    moved = []
    try:
        for index, source in enumerate(paths):
            target = quarantine / "{}-{}".format(index, source.name)
            shutil.move(str(source), str(target))
            moved.append((source, target))
    except Exception:
        for source, target in reversed(moved):
            try:
                shutil.move(str(target), str(source))
            except OSError:
                LOGGER.exception("Could not restore quarantined file: %s", source)
        shutil.rmtree(quarantine, ignore_errors=True)
        raise
    return quarantine, moved


def delete_video(
    video_id: int,
    password: str,
) -> Dict:
    if not verify_delete_password(password):
        raise PermissionError("删除校验码错误")
    return delete_video_authorized(video_id)


def delete_video_authorized(video_id: int) -> Dict:
    """Delete a video after the caller has already authorized the operation."""

    video = database.get_video_admin(video_id)
    if not video:
        raise FileNotFoundError("视频不存在")
    if int(video.get("is_deleted") or 0):
        return {
            "deleted_files": 0,
            "video_id": int(video_id),
            "already_deleted": True,
        }
    if database.has_active_migration(video_id):
        raise PermissionError("视频存在活动迁移任务，暂不能删除")
    resolution = video_storage.resolve_video_storage(video)
    video_path = resolution["path"]
    if video_path.is_symlink():
        raise PermissionError("拒绝删除符号链接视频")
    if resolution["storage_kind"] == "external" and not resolution["online"]:
        raise PermissionError("外置存储未连接，暂不能删除视频")
    if video_path.is_file() and not video_storage.is_managed_video_path(
        video,
        video_path,
    ):
        raise PermissionError("视频文件不在允许删除的目录中")
    paths = _delete_candidates(video)
    if resolution["storage_kind"] == "local" and video_path.is_file():
        paths.insert(0, video_path)
    moved = []
    quarantine_dirs = []
    try:
        if resolution["storage_kind"] == "local":
            quarantine, local_moved = _move_to_quarantine(
                video_id,
                paths,
                config.DELETE_QUARANTINE_DIR,
            )
            quarantine_dirs.append(quarantine)
            moved.extend(local_moved)
        else:
            local_paths = [path for path in paths if path.exists()]
            if local_paths:
                quarantine, local_moved = _move_to_quarantine(
                    video_id,
                    local_paths,
                    config.DELETE_QUARANTINE_DIR,
                )
                quarantine_dirs.append(quarantine)
                moved.extend(local_moved)
            if video_path.is_file():
                external_quarantine = video_path.parent / ".delete-quarantine"
                external_quarantine.mkdir(parents=True, exist_ok=True)
                quarantine, external_moved = _move_to_quarantine(
                    video_id,
                    [video_path],
                    external_quarantine,
                )
                quarantine_dirs.append(quarantine)
                moved.extend(external_moved)
        deleted = database.delete_video_record(video_id)
        if not deleted:
            raise RuntimeError("数据库中的视频记录已不存在")
    except Exception:
        for source, target in reversed(moved):
            try:
                if target.exists() and not source.exists():
                    shutil.move(str(target), str(source))
            except OSError:
                LOGGER.exception("Could not roll back deleted file: %s", source)
        for quarantine in quarantine_dirs:
            shutil.rmtree(quarantine, ignore_errors=True)
        raise

    for quarantine in quarantine_dirs:
        try:
            shutil.rmtree(quarantine)
        except OSError:
            LOGGER.exception("Could not purge delete quarantine: %s", quarantine)
    return {
        "deleted_files": len(moved),
        "video_id": int(video_id),
        "already_deleted": False,
    }
