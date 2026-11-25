from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path
from typing import Dict, Optional

from local_video_site.core import config


class ExternalPlaybackTokenError(ValueError):
    pass


def external_token_seconds() -> int:
    return config.external_playback_token_seconds()


def _read_secret(path: Path) -> bytes:
    value = path.read_text(encoding="ascii").strip()
    try:
        secret = bytes.fromhex(value)
    except ValueError as exc:
        raise RuntimeError("外部播放签名密钥格式无效") from exc
    if len(secret) < 32:
        raise RuntimeError("外部播放签名密钥长度不足")
    return secret


def _secret(path: Optional[Path] = None) -> bytes:
    secret_path = path or config.PLAYBACK_TOKEN_SECRET_PATH
    if secret_path.is_file():
        return _read_secret(secret_path)
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            secret_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        return _read_secret(secret_path)
    try:
        os.fchmod(descriptor, 0o600)
        secret = secrets.token_bytes(32)
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            descriptor = -1
            handle.write(secret.hex())
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return secret


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def create_external_playback_token(
    video_id: int,
    *,
    user_id: Optional[int],
    seconds: Optional[int] = None,
    path: Optional[Path] = None,
) -> str:
    lifetime = (
        external_token_seconds()
        if seconds is None
        else int(seconds)
    )
    expires = int(time.time()) + int(lifetime)
    payload = "1|{}|{}|{}|{}".format(
        int(video_id),
        int(user_id or 0),
        expires,
        secrets.token_urlsafe(12),
    ).encode("utf-8")
    encoded = _b64encode(payload)
    signature = hmac.new(
        _secret(path),
        encoded.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return "{}.{}".format(encoded, signature)


def verify_external_playback_token(
    token: str,
    video_id: int,
    *,
    path: Optional[Path] = None,
) -> Dict:
    try:
        encoded, signature = str(token).split(".", 1)
        expected = hmac.new(
            _secret(path),
            encoded.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ExternalPlaybackTokenError("外部播放链接签名无效")
        payload = _b64decode(encoded).decode("utf-8")
        version, token_video_id, user_id, expires, nonce = payload.split("|", 4)
    except ExternalPlaybackTokenError:
        raise
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ExternalPlaybackTokenError("外部播放链接格式无效") from exc
    if version != "1" or int(token_video_id) != int(video_id):
        raise ExternalPlaybackTokenError("外部播放链接与视频不匹配")
    if int(expires) < int(time.time()):
        raise ExternalPlaybackTokenError("外部播放链接已经过期")
    return {
        "video_id": int(token_video_id),
        "user_id": int(user_id) or None,
        "expires_at": int(expires),
        "nonce": nonce,
    }
