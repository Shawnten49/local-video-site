from __future__ import annotations

import json
import sqlite3
from typing import Dict, List, Optional

from local_video_site.db.connection import connect
from local_video_site.db.transaction import now_iso
from local_video_site.settings.errors import SettingError


class SettingsStorageNotReady(RuntimeError):
    pass


def _schema_ready() -> bool:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'setting_overrides'
            """
        ).fetchone()
    return row is not None


def load_overrides() -> Dict[str, Dict]:
    if not _schema_ready():
        raise SettingsStorageNotReady("settings schema is not ready")
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM setting_overrides"
        ).fetchall()
    result = {}
    for row in rows:
        item = dict(row)
        try:
            item["value"] = json.loads(item["value_json"])
        except (TypeError, ValueError):
            item["value"] = None
            item["invalid_json"] = True
        result[str(item["setting_key"])] = item
    return result


def get_override(setting_key: str) -> Optional[Dict]:
    if not _schema_ready():
        raise SettingsStorageNotReady("settings schema is not ready")
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM setting_overrides WHERE setting_key = ?",
            (str(setting_key),),
        ).fetchone()
    if row is None:
        return None
    item = dict(row)
    try:
        item["value"] = json.loads(item["value_json"])
    except (TypeError, ValueError):
        item["value"] = None
        item["invalid_json"] = True
    return item


def _insert_audit(
    connection: sqlite3.Connection,
    *,
    setting_key: str,
    action: str,
    old_value_json: Optional[str],
    new_value_json: Optional[str],
    old_display: str,
    new_display: str,
    actor_user_id: Optional[int],
    actor_username: str,
    client_ip: str,
    request_id: str,
    note: str,
) -> None:
    connection.execute(
        """
        INSERT INTO setting_audit (
            setting_key, change_code, old_value_json, new_value_json,
            old_display, new_display, actor_user_id, actor_username,
            client_ip, request_id, changed_at, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            setting_key,
            action,
            old_value_json,
            new_value_json,
            old_display,
            new_display,
            actor_user_id,
            actor_username,
            client_ip,
            request_id,
            now_iso(),
            note,
        ),
    )


def write_override(
    *,
    setting_key: str,
    value: object,
    value_type: str,
    expected_version: int,
    actor_user_id: Optional[int],
    actor_username: str,
    client_ip: str,
    request_id: str,
    note: str,
    old_display: str,
    new_display: str,
) -> Dict:
    if not _schema_ready():
        raise SettingsStorageNotReady("settings schema is not ready")
    now = now_iso()
    value_json = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM setting_overrides WHERE setting_key = ?",
            (setting_key,),
        ).fetchone()
        current_version = int(row["version"]) if row else 0
        if current_version != int(expected_version):
            connection.rollback()
            raise SettingError(
                "参数已被其他管理员修改，请刷新后重试",
                "setting_version_conflict",
                409,
            )
        if row is None:
            connection.execute(
                """
                INSERT INTO setting_overrides (
                    setting_key, value_json, value_type, version,
                    updated_by_user_id, updated_by_username,
                    updated_at, note
                ) VALUES (?, ?, ?, 1, ?, ?, ?, ?)
                """,
                (
                    setting_key,
                    value_json,
                    value_type,
                    actor_user_id,
                    actor_username,
                    now,
                    note,
                ),
            )
        else:
            connection.execute(
                """
                UPDATE setting_overrides
                SET value_json = ?, value_type = ?, version = version + 1,
                    updated_by_user_id = ?, updated_by_username = ?,
                    updated_at = ?, note = ?
                WHERE setting_key = ?
                """,
                (
                    value_json,
                    value_type,
                    actor_user_id,
                    actor_username,
                    now,
                    note,
                    setting_key,
                ),
            )
        _insert_audit(
            connection,
            setting_key=setting_key,
            action="update",
            old_value_json=row["value_json"] if row else None,
            new_value_json=value_json,
            old_display=old_display,
            new_display=new_display,
            actor_user_id=actor_user_id,
            actor_username=actor_username,
            client_ip=client_ip,
            request_id=request_id,
            note=note,
        )
        connection.commit()
    return get_override(setting_key) or {}


def delete_override(
    *,
    setting_key: str,
    expected_version: int,
    actor_user_id: Optional[int],
    actor_username: str,
    client_ip: str,
    request_id: str,
    note: str,
    old_display: str,
    new_display: str,
) -> None:
    if not _schema_ready():
        raise SettingsStorageNotReady("settings schema is not ready")
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM setting_overrides WHERE setting_key = ?",
            (setting_key,),
        ).fetchone()
        if row is None:
            connection.rollback()
            raise SettingError(
                "参数没有数据库覆盖值",
                "setting_override_missing",
                409,
            )
        if int(row["version"]) != int(expected_version):
            connection.rollback()
            raise SettingError(
                "参数已被其他管理员修改，请刷新后重试",
                "setting_version_conflict",
                409,
            )
        connection.execute(
            "DELETE FROM setting_overrides WHERE setting_key = ?",
            (setting_key,),
        )
        _insert_audit(
            connection,
            setting_key=setting_key,
            action="reset",
            old_value_json=row["value_json"],
            new_value_json=None,
            old_display=old_display,
            new_display=new_display,
            actor_user_id=actor_user_id,
            actor_username=actor_username,
            client_ip=client_ip,
            request_id=request_id,
            note=note,
        )
        connection.commit()


def record_secret_audit(
    *,
    setting_key: str,
    action: str,
    actor_user_id: Optional[int],
    actor_username: str,
    client_ip: str,
    request_id: str,
    note: str,
    old_display: str,
    new_display: str,
) -> None:
    if not _schema_ready():
        raise SettingsStorageNotReady("settings schema is not ready")
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        _insert_audit(
            connection,
            setting_key=setting_key,
            action=action,
            old_value_json=None,
            new_value_json=None,
            old_display=old_display,
            new_display=new_display,
            actor_user_id=actor_user_id,
            actor_username=actor_username,
            client_ip=client_ip,
            request_id=request_id,
            note=note,
        )
        connection.commit()


def list_audit(
    setting_key: str = "",
    page: int = 1,
    page_size: int = 30,
) -> Dict:
    if not _schema_ready():
        return {
            "items": [],
            "total": 0,
            "page": 1,
            "page_size": page_size,
            "pages": 1,
        }
    page = max(1, int(page))
    page_size = max(1, min(100, int(page_size)))
    clauses = ["1 = 1"]
    params: List[object] = []
    if setting_key:
        clauses.append("setting_key = ?")
        params.append(str(setting_key))
    where = " AND ".join(clauses)
    with connect() as connection:
        total = int(connection.execute(
            "SELECT COUNT(*) FROM setting_audit WHERE {}".format(where),
            params,
        ).fetchone()[0])
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        rows = connection.execute(
            """
            SELECT id, setting_key, change_code AS action,
                   old_display, new_display,
                   actor_user_id, actor_username, client_ip, request_id,
                   changed_at, note
            FROM setting_audit
            WHERE {}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """.format(where),
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    return {
        "items": [dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }
