from __future__ import annotations

from typing import Dict

from local_video_site import database
from local_video_site.core import config
from .accounts import (
    hash_bootstrap_password,
    normalize_username,
    verify_password,
)


DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "123456"


def ensure_bootstrap_admin(
    username: str = "",
    password: str = "",
) -> Dict:
    if not config.auto_bootstrap_admin_enabled():
        return {
            "status": "disabled",
            "username": "",
            "user_id": None,
        }
    requested_username = str(username or "").strip()
    requested_password = str(password or "")
    source = "environment"
    if not requested_username:
        requested_username = config.bootstrap_admin_username()
        source = "default"
    if not requested_password:
        requested_password = config.bootstrap_admin_password()
        if requested_password == DEFAULT_ADMIN_PASSWORD:
            source = "default"
    normalized = normalize_username(requested_username)
    status, user_id = database.create_initial_admin_if_empty(
        username=requested_username,
        normalized_username=normalized,
        password_hash=hash_bootstrap_password(requested_password),
    )
    if status != "created" or user_id is None:
        existing = database.get_user_by_normalized_username(
            DEFAULT_ADMIN_USERNAME
        )
        return {
            "status": (
                "already_exists"
                if existing is not None
                else "skipped_users_exist"
            ),
            "username": requested_username,
            "user_id": None,
        }
    database.migrate_legacy_favorites(user_id)
    database.write_auth_audit(
        action="auth.bootstrap.admin_created",
        result="success",
        actor_user_id=user_id,
        actor_username=requested_username,
        target_type="user",
        target_id=str(user_id),
        details={
            "source": source,
            "must_change_password": True,
        },
    )
    return {
        "status": "created",
        "username": requested_username,
        "user_id": user_id,
        "source": source,
        "must_change_password": True,
    }


def default_admin_pending_change() -> bool:
    user = database.get_user_by_normalized_username(DEFAULT_ADMIN_USERNAME)
    if (
        user is None
        or user.get("role") != "admin"
        or not user.get("is_active")
        or not user.get("must_change_password")
    ):
        return False
    return verify_password(
        DEFAULT_ADMIN_PASSWORD,
        str(user.get("password_hash") or ""),
    )
