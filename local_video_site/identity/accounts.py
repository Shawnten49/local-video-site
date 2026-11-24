from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Dict, Optional

from local_video_site import database
from local_video_site.core import config
from local_video_site.identity.permissions.account import ROLE_ADMIN, ROLE_USER


SESSION_COOKIE_NAME = "lvs_session"


class AccountError(ValueError):
    def __init__(self, message: str, code: str = "account_error"):
        super().__init__(message)
        self.code = code


def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def normalize_username(value: str) -> str:
    import unicodedata

    username = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not username:
        raise AccountError("用户名不能为空", "username_empty")
    if len(username) > 64:
        raise AccountError("用户名不能超过 64 个字符", "username_too_long")
    if any(ord(char) < 32 for char in username):
        raise AccountError("用户名不能包含控制字符", "username_invalid")
    return username.casefold()


def validate_password(password: str) -> None:
    minimum = config.min_password_length()
    if len(str(password or "")) < minimum:
        raise AccountError(
            "密码至少需要 {} 个字符".format(minimum),
            "password_too_short",
        )


def hash_password(
    password: str,
    *,
    enforce_policy: bool = True,
) -> str:
    if enforce_policy:
        validate_password(password)
    iterations = config.password_hash_iterations()
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        str(password).encode("utf-8"),
        salt,
        iterations,
    )
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        salt.hex(),
        digest.hex(),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = str(encoded).split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            str(password).encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(actual.hex(), digest_hex)
    except (TypeError, ValueError):
        return False


def hash_bootstrap_password(password: str) -> str:
    return hash_password(password, enforce_policy=False)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def create_user(
    username: str,
    password: str,
    role: str = ROLE_USER,
    created_by_user_id: Optional[int] = None,
    must_change_password: bool = False,
) -> Dict:
    if role not in {ROLE_USER, ROLE_ADMIN}:
        raise AccountError("账号角色无效", "role_invalid")
    normalized = normalize_username(username)
    user_id = database.create_user(
        username=str(username).strip(),
        normalized_username=normalized,
        password_hash=hash_password(password),
        role=role,
        created_by_user_id=created_by_user_id,
        must_change_password=must_change_password,
    )
    return database.get_user(user_id)


def authenticate(username: str, password: str) -> Optional[Dict]:
    try:
        normalized = normalize_username(username)
    except AccountError:
        return None
    user = database.get_user_by_normalized_username(normalized)
    if not user or not user.get("is_active"):
        return None
    if not verify_password(password, user.get("password_hash") or ""):
        return None
    database.record_user_login(int(user["id"]))
    return database.get_user(int(user["id"]))


def create_session(
    user_id: int,
    remember: bool = False,
    client_ip: str = "",
    user_agent: str = "",
) -> tuple:
    token = secrets.token_urlsafe(48)
    token_hash = hash_session_token(token)
    now = _now()
    expires = now + timedelta(
        days=(
            config.remember_session_days()
            if remember
            else config.session_days()
        )
    )
    session_id = database.create_user_session(
        user_id=int(user_id),
        token_hash=token_hash,
        created_at=_iso(now),
        expires_at=_iso(expires),
        client_ip=client_ip,
        user_agent_hash=hashlib.sha256(
            str(user_agent or "").encode("utf-8")
        ).hexdigest(),
    )
    return session_id, token, expires


def change_own_password(
    user_id: int,
    current_password: str,
    new_password: str,
    keep_session_id: Optional[int] = None,
) -> None:
    user = database.get_user(int(user_id))
    if not user or not verify_password(
        current_password,
        user.get("password_hash") or "",
    ):
        raise AccountError("当前密码错误", "current_password_invalid")
    reset_user_password(
        int(user_id),
        new_password,
        keep_session_id=keep_session_id,
    )


def reset_user_password(
    user_id: int,
    new_password: str,
    keep_session_id: Optional[int] = None,
    must_change_password: bool = False,
) -> None:
    database.update_user_password(
        int(user_id),
        hash_password(new_password),
        must_change_password=must_change_password,
        keep_session_id=keep_session_id,
    )
