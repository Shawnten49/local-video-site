from __future__ import annotations

import json
import sqlite3
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from local_video_site.core import config
from ..db.connection import connect
from ..db.transaction import now_iso as _now
from local_video_site.episodes.naming import is_generic_episode_title, meaningful_episode_title, season_number_from_label
from local_video_site.core.search import parse_search_query
from .videos import video_repository, video_with_preview_state


def create_user(
    username: str,
    normalized_username: str,
    password_hash: str,
    role: str = "user",
    created_by_user_id: Optional[int] = None,
    must_change_password: bool = False,
) -> int:
    now = _now()
    try:
        with connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO users (
                    username, normalized_username, password_hash, role,
                    is_active, must_change_password, created_by_user_id,
                    created_at, updated_at, password_changed_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                """,
                (
                    username,
                    normalized_username,
                    password_hash,
                    role,
                    1 if must_change_password else 0,
                    created_by_user_id,
                    now,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid or 0)
    except sqlite3.IntegrityError as exc:
        raise ValueError("用户名已经存在") from exc


def create_initial_admin_if_empty(
    username: str,
    normalized_username: str,
    password_hash: str,
) -> Tuple[str, Optional[int]]:
    now = _now()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        count = int(
            connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        )
        if count > 0:
            connection.rollback()
            return "skipped_users_exist", None
        cursor = connection.execute(
            """
            INSERT INTO users (
                username, normalized_username, password_hash, role,
                is_active, must_change_password, created_by_user_id,
                created_at, updated_at, password_changed_at
            ) VALUES (?, ?, ?, 'admin', 1, 1, NULL, ?, ?, ?)
            """,
            (
                username,
                normalized_username,
                password_hash,
                now,
                now,
                now,
            ),
        )
        connection.commit()
        return "created", int(cursor.lastrowid or 0)


def get_user(user_id: int) -> Optional[Dict]:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE id = ?",
            (int(user_id),),
        ).fetchone()
    return dict(row) if row else None
def get_user_by_normalized_username(value: str) -> Optional[Dict]:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE normalized_username = ?",
            (str(value),),
        ).fetchone()
    return dict(row) if row else None
def list_users() -> List[Dict]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT id, username, normalized_username, role, is_active,
                   must_change_password, created_by_user_id,
                   created_at, updated_at, password_changed_at,
                   last_login_at
            FROM users
            ORDER BY role DESC, normalized_username COLLATE NOCASE, id
            """
        ).fetchall()
    return [dict(row) for row in rows]
def user_count() -> int:
    with connect() as connection:
        return int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])
def active_admin_count() -> int:
    with connect() as connection:
        return int(connection.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1"
        ).fetchone()[0])
def update_user_account(
    user_id: int,
    username: Optional[str] = None,
    normalized_username: Optional[str] = None,
    role: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> None:
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        user = connection.execute(
            "SELECT * FROM users WHERE id = ?",
            (int(user_id),),
        ).fetchone()
        if user is None:
            connection.rollback()
            raise ValueError("账号不存在")
        removing_admin = (
            user["role"] == "admin"
            and bool(user["is_active"])
            and (
                (role is not None and role != "admin")
                or (is_active is not None and not is_active)
            )
        )
        if removing_admin:
            count = connection.execute(
                """
                SELECT COUNT(*) FROM users
                WHERE role = 'admin' AND is_active = 1
                """
            ).fetchone()[0]
            if int(count) <= 1:
                connection.rollback()
                raise ValueError("不能停用或降级最后一个有效管理员")
        updates: Dict[str, object] = {"updated_at": _now()}
        if username is not None:
            updates["username"] = username
        if normalized_username is not None:
            updates["normalized_username"] = normalized_username
        if role is not None:
            updates["role"] = role
        if is_active is not None:
            updates["is_active"] = 1 if is_active else 0
        assignments = ", ".join("{} = ?".format(key) for key in updates)
        try:
            connection.execute(
                "UPDATE users SET {} WHERE id = ?".format(assignments),
                [*updates.values(), int(user_id)],
            )
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise ValueError("用户名已经存在") from exc
        if is_active is False:
            connection.execute(
                """
                UPDATE user_sessions
                SET revoked_at = ?, revoke_reason = 'account_disabled'
                WHERE user_id = ? AND revoked_at IS NULL
                """,
                (_now(), int(user_id)),
            )
        connection.commit()
def update_user_password(
    user_id: int,
    password_hash: str,
    must_change_password: bool = False,
    keep_session_id: Optional[int] = None,
) -> None:
    now = _now()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            """
            UPDATE users
            SET password_hash = ?,
                password_version = password_version + 1,
                must_change_password = ?,
                password_changed_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                password_hash,
                1 if must_change_password else 0,
                now,
                now,
                int(user_id),
            ),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            raise ValueError("账号不存在")
        connection.execute(
            """
            UPDATE user_sessions
            SET revoked_at = COALESCE(revoked_at, ?),
                revoke_reason = COALESCE(revoke_reason, 'password_changed')
            WHERE user_id = ?
              AND revoked_at IS NULL
              AND (? IS NULL OR id <> ?)
            """,
            (now, int(user_id), keep_session_id, keep_session_id),
        )
        connection.commit()
def record_user_login(user_id: int) -> None:
    with connect() as connection:
        connection.execute(
            "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
            (_now(), _now(), int(user_id)),
        )
def create_user_session(
    user_id: int,
    token_hash: str,
    created_at: str,
    expires_at: str,
    client_ip: str = "",
    user_agent_hash: str = "",
) -> int:
    with connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO user_sessions (
                user_id, token_hash, created_at, last_seen_at,
                expires_at, client_ip, user_agent_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(user_id),
                token_hash,
                created_at,
                created_at,
                expires_at,
                client_ip,
                user_agent_hash,
            ),
        )
        return int(cursor.lastrowid or 0)
def get_session_identity(token_hash: str) -> Optional[Dict]:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT s.id AS session_id, s.expires_at, s.revoked_at,
                   u.id AS user_id, u.username, u.role, u.is_active,
                   u.must_change_password
            FROM user_sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ?
              AND s.revoked_at IS NULL
              AND s.expires_at > ?
              AND u.is_active = 1
            LIMIT 1
            """,
            (token_hash, _now()),
        ).fetchone()
    return dict(row) if row else None
def touch_user_session(session_id: int) -> None:
    with connect() as connection:
        connection.execute(
            "UPDATE user_sessions SET last_seen_at = ? WHERE id = ?",
            (_now(), int(session_id)),
        )
def revoke_user_session(session_id: int, reason: str = "logout") -> None:
    with connect() as connection:
        connection.execute(
            """
            UPDATE user_sessions
            SET revoked_at = COALESCE(revoked_at, ?),
                revoke_reason = COALESCE(revoke_reason, ?)
            WHERE id = ?
            """,
            (_now(), reason, int(session_id)),
        )
def record_login_attempt(
    normalized_username: str,
    client_ip: str,
    success: bool,
    failure_code: Optional[str] = None,
) -> None:
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO auth_login_attempts (
                normalized_username, client_ip, success,
                failure_code, attempted_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                normalized_username,
                client_ip,
                int(bool(success)),
                failure_code,
                _now(),
            ),
        )
def recent_login_failure_count(
    normalized_username: str,
    client_ip: str,
    minutes: int = 10,
) -> int:
    cutoff = (
        datetime.now().astimezone() - timedelta(minutes=max(1, int(minutes)))
    ).isoformat(timespec="seconds")
    with connect() as connection:
        return int(connection.execute(
            """
            SELECT COUNT(*) FROM auth_login_attempts
            WHERE normalized_username = ?
              AND client_ip = ?
              AND success = 0
              AND attempted_at >= ?
            """,
            (normalized_username, client_ip, cutoff),
        ).fetchone()[0])
def write_auth_audit(
    action: str,
    result: str,
    actor_user_id: Optional[int] = None,
    actor_username: str = "",
    target_type: str = "",
    target_id: Optional[str] = None,
    request_id: str = "",
    client_ip: str = "",
    details: Optional[Dict] = None,
) -> None:
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO auth_audit_log (
                actor_user_id, actor_username, action, target_type,
                target_id, result, request_id, client_ip,
                details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                actor_user_id,
                actor_username,
                action,
                target_type,
                target_id,
                result,
                request_id,
                client_ip,
                json.dumps(details or {}, ensure_ascii=False),
                _now(),
            ),
        )
