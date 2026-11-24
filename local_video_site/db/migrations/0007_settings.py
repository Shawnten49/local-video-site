from __future__ import annotations

from .runner import BASELINE_VERSION, Migration


settings_migration = Migration(
    BASELINE_VERSION + 6,
    "add runtime setting overrides and audit log",
    (
        """
        CREATE TABLE IF NOT EXISTS setting_overrides (
            setting_key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            value_type TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1
                CHECK (version >= 1),
            updated_by_user_id INTEGER,
            updated_by_username TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT ''
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS setting_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            setting_key TEXT NOT NULL,
            change_code TEXT NOT NULL,
            old_value_json TEXT,
            new_value_json TEXT,
            old_display TEXT NOT NULL DEFAULT '',
            new_display TEXT NOT NULL DEFAULT '',
            actor_user_id INTEGER,
            actor_username TEXT NOT NULL DEFAULT '',
            client_ip TEXT NOT NULL DEFAULT '',
            request_id TEXT NOT NULL DEFAULT '',
            changed_at TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT ''
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_setting_audit_key_time
        ON setting_audit(setting_key, changed_at DESC)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_setting_audit_time
        ON setting_audit(changed_at DESC)
        """,
        """
        UPDATE schema_value_set_items
        SET label = '删除校验码',
            description = '通过删除校验码确认操作。'
        WHERE set_key = 'delete_verification_method'
          AND value = 'password'
        """,
    ),
)
