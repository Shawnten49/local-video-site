from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

from .connection import connect
from .transaction import now_iso as _now


CATEGORY_PARSER_VERSION = 2
ACTOR_METADATA_VERSION = 1
PLAYBACK_NORMALIZATION_VERSION = 1
VIDEO_MEDIA_COLUMNS = (
    "duration",
    "width",
    "height",
    "video_codec",
    "audio_codec",
    "metadata_status",
    "metadata_error",
    "normalization_status",
    "normalization_error",
    "normalized_at",
    "normalization_version",
)
VIDEO_ASSET_COLUMNS = (
    "thumbnail_path",
    "cover_source",
    "cover_timestamp",
    "cover_updated_at",
    "preview_path",
    "preview_status",
    "preview_error",
    "preview_start",
    "preview_duration",
    "preview_updated_at",
    "preview_version",
    "preview_source_size",
    "preview_source_mtime_ns",
    "preview_last_accessed_at",
)


def _ensure_video_satellite_schema(
    connection: sqlite3.Connection,
) -> None:
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info('videos')")
    }
    has_legacy_media = any(
        column in columns
        for column in VIDEO_MEDIA_COLUMNS + VIDEO_ASSET_COLUMNS
    )
    if has_legacy_media:
        def expr(name: str, default: str = "NULL") -> str:
            return name if name in columns else default

        connection.execute(
            """
            INSERT OR IGNORE INTO video_media_info (
                video_id, duration, width, height, video_codec, audio_codec,
                metadata_status, metadata_error, normalization_status,
                normalization_error, normalized_at, normalization_version,
                updated_at
            )
            SELECT id, {duration}, {width}, {height}, {video_codec},
                   {audio_codec},
                   COALESCE({metadata_status}, 'pending'),
                   {metadata_error},
                   COALESCE({normalization_status}, 'none'),
                   {normalization_error}, {normalized_at},
                   COALESCE({normalization_version}, 0), updated_at
            FROM videos
            """.format(
                duration=expr("duration"),
                width=expr("width"),
                height=expr("height"),
                video_codec=expr("video_codec"),
                audio_codec=expr("audio_codec"),
                metadata_status=expr("metadata_status", "'pending'"),
                metadata_error=expr("metadata_error"),
                normalization_status=expr(
                    "normalization_status",
                    "'none'",
                ),
                normalization_error=expr("normalization_error"),
                normalized_at=expr("normalized_at"),
                normalization_version=expr("normalization_version", "0"),
            )
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO video_assets (
                video_id, thumbnail_path, cover_source, cover_timestamp,
                cover_updated_at, preview_path, preview_status, preview_error,
                preview_start, preview_duration, preview_updated_at,
                preview_version, preview_source_size, preview_source_mtime_ns,
                preview_last_accessed_at, updated_at
            )
            SELECT id, {thumbnail_path},
                   COALESCE({cover_source}, 'auto'),
                   {cover_timestamp}, {cover_updated_at}, {preview_path},
                   COALESCE({preview_status}, 'pending'), {preview_error},
                   {preview_start}, {preview_duration},
                   {preview_updated_at},
                   COALESCE({preview_version}, 0), {preview_source_size},
                   {preview_source_mtime_ns}, {preview_last_accessed_at},
                   updated_at
            FROM videos
            """.format(
                thumbnail_path=expr("thumbnail_path"),
                cover_source=expr("cover_source", "'auto'"),
                cover_timestamp=expr("cover_timestamp"),
                cover_updated_at=expr("cover_updated_at"),
                preview_path=expr("preview_path"),
                preview_status=expr("preview_status", "'pending'"),
                preview_error=expr("preview_error"),
                preview_start=expr("preview_start"),
                preview_duration=expr("preview_duration"),
                preview_updated_at=expr("preview_updated_at"),
                preview_version=expr("preview_version", "0"),
                preview_source_size=expr("preview_source_size"),
                preview_source_mtime_ns=expr(
                    "preview_source_mtime_ns"
                ),
                preview_last_accessed_at=expr(
                    "preview_last_accessed_at"
                ),
            )
        )
        connection.execute("DROP VIEW IF EXISTS video_catalog")
        connection.execute("DROP TABLE IF EXISTS videos_core_new")
        connection.execute(
            """
            CREATE TABLE videos_core_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                display_title_override TEXT,
                original_url TEXT,
                download_source_url TEXT,
                media_origin TEXT NOT NULL DEFAULT 'download',
                directory TEXT NOT NULL,
                extension TEXT NOT NULL,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                missing INTEGER NOT NULL DEFAULT 0,
                is_deleted INTEGER NOT NULL DEFAULT 0,
                deleted_at TEXT,
                access_level INTEGER NOT NULL DEFAULT 0,
                storage_volume_id INTEGER,
                storage_relative_path TEXT,
                storage_changed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO videos_core_new (
                id, path, title, display_title_override,
                original_url, download_source_url,
                media_origin, directory, extension, size, mtime_ns, missing,
                is_deleted, access_level, storage_volume_id,
                storage_relative_path, storage_changed_at, created_at,
                updated_at
            )
            SELECT id, path, title, {display_title_override},
                   {original_url}, {download_source_url},
                   COALESCE({media_origin}, 'download'), directory, extension,
                   size, mtime_ns, COALESCE({missing}, 0),
                   COALESCE({is_deleted}, 0), COALESCE({access_level}, 0),
                   {storage_volume_id}, {storage_relative_path},
                   {storage_changed_at}, created_at, updated_at
            FROM videos
            """.format(
                display_title_override=expr(
                    "display_title_override"
                ),
                original_url=expr("original_url"),
                download_source_url=expr("download_source_url"),
                media_origin=expr("media_origin", "'download'"),
                missing=expr("missing", "0"),
                is_deleted=expr("is_deleted", "0"),
                access_level=expr("access_level", "0"),
                storage_volume_id=expr("storage_volume_id"),
                storage_relative_path=expr("storage_relative_path"),
                storage_changed_at=expr("storage_changed_at"),
            )
        )
        connection.execute("DROP TABLE videos")
        connection.execute("ALTER TABLE videos_core_new RENAME TO videos")
        for statement in (
            "CREATE INDEX idx_videos_title ON videos(title)",
            "CREATE INDEX idx_videos_updated ON videos(updated_at)",
            "CREATE INDEX idx_videos_directory ON videos(directory)",
            "CREATE INDEX idx_videos_original_url ON videos(original_url)",
            "CREATE INDEX idx_videos_storage_volume "
            "ON videos(storage_volume_id)",
            "CREATE INDEX idx_videos_storage_changed "
            "ON videos(storage_changed_at)",
            "CREATE INDEX idx_videos_lifecycle "
            "ON videos(is_deleted, missing, id)",
            "CREATE INDEX idx_videos_access_level "
            "ON videos(access_level, missing, is_deleted)",
            "CREATE INDEX idx_videos_admin_order "
            "ON videos(created_at DESC, id DESC)",
            "CREATE INDEX idx_videos_admin_state "
            "ON videos(is_deleted, access_level, storage_volume_id, "
            "created_at DESC)",
            "CREATE INDEX idx_videos_directory_state "
            "ON videos(directory, is_deleted, created_at DESC)",
        ):
            connection.execute(statement)
    else:
        connection.execute(
            """
            INSERT OR IGNORE INTO video_media_info (video_id, updated_at)
            SELECT id, updated_at FROM videos
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO video_assets (video_id, updated_at)
            SELECT id, updated_at FROM videos
            """
        )
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info('videos')")
    }
    media_columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info('video_media_info')"
        )
    }
    if "container_format" not in media_columns:
        connection.execute(
            "ALTER TABLE video_media_info "
            "ADD COLUMN container_format TEXT"
        )
    if "display_title_override" not in columns:
        connection.execute(
            "ALTER TABLE videos ADD COLUMN display_title_override TEXT"
        )
    connection.execute("DROP VIEW IF EXISTS video_catalog")
    connection.execute(
        """
        CREATE VIEW video_catalog AS
        SELECT
            v.id, v.path,
            COALESCE(
                NULLIF(v.display_title_override, ''),
                v.title
            ) AS title,
            v.title AS default_title,
            v.display_title_override,
            v.original_url, v.download_source_url,
            v.media_origin, v.directory, v.extension, v.size, v.mtime_ns,
            v.missing, v.is_deleted, v.deleted_at, v.access_level,
            v.storage_volume_id,
            v.storage_relative_path, v.storage_changed_at,
            v.created_at, v.updated_at,
            m.duration, m.width, m.height, m.video_codec, m.audio_codec,
            m.container_format,
            m.metadata_status, m.metadata_error, m.metadata_version,
            m.normalization_status, m.normalization_error, m.normalized_at,
            m.normalization_version,
            a.thumbnail_path, a.cover_source, a.cover_timestamp,
            a.cover_updated_at, a.preview_path, a.preview_status,
            a.preview_error, a.preview_start, a.preview_duration,
            a.preview_updated_at, a.preview_version, a.preview_source_size,
            a.preview_source_mtime_ns, a.preview_last_accessed_at,
            CASE WHEN m.video_id IS NOT NULL THEN m.updated_at
                 ELSE v.updated_at END AS media_updated_at,
            CASE WHEN a.video_id IS NOT NULL THEN a.updated_at
                 ELSE v.updated_at END AS assets_updated_at
        FROM videos v
        LEFT JOIN video_media_info m ON m.video_id = v.id
        LEFT JOIN video_assets a ON a.video_id = v.id
        """
    )


def _ensure_media_policy_columns(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info('site_media_policies')"
        )
    }
    migrations = {
        "format_strategy": "TEXT NOT NULL DEFAULT 'auto'",
        "prefer_h264": "INTEGER NOT NULL DEFAULT 0",
        "merge_output_format": "TEXT NOT NULL DEFAULT 'mp4'",
    }
    for column, definition in migrations.items():
        if column not in columns:
            connection.execute(
                "ALTER TABLE site_media_policies ADD COLUMN {} {}".format(
                    column,
                    definition,
                )
            )


def _ensure_episode_title_columns(
    connection: sqlite3.Connection,
) -> None:
    for table in ("video_episodes", "episode_download_items"):
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info('{}')".format(table)
            )
        }
        if "episode_title" not in columns:
            connection.execute(
                "ALTER TABLE {} ADD COLUMN episode_title TEXT".format(
                    table
                )
            )
    batch_columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info('episode_download_batches')"
        )
    }
    if "access_level" not in batch_columns:
        connection.execute(
            "ALTER TABLE episode_download_batches "
            "ADD COLUMN access_level INTEGER NOT NULL DEFAULT 0"
        )
    if "created_by_user_id" not in batch_columns:
        connection.execute(
            "ALTER TABLE episode_download_batches "
            "ADD COLUMN created_by_user_id INTEGER"
        )
    if "created_by_username" not in batch_columns:
        connection.execute(
            "ALTER TABLE episode_download_batches "
            "ADD COLUMN created_by_username TEXT"
        )


def _ensure_episode_discovery_schema(
    connection: sqlite3.Connection,
) -> None:
    for table in (
        "episode_download_batches",
        "episode_download_discoveries",
    ):
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info('{}')".format(table)
            )
        }
        if "file_name_mode" not in columns:
            connection.execute(
                "ALTER TABLE {} ADD COLUMN file_name_mode "
                "TEXT NOT NULL DEFAULT 'sequence'".format(table)
            )
    discovery_columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info('episode_download_discoveries')"
        )
    }
    if "preserve_original_name" not in discovery_columns:
        connection.execute(
            "ALTER TABLE episode_download_discoveries "
            "ADD COLUMN preserve_original_name "
            "INTEGER NOT NULL DEFAULT 0"
        )
    item_columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info('episode_download_items')"
        )
    }
    for column, definition in {
        "source_tab_key": "TEXT",
        "source_tab_title": "TEXT",
        "source_tab_item_title": "TEXT",
        "source_season_name": "TEXT",
        "source_episode_number": "INTEGER",
    }.items():
        if column not in item_columns:
            connection.execute(
                "ALTER TABLE episode_download_items "
                "ADD COLUMN {} {}".format(column, definition)
            )


def _ensure_video_trim_columns(
    connection: sqlite3.Connection,
) -> None:
    columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info('video_trim_jobs')"
        )
    }
    for column, definition in {
        "source_width": "INTEGER",
        "source_height": "INTEGER",
        "source_video_codec": "TEXT",
        "source_audio_codec": "TEXT",
        "backup_status": "TEXT NOT NULL DEFAULT 'none'",
        "backup_size": "INTEGER",
        "backup_mtime_ns": "INTEGER",
        "backup_sha256": "TEXT",
        "backup_created_at": "TEXT",
        "backup_deleted_at": "TEXT",
        "restore_count": "INTEGER NOT NULL DEFAULT 0",
        "last_restored_at": "TEXT",
        "last_restored_by_user_id": "INTEGER",
        "last_restored_by_username": "TEXT",
        "restore_temporary_path": "TEXT",
        "restore_rollback_path": "TEXT",
        "restore_status": "TEXT",
    }.items():
        if column not in columns:
            connection.execute(
                "ALTER TABLE video_trim_jobs "
                "ADD COLUMN {} {}".format(column, definition)
            )


def _ensure_external_scan_columns(
    connection: sqlite3.Connection,
) -> None:
    storage_columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info('video_storage_volumes')"
        )
    }
    for column, definition in {
        "identity_source": "TEXT NOT NULL DEFAULT 'marker'",
        "access_mode": "TEXT NOT NULL DEFAULT 'read_write'",
        "online_state": "TEXT NOT NULL DEFAULT 'unknown'",
        "last_probe_at": "TEXT",
        "last_probe_error": "TEXT",
    }.items():
        if column not in storage_columns:
            connection.execute(
                "ALTER TABLE video_storage_volumes "
                "ADD COLUMN {} {}".format(column, definition)
            )

    episode_columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info('video_episodes')"
        )
    }
    for column, definition in {
        "origin_type": "TEXT NOT NULL DEFAULT 'download'",
        "origin_scan_id": "TEXT",
        "storage_volume_id": "INTEGER",
        "storage_relative_path": "TEXT",
    }.items():
        if column not in episode_columns:
            connection.execute(
                "ALTER TABLE video_episodes ADD COLUMN {} {}".format(
                    column,
                    definition,
                )
            )

    batch_columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info('video_scan_batches')"
        )
    }
    for column, definition in {
        "grouping_mode": "TEXT NOT NULL DEFAULT 'single_season'",
        "season_group_count": "INTEGER NOT NULL DEFAULT 0",
        "target_series_id": "INTEGER",
        "target_season_id": "INTEGER",
    }.items():
        if column not in batch_columns:
            connection.execute(
                "ALTER TABLE video_scan_batches "
                "ADD COLUMN {} {}".format(column, definition)
            )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_video_scan_batches_target_series "
        "ON video_scan_batches(target_series_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_video_scan_batches_target_season "
        "ON video_scan_batches(target_season_id)"
    )

    import_columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info('video_import_jobs')"
        )
    }
    if "access_level" not in import_columns:
        connection.execute(
            "ALTER TABLE video_import_jobs "
            "ADD COLUMN access_level INTEGER NOT NULL DEFAULT 0"
        )


def _ensure_playback_cache_columns(
    connection: sqlite3.Connection,
) -> None:
    columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info('playback_cache_entries')"
        )
    }
    for column, definition in {
        "preparation_mode": "TEXT NOT NULL DEFAULT 'legacy'",
        "source_video_codec": "TEXT",
        "source_audio_codec": "TEXT",
        "decision_reason": "TEXT",
        "decision_version": "INTEGER NOT NULL DEFAULT 1",
    }.items():
        if column not in columns:
            connection.execute(
                "ALTER TABLE playback_cache_entries "
                "ADD COLUMN {} {}".format(column, definition)
            )


def _ensure_video_format_columns(
    connection: sqlite3.Connection,
) -> None:
    columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info('video_format_jobs')"
        )
    }
    for column, definition in {
        "profile_key": "TEXT NOT NULL DEFAULT 'copy'",
        "lossy": "INTEGER NOT NULL DEFAULT 0",
        "source_container_format": "TEXT",
        "target_container": "TEXT NOT NULL DEFAULT 'mp4'",
        "target_video_codec": "TEXT",
        "target_audio_codec": "TEXT",
        "target_width": "INTEGER",
        "target_height": "INTEGER",
        "encoding_profile_json": "TEXT",
        "confirmation_version": "INTEGER NOT NULL DEFAULT 1",
        "confirmed_at": "TEXT",
        "backup_path": "TEXT",
        "backup_size": "INTEGER",
        "backup_mtime_ns": "INTEGER",
        "backup_status": "TEXT NOT NULL DEFAULT 'none'",
        "backup_created_at": "TEXT",
        "backup_deleted_at": "TEXT",
        "restore_status": "TEXT",
        "last_restored_at": "TEXT",
    }.items():
        if column not in columns:
            connection.execute(
                "ALTER TABLE video_format_jobs "
                "ADD COLUMN {} {}".format(column, definition)
            )


def _ensure_series_delete_columns(
    connection: sqlite3.Connection,
) -> None:
    for table in ("video_series", "video_seasons"):
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info('{}')".format(table)
            )
        }
        if "is_deleted" not in columns:
            connection.execute(
                "ALTER TABLE {} ADD COLUMN "
                "is_deleted INTEGER NOT NULL DEFAULT 0".format(table)
            )
        if "deleted_at" not in columns:
            connection.execute(
                "ALTER TABLE {} ADD COLUMN deleted_at TEXT".format(table)
            )
        if "display_name_override" not in columns:
            connection.execute(
                "ALTER TABLE {} ADD COLUMN "
                "display_name_override TEXT".format(table)
            )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_video_series_active "
        "ON video_series(is_deleted, updated_at DESC)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_video_seasons_active "
        "ON video_seasons(series_id, is_deleted, season_number)"
    )


def ensure_media_policy_schema() -> None:
    """Create the additive media-policy table for older databases."""

    with connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS site_media_policies (
                site_key TEXT PRIMARY KEY,
                preset_key TEXT NOT NULL DEFAULT 'generic',
                source_order_json TEXT NOT NULL,
                identity_mode TEXT NOT NULL DEFAULT 'site_default',
                kind_order_json TEXT NOT NULL,
                quality_target TEXT NOT NULL DEFAULT 'auto',
                quality_fallback TEXT NOT NULL DEFAULT 'nearest_below',
                min_height INTEGER NOT NULL DEFAULT 360,
                max_height INTEGER NOT NULL DEFAULT 1080,
                allow_below_minimum INTEGER NOT NULL DEFAULT 1,
                allow_page_fallback INTEGER NOT NULL DEFAULT 1,
                play_selector TEXT,
                format_strategy TEXT NOT NULL DEFAULT 'auto',
                prefer_h264 INTEGER NOT NULL DEFAULT 0,
                merge_output_format TEXT NOT NULL DEFAULT 'mp4',
                manual_override INTEGER NOT NULL DEFAULT 1,
                strategy_version INTEGER NOT NULL DEFAULT 1,
                status_note TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        _ensure_media_policy_columns(connection)


def _apply_legacy_schema() -> None:
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                normalized_username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user'
                    CHECK (role IN ('user', 'admin')),
                is_active INTEGER NOT NULL DEFAULT 1
                    CHECK (is_active IN (0, 1)),
                must_change_password INTEGER NOT NULL DEFAULT 0
                    CHECK (must_change_password IN (0, 1)),
                password_version INTEGER NOT NULL DEFAULT 1,
                created_by_user_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                password_changed_at TEXT,
                last_login_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_users_role_active
                ON users(role, is_active);

            CREATE TABLE IF NOT EXISTS user_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT,
                revoke_reason TEXT,
                client_ip TEXT,
                user_agent_hash TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_user_sessions_user_active
                ON user_sessions(user_id, revoked_at, expires_at);
            CREATE INDEX IF NOT EXISTS idx_user_sessions_expiry
                ON user_sessions(expires_at);

            CREATE TABLE IF NOT EXISTS user_favorites (
                user_id INTEGER NOT NULL,
                video_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (user_id, video_id)
            );
            CREATE INDEX IF NOT EXISTS idx_user_favorites_video
                ON user_favorites(video_id);
            CREATE INDEX IF NOT EXISTS idx_user_favorites_user_created
                ON user_favorites(user_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS auth_login_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                normalized_username TEXT NOT NULL,
                client_ip TEXT NOT NULL,
                success INTEGER NOT NULL DEFAULT 0
                    CHECK (success IN (0, 1)),
                failure_code TEXT,
                attempted_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_auth_login_attempts_identity
                ON auth_login_attempts(
                    normalized_username,
                    client_ip,
                    attempted_at DESC
                );

            CREATE TABLE IF NOT EXISTS auth_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_user_id INTEGER,
                actor_username TEXT,
                action TEXT NOT NULL,
                target_type TEXT,
                target_id TEXT,
                result TEXT NOT NULL,
                request_id TEXT,
                client_ip TEXT,
                details_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_auth_audit_created
                ON auth_audit_log(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_auth_audit_actor
                ON auth_audit_log(actor_user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_auth_audit_action
                ON auth_audit_log(action, created_at DESC);

            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                display_title_override TEXT,
                original_url TEXT,
                download_source_url TEXT,
                media_origin TEXT NOT NULL DEFAULT 'download',
                directory TEXT NOT NULL,
                extension TEXT NOT NULL,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                missing INTEGER NOT NULL DEFAULT 0,
                is_deleted INTEGER NOT NULL DEFAULT 0,
                access_level INTEGER NOT NULL DEFAULT 0,
                storage_volume_id INTEGER,
                storage_relative_path TEXT,
                storage_changed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_videos_title ON videos(title);
            CREATE INDEX IF NOT EXISTS idx_videos_updated ON videos(updated_at);
            CREATE INDEX IF NOT EXISTS idx_videos_directory ON videos(directory);
            CREATE TABLE IF NOT EXISTS video_media_info (
                video_id INTEGER PRIMARY KEY,
                duration REAL,
                width INTEGER,
                height INTEGER,
                video_codec TEXT,
                audio_codec TEXT,
                container_format TEXT,
                metadata_status TEXT NOT NULL DEFAULT 'pending',
                metadata_error TEXT,
                metadata_version INTEGER NOT NULL DEFAULT 0,
                normalization_status TEXT NOT NULL DEFAULT 'none',
                normalization_error TEXT,
                normalized_at TEXT,
                normalization_version INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_video_media_metadata_status
                ON video_media_info(metadata_status, video_id);
            CREATE INDEX IF NOT EXISTS idx_video_media_normalization
                ON video_media_info(normalization_status, video_id);
            CREATE TABLE IF NOT EXISTS video_assets (
                video_id INTEGER PRIMARY KEY,
                thumbnail_path TEXT,
                cover_source TEXT NOT NULL DEFAULT 'auto',
                cover_timestamp REAL,
                cover_updated_at TEXT,
                preview_path TEXT,
                preview_status TEXT NOT NULL DEFAULT 'pending',
                preview_error TEXT,
                preview_start REAL,
                preview_duration REAL,
                preview_updated_at TEXT,
                preview_version INTEGER NOT NULL DEFAULT 0,
                preview_source_size INTEGER,
                preview_source_mtime_ns INTEGER,
                preview_last_accessed_at TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_video_assets_preview_status
                ON video_assets(preview_status, video_id);
            CREATE INDEX IF NOT EXISTS idx_video_assets_preview_access
                ON video_assets(preview_last_accessed_at);
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL UNIQUE,
                external_slug TEXT,
                external_type TEXT,
                source_domain TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_categories_name
                ON categories(normalized_name);

            CREATE TABLE IF NOT EXISTS video_categories (
                video_id INTEGER NOT NULL,
                category_id INTEGER NOT NULL,
                source_kind TEXT NOT NULL DEFAULT 'title',
                source_url TEXT,
                created_at TEXT,
                PRIMARY KEY (video_id, category_id),
                FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE,
                FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_video_categories_category
                ON video_categories(category_id, video_id);

            CREATE TABLE IF NOT EXISTS actors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL UNIQUE,
                external_id INTEGER,
                external_slug TEXT,
                source_domain TEXT,
                profile_url TEXT,
                avatar_source_url TEXT,
                avatar_path TEXT,
                avatar_status TEXT NOT NULL DEFAULT 'pending',
                avatar_error TEXT,
                avatar_fetched_at TEXT,
                avatar_attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_actors_name
                ON actors(normalized_name);

            CREATE TABLE IF NOT EXISTS video_actors (
                video_id INTEGER NOT NULL,
                actor_id INTEGER NOT NULL,
                source_domain TEXT,
                source_url TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (video_id, actor_id),
                FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE,
                FOREIGN KEY (actor_id) REFERENCES actors(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_video_actors_actor
                ON video_actors(actor_id, video_id);

            CREATE TABLE IF NOT EXISTS manual_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_manual_categories_name
                ON manual_categories(normalized_name);

            CREATE TABLE IF NOT EXISTS video_manual_categories (
                video_id INTEGER NOT NULL,
                manual_category_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (video_id, manual_category_id)
            );
            CREATE INDEX IF NOT EXISTS idx_video_manual_categories_category
                ON video_manual_categories(manual_category_id, video_id);

            CREATE TABLE IF NOT EXISTS category_fetch_cache (
                video_id INTEGER PRIMARY KEY,
                source_url TEXT NOT NULL,
                status TEXT NOT NULL,
                categories_json TEXT,
                actors_json TEXT,
                parser_version INTEGER NOT NULL DEFAULT 0,
                actor_metadata_version INTEGER NOT NULL DEFAULT 0,
                fetched_at TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                http_status INTEGER,
                error TEXT,
                FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS favorites (
                video_id INTEGER PRIMARY KEY,
                created_at TEXT NOT NULL,
                FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_favorites_created_at
                ON favorites(created_at);

            CREATE TABLE IF NOT EXISTS playback_cache_entries (
                cache_path TEXT PRIMARY KEY,
                video_id INTEGER,
                source_path TEXT NOT NULL,
                source_size INTEGER NOT NULL,
                source_mtime_ns INTEGER NOT NULL,
                cache_size INTEGER NOT NULL,
                preparation_mode TEXT NOT NULL DEFAULT 'legacy',
                source_video_codec TEXT,
                source_audio_codec TEXT,
                decision_reason TEXT,
                decision_version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_accessed_at TEXT,
                status TEXT NOT NULL DEFAULT 'ready',
                FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_playback_cache_created
                ON playback_cache_entries(created_at);
            CREATE INDEX IF NOT EXISTS idx_playback_cache_video
                ON playback_cache_entries(video_id);

            CREATE TABLE IF NOT EXISTS download_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL,
                created_by_user_id INTEGER,
                created_by_username TEXT,
                url TEXT NOT NULL,
                normalized_url TEXT NOT NULL,
                metadata_url TEXT,
                normalized_metadata_url TEXT,
                title TEXT,
                output_dir TEXT NOT NULL,
                file_path TEXT,
                file_name TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                stage TEXT NOT NULL DEFAULT 'waiting',
                progress_percent REAL NOT NULL DEFAULT 0,
                downloaded_bytes INTEGER NOT NULL DEFAULT 0,
                total_bytes INTEGER NOT NULL DEFAULT 0,
                media_width INTEGER,
                media_height INTEGER,
                media_quality TEXT,
                requested_quality TEXT NOT NULL DEFAULT 'auto',
                index_into_library INTEGER NOT NULL DEFAULT 1,
                media_url_expires_at TEXT,
                media_refresh_count INTEGER NOT NULL DEFAULT 0,
                last_media_refresh_at TEXT,
                media_identity TEXT,
                resume_validation TEXT,
                resume_total_bytes INTEGER,
                transport_kind TEXT,
                transport_site_key TEXT,
                range_connection_count INTEGER NOT NULL DEFAULT 0,
                range_completed_chunks INTEGER NOT NULL DEFAULT 0,
                range_total_chunks INTEGER NOT NULL DEFAULT 0,
                hls_connection_count INTEGER NOT NULL DEFAULT 0,
                hls_completed_segments INTEGER NOT NULL DEFAULT 0,
                hls_total_segments INTEGER NOT NULL DEFAULT 0,
                speed_bytes_per_sec REAL NOT NULL DEFAULT 0,
                eta_seconds INTEGER,
                elapsed_seconds INTEGER NOT NULL DEFAULT 0,
                run_started_at TEXT,
                pause_requested INTEGER NOT NULL DEFAULT 0,
                paused_at TEXT,
                resume_count INTEGER NOT NULL DEFAULT 0,
                run_count INTEGER NOT NULL DEFAULT 0,
                filename_hint TEXT,
                partial_path TEXT,
                checkpoint_path TEXT,
                delete_requested INTEGER NOT NULL DEFAULT 0,
                deleted_at TEXT,
                last_progress_at TEXT,
                stall_detected_at TEXT,
                stall_count INTEGER NOT NULL DEFAULT 0,
                message TEXT,
                error_detail TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                priority INTEGER NOT NULL DEFAULT 0,
                access_level INTEGER NOT NULL DEFAULT 0,
                video_id INTEGER,
                metadata_status TEXT NOT NULL DEFAULT 'pending',
                category_count INTEGER NOT NULL DEFAULT 0,
                actor_count INTEGER NOT NULL DEFAULT 0,
                log_path TEXT,
                next_retry_at TEXT,
                episode_id INTEGER,
                episode_batch_id TEXT,
                output_stem TEXT,
                display_title TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                heartbeat_at TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_download_tasks_queue
                ON download_tasks(
                    status, next_retry_at, priority DESC, id ASC
                );
            CREATE INDEX IF NOT EXISTS idx_download_tasks_created
                ON download_tasks(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_download_tasks_video
                ON download_tasks(video_id);
            CREATE INDEX IF NOT EXISTS idx_download_tasks_active_video
                ON download_tasks(video_id, status);
            CREATE INDEX IF NOT EXISTS idx_download_tasks_batch
                ON download_tasks(batch_id);
            CREATE TABLE IF NOT EXISTS video_series (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT '',
                source_url TEXT,
                display_name_override TEXT,
                is_deleted INTEGER NOT NULL DEFAULT 0,
                deleted_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(provider, normalized_name)
            );
            CREATE INDEX IF NOT EXISTS idx_video_series_name
                ON video_series(normalized_name);

            CREATE TABLE IF NOT EXISTS video_seasons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                season_number INTEGER NOT NULL,
                relative_directory TEXT NOT NULL,
                source_url TEXT,
                representative_video_id INTEGER,
                metadata_video_id INTEGER,
                cover_path TEXT,
                preview_path TEXT,
                preview_status TEXT NOT NULL DEFAULT 'pending',
                metadata_status TEXT NOT NULL DEFAULT 'pending',
                metadata_error TEXT,
                display_name_override TEXT,
                is_deleted INTEGER NOT NULL DEFAULT 0,
                deleted_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(series_id, season_number)
            );
            CREATE INDEX IF NOT EXISTS idx_video_seasons_series
                ON video_seasons(series_id, season_number);
            CREATE INDEX IF NOT EXISTS idx_video_seasons_representative
                ON video_seasons(representative_video_id);

            CREATE TABLE IF NOT EXISTS video_episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                season_id INTEGER NOT NULL,
                episode_number INTEGER NOT NULL,
                display_title TEXT NOT NULL,
                episode_title TEXT,
                source_url TEXT NOT NULL,
                normalized_source_url TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                absolute_path TEXT NOT NULL,
                video_id INTEGER,
                download_task_id INTEGER,
                origin_type TEXT NOT NULL DEFAULT 'download',
                origin_scan_id TEXT,
                storage_volume_id INTEGER,
                storage_relative_path TEXT,
                status TEXT NOT NULL DEFAULT 'planned',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(season_id, episode_number),
                UNIQUE(normalized_source_url),
                UNIQUE(absolute_path)
            );
            CREATE INDEX IF NOT EXISTS idx_video_episodes_season
                ON video_episodes(season_id, episode_number);
            CREATE INDEX IF NOT EXISTS idx_video_episodes_video
                ON video_episodes(video_id);
            CREATE INDEX IF NOT EXISTS idx_video_episodes_task
                ON video_episodes(download_task_id);

            CREATE TABLE IF NOT EXISTS episode_download_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL UNIQUE,
                series_name TEXT NOT NULL,
                season_name TEXT NOT NULL,
                season_number INTEGER NOT NULL,
                first_episode_url TEXT NOT NULL,
                provider TEXT NOT NULL,
                output_root TEXT NOT NULL,
                quality TEXT NOT NULL DEFAULT 'site',
                file_name_mode TEXT NOT NULL DEFAULT 'sequence',
                access_level INTEGER NOT NULL DEFAULT 0,
                created_by_user_id INTEGER,
                created_by_username TEXT,
                status TEXT NOT NULL DEFAULT 'preview',
                item_count INTEGER NOT NULL DEFAULT 0,
                revision INTEGER NOT NULL DEFAULT 1,
                error_code TEXT,
                error_detail TEXT,
                created_at TEXT NOT NULL,
                confirmed_at TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_episode_batches_status
                ON episode_download_batches(status, id);

            CREATE TABLE IF NOT EXISTS episode_download_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL,
                episode_number INTEGER NOT NULL,
                source_url TEXT NOT NULL,
                normalized_source_url TEXT NOT NULL,
                display_title TEXT NOT NULL,
                episode_title TEXT,
                file_name TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                absolute_path TEXT NOT NULL,
                selected INTEGER NOT NULL DEFAULT 0,
                validation_status TEXT NOT NULL DEFAULT 'ready',
                validation_message TEXT,
                download_task_id INTEGER,
                source_tab_key TEXT,
                source_tab_title TEXT,
                source_tab_item_title TEXT,
                source_season_name TEXT,
                source_episode_number INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(batch_id, episode_number),
                UNIQUE(batch_id, normalized_source_url)
            );
            CREATE INDEX IF NOT EXISTS idx_episode_items_batch
                ON episode_download_items(batch_id, episode_number);
            CREATE INDEX IF NOT EXISTS idx_episode_items_source
                ON episode_download_items(normalized_source_url);

            CREATE TABLE IF NOT EXISTS episode_download_discoveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                discovery_id TEXT NOT NULL UNIQUE,
                provider TEXT NOT NULL,
                source_url TEXT NOT NULL,
                series_name TEXT NOT NULL,
                output_root TEXT NOT NULL,
                quality TEXT NOT NULL DEFAULT 'site',
                access_level INTEGER NOT NULL DEFAULT 0,
                mode TEXT NOT NULL DEFAULT 'continuous_grouped',
                aggregate_single_season INTEGER NOT NULL DEFAULT 0,
                preserve_original_name INTEGER NOT NULL DEFAULT 0,
                file_name_mode TEXT NOT NULL DEFAULT 'sequence',
                target_season_name TEXT,
                target_season_number INTEGER,
                status TEXT NOT NULL DEFAULT 'preview',
                revision INTEGER NOT NULL DEFAULT 1,
                tab_count INTEGER NOT NULL DEFAULT 0,
                source_item_count INTEGER NOT NULL DEFAULT 0,
                item_count INTEGER NOT NULL DEFAULT 0,
                complete INTEGER NOT NULL DEFAULT 1,
                warning TEXT,
                error_code TEXT,
                error_detail TEXT,
                created_by_user_id INTEGER,
                created_by_username TEXT,
                created_at TEXT NOT NULL,
                confirmed_at TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_episode_discoveries_status
                ON episode_download_discoveries(status, id);
            CREATE INDEX IF NOT EXISTS idx_episode_discoveries_owner
                ON episode_download_discoveries(
                    created_by_user_id, created_at DESC
                );

            CREATE TABLE IF NOT EXISTS episode_download_discovery_tabs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                discovery_id TEXT NOT NULL,
                tab_sequence INTEGER NOT NULL,
                tab_key TEXT NOT NULL,
                tab_title TEXT NOT NULL,
                season_name TEXT NOT NULL,
                season_number INTEGER NOT NULL,
                source_url TEXT NOT NULL,
                source_item_count INTEGER NOT NULL DEFAULT 0,
                batch_id TEXT,
                status TEXT NOT NULL DEFAULT 'ready',
                warning TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(discovery_id, tab_sequence),
                UNIQUE(discovery_id, tab_key)
            );
            CREATE INDEX IF NOT EXISTS idx_episode_discovery_tabs_parent
                ON episode_download_discovery_tabs(
                    discovery_id, tab_sequence
                );

            CREATE TABLE IF NOT EXISTS video_storage_volumes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                storage_key TEXT NOT NULL UNIQUE,
                label TEXT NOT NULL,
                volume_uuid TEXT,
                mount_path TEXT NOT NULL,
                root_relative_path TEXT NOT NULL DEFAULT '',
                identity_source TEXT NOT NULL DEFAULT 'marker',
                access_mode TEXT NOT NULL DEFAULT 'read_write',
                online_state TEXT NOT NULL DEFAULT 'unknown',
                last_probe_at TEXT,
                last_probe_error TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                last_seen_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS video_migrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL,
                video_id INTEGER,
                video_title TEXT NOT NULL,
                mode TEXT NOT NULL,
                direction TEXT NOT NULL,
                status TEXT NOT NULL,
                stage TEXT NOT NULL,
                source_storage_kind TEXT NOT NULL,
                source_volume_id INTEGER,
                source_root TEXT NOT NULL,
                source_relative_path TEXT NOT NULL,
                source_path TEXT NOT NULL,
                destination_storage_kind TEXT NOT NULL,
                destination_volume_id INTEGER,
                destination_root TEXT NOT NULL,
                destination_relative_path TEXT NOT NULL,
                destination_path TEXT NOT NULL,
                total_bytes INTEGER NOT NULL DEFAULT 0,
                copied_bytes INTEGER NOT NULL DEFAULT 0,
                progress_percent REAL NOT NULL DEFAULT 0,
                verification_mode TEXT NOT NULL DEFAULT 'sha256',
                source_sha256 TEXT,
                destination_sha256 TEXT,
                error_code TEXT,
                error_detail TEXT,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                copied_at TEXT,
                database_committed_at TEXT,
                source_deleted_at TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE (batch_id, video_id)
            );
            CREATE INDEX IF NOT EXISTS idx_video_migrations_batch
                ON video_migrations(batch_id);
            CREATE INDEX IF NOT EXISTS idx_video_migrations_status
                ON video_migrations(status, id);
            CREATE INDEX IF NOT EXISTS idx_video_migrations_video
                ON video_migrations(video_id, id DESC);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_video_migrations_active_video
                ON video_migrations(video_id)
                WHERE status IN ('pending', 'running', 'needs_cleanup');

            CREATE TABLE IF NOT EXISTS video_import_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL,
                source_path TEXT NOT NULL,
                source_root TEXT NOT NULL,
                source_relative_path TEXT NOT NULL,
                source_size INTEGER NOT NULL DEFAULT 0,
                source_mtime_ns INTEGER NOT NULL DEFAULT 0,
                destination_root TEXT NOT NULL,
                destination_relative_path TEXT NOT NULL,
                destination_path TEXT NOT NULL,
                video_id INTEGER,
                delete_source INTEGER NOT NULL DEFAULT 0,
                access_level INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                stage TEXT NOT NULL DEFAULT 'waiting',
                progress_percent REAL NOT NULL DEFAULT 0,
                copied_bytes INTEGER NOT NULL DEFAULT 0,
                verification_mode TEXT NOT NULL DEFAULT 'sha256',
                source_sha256 TEXT,
                destination_sha256 TEXT,
                metadata_status TEXT NOT NULL DEFAULT 'pending',
                thumbnail_status TEXT NOT NULL DEFAULT 'pending',
                preview_status TEXT NOT NULL DEFAULT 'pending',
                error_code TEXT,
                error_detail TEXT,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                copied_at TEXT,
                video_inserted_at TEXT,
                source_deleted_at TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE (batch_id, source_path)
            );
            CREATE INDEX IF NOT EXISTS idx_video_import_jobs_status
                ON video_import_jobs(status, id);
            CREATE INDEX IF NOT EXISTS idx_video_import_jobs_batch
                ON video_import_jobs(batch_id);
            CREATE INDEX IF NOT EXISTS idx_video_import_jobs_source
                ON video_import_jobs(source_path);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_video_import_jobs_active_source
                ON video_import_jobs(source_path)
                WHERE status IN ('pending', 'running', 'needs_cleanup');

            CREATE TABLE IF NOT EXISTS video_scan_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id TEXT NOT NULL UNIQUE,
                source_type TEXT NOT NULL,
                source_path TEXT NOT NULL,
                source_volume_id INTEGER,
                source_relative_path TEXT NOT NULL DEFAULT '',
                scan_mode TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                stage TEXT NOT NULL DEFAULT 'queued',
                review_required INTEGER NOT NULL DEFAULT 0,
                grouping_mode TEXT NOT NULL DEFAULT 'single_season',
                season_group_count INTEGER NOT NULL DEFAULT 0,
                target_series_id INTEGER,
                target_season_id INTEGER,
                series_name TEXT NOT NULL DEFAULT '',
                season_name TEXT NOT NULL DEFAULT '',
                season_number INTEGER NOT NULL DEFAULT 1,
                access_level INTEGER NOT NULL DEFAULT 0,
                total_files INTEGER NOT NULL DEFAULT 0,
                processed_files INTEGER NOT NULL DEFAULT 0,
                ready_files INTEGER NOT NULL DEFAULT 0,
                failed_files INTEGER NOT NULL DEFAULT 0,
                imported_files INTEGER NOT NULL DEFAULT 0,
                progress_percent REAL NOT NULL DEFAULT 0,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                error_code TEXT,
                error_detail TEXT,
                created_by_user_id INTEGER,
                created_by_username TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_video_scan_batches_status
                ON video_scan_batches(status, id);
            CREATE INDEX IF NOT EXISTS idx_video_scan_batches_owner
                ON video_scan_batches(created_by_user_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS video_scan_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id TEXT NOT NULL,
                source_path TEXT NOT NULL,
                source_relative_path TEXT NOT NULL,
                file_name TEXT NOT NULL,
                extension TEXT NOT NULL,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                duration REAL,
                width INTEGER,
                height INTEGER,
                video_codec TEXT,
                audio_codec TEXT,
                inferred_title TEXT NOT NULL,
                series_name TEXT NOT NULL DEFAULT '',
                season_name TEXT NOT NULL DEFAULT '',
                season_number INTEGER NOT NULL DEFAULT 1,
                episode_number INTEGER,
                episode_title TEXT,
                access_level INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                selected INTEGER NOT NULL DEFAULT 1,
                thumbnail_path TEXT,
                preview_path TEXT,
                preview_status TEXT NOT NULL DEFAULT 'pending',
                preview_start REAL,
                preview_duration REAL,
                preview_error TEXT,
                existing_video_id INTEGER,
                video_id INTEGER,
                error_code TEXT,
                error_detail TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(scan_id, source_path)
            );
            CREATE INDEX IF NOT EXISTS idx_video_scan_items_scan_status
                ON video_scan_items(scan_id, status, id);
            CREATE INDEX IF NOT EXISTS idx_video_scan_items_video
                ON video_scan_items(video_id);

            CREATE TABLE IF NOT EXISTS video_rename_operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id INTEGER NOT NULL,
                old_path TEXT NOT NULL,
                new_path TEXT NOT NULL,
                new_title TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_video_rename_status
                ON video_rename_operations(status, id);
            CREATE INDEX IF NOT EXISTS idx_video_rename_video
                ON video_rename_operations(video_id);

            CREATE TABLE IF NOT EXISTS video_trim_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL UNIQUE,
                video_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                stage TEXT NOT NULL DEFAULT 'waiting',
                progress_percent REAL NOT NULL DEFAULT 0,
                requested_start_seconds REAL NOT NULL,
                actual_cut_seconds REAL NOT NULL,
                cut_mode TEXT NOT NULL,
                keyframe_before_seconds REAL,
                keyframe_after_seconds REAL,
                source_path TEXT NOT NULL,
                source_size INTEGER NOT NULL,
                source_mtime_ns INTEGER NOT NULL,
                source_duration REAL,
                source_width INTEGER,
                source_height INTEGER,
                source_video_codec TEXT,
                source_audio_codec TEXT,
                temporary_path TEXT,
                backup_path TEXT,
                backup_status TEXT NOT NULL DEFAULT 'none',
                backup_size INTEGER,
                backup_mtime_ns INTEGER,
                backup_sha256 TEXT,
                backup_created_at TEXT,
                backup_deleted_at TEXT,
                output_size INTEGER,
                output_duration REAL,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                restore_count INTEGER NOT NULL DEFAULT 0,
                last_restored_at TEXT,
                last_restored_by_user_id INTEGER,
                last_restored_by_username TEXT,
                restore_temporary_path TEXT,
                restore_rollback_path TEXT,
                restore_status TEXT,
                created_by_user_id INTEGER,
                created_by_username TEXT,
                error_code TEXT,
                error_detail TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_video_trim_jobs_status
                ON video_trim_jobs(status, id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_video_trim_job_active
                ON video_trim_jobs(video_id)
                WHERE status IN ('pending', 'running', 'committing');

            CREATE TABLE IF NOT EXISTS video_format_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL UNIQUE,
                batch_id TEXT NOT NULL,
                video_id INTEGER NOT NULL,
                source_scan_id TEXT,
                source_scan_item_id INTEGER,
                operation TEXT NOT NULL DEFAULT 'mkv_to_mp4_remux',
                source_storage_kind TEXT NOT NULL,
                source_volume_id INTEGER,
                source_root TEXT NOT NULL,
                source_relative_path TEXT NOT NULL,
                source_path TEXT NOT NULL,
                source_extension TEXT NOT NULL,
                source_size INTEGER NOT NULL,
                source_mtime_ns INTEGER NOT NULL,
                source_duration REAL,
                source_width INTEGER,
                source_height INTEGER,
                source_video_codec TEXT,
                source_audio_codec TEXT,
                source_container_format TEXT,
                profile_key TEXT NOT NULL DEFAULT 'copy',
                lossy INTEGER NOT NULL DEFAULT 0,
                target_container TEXT NOT NULL DEFAULT 'mp4',
                target_video_codec TEXT,
                target_audio_codec TEXT,
                target_width INTEGER,
                target_height INTEGER,
                encoding_profile_json TEXT,
                confirmation_version INTEGER NOT NULL DEFAULT 1,
                confirmed_at TEXT,
                target_storage_kind TEXT NOT NULL,
                target_volume_id INTEGER,
                target_root TEXT NOT NULL,
                target_relative_path TEXT NOT NULL,
                target_path TEXT NOT NULL,
                temporary_path TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                stage TEXT NOT NULL DEFAULT 'waiting',
                progress_percent REAL NOT NULL DEFAULT 0,
                processed_seconds REAL,
                compatibility_class TEXT,
                error_code TEXT,
                error_detail TEXT,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                output_size INTEGER,
                output_mtime_ns INTEGER,
                output_duration REAL,
                output_video_codec TEXT,
                output_audio_codec TEXT,
                result_json TEXT,
                backup_path TEXT,
                backup_size INTEGER,
                backup_mtime_ns INTEGER,
                backup_status TEXT NOT NULL DEFAULT 'none',
                backup_created_at TEXT,
                backup_deleted_at TEXT,
                restore_status TEXT,
                last_restored_at TEXT,
                db_committed_at TEXT,
                source_deleted_at TEXT,
                created_by_user_id INTEGER,
                created_by_username TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_video_format_jobs_status
                ON video_format_jobs(status, id);
            CREATE INDEX IF NOT EXISTS idx_video_format_jobs_batch
                ON video_format_jobs(batch_id);
            CREATE INDEX IF NOT EXISTS idx_video_format_jobs_video
                ON video_format_jobs(video_id, id DESC);
            DROP INDEX IF EXISTS idx_video_format_jobs_active_video;
            CREATE UNIQUE INDEX IF NOT EXISTS idx_video_format_jobs_active_video
                ON video_format_jobs(video_id)
                WHERE status IN ('pending', 'running', 'needs_cleanup');

            CREATE TABLE IF NOT EXISTS video_delete_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL UNIQUE,
                batch_id TEXT NOT NULL,
                root_job_id INTEGER,
                parent_job_id INTEGER,
                scope_type TEXT NOT NULL,
                target_id INTEGER NOT NULL,
                target_name TEXT NOT NULL,
                source_action TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                stage TEXT NOT NULL DEFAULT 'waiting_dependencies',
                progress_percent REAL NOT NULL DEFAULT 0,
                expected_snapshot_json TEXT,
                result_json TEXT,
                error_code TEXT,
                error_detail TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                verification_method TEXT,
                verified_at TEXT,
                requested_by_user_id INTEGER,
                requested_by_username TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_video_delete_jobs_status
                ON video_delete_jobs(status, id);
            CREATE INDEX IF NOT EXISTS idx_video_delete_jobs_batch
                ON video_delete_jobs(batch_id, id);
            CREATE INDEX IF NOT EXISTS idx_video_delete_jobs_target
                ON video_delete_jobs(scope_type, target_id, id DESC);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_video_delete_jobs_active_target
                ON video_delete_jobs(scope_type, target_id)
                WHERE status IN ('pending', 'running', 'blocked');

            CREATE TABLE IF NOT EXISTS video_delete_job_dependencies (
                job_id INTEGER NOT NULL,
                depends_on_job_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(job_id, depends_on_job_id)
            );
            CREATE INDEX IF NOT EXISTS idx_video_delete_dependencies_job
                ON video_delete_job_dependencies(job_id);
            CREATE INDEX IF NOT EXISTS idx_video_delete_dependencies_parent
                ON video_delete_job_dependencies(depends_on_job_id);

            CREATE TABLE IF NOT EXISTS transport_site_policies (
                site_key TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                domains_json TEXT NOT NULL,
                transport_mode TEXT NOT NULL DEFAULT 'auto',
                lifecycle_state TEXT NOT NULL DEFAULT 'UNKNOWN',
                manual_override INTEGER NOT NULL DEFAULT 0,
                is_builtin INTEGER NOT NULL DEFAULT 0,
                qualification_enabled INTEGER NOT NULL DEFAULT 1,
                initial_connections INTEGER NOT NULL DEFAULT 2,
                max_connections INTEGER NOT NULL DEFAULT 4,
                tls_backend TEXT NOT NULL DEFAULT 'auto',
                policy_version INTEGER NOT NULL DEFAULT 1,
                breaker_state TEXT NOT NULL DEFAULT 'CLOSED',
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                disabled_until TEXT,
                quarantine_reason TEXT,
                last_success_at TEXT,
                last_failure_at TEXT,
                promoted_at TEXT,
                promoted_by TEXT,
                status_note TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS transport_site_stats (
                site_key TEXT PRIMARY KEY,
                tasks_created INTEGER NOT NULL DEFAULT 0,
                tasks_completed INTEGER NOT NULL DEFAULT 0,
                tasks_failed INTEGER NOT NULL DEFAULT 0,
                range_attempts INTEGER NOT NULL DEFAULT 0,
                range_successes INTEGER NOT NULL DEFAULT 0,
                range_failures INTEGER NOT NULL DEFAULT 0,
                qualification_successes INTEGER NOT NULL DEFAULT 0,
                qualification_failures INTEGER NOT NULL DEFAULT 0,
                ytdlp_fallbacks INTEGER NOT NULL DEFAULT 0,
                resume_attempts INTEGER NOT NULL DEFAULT 0,
                resume_successes INTEGER NOT NULL DEFAULT 0,
                url_refresh_successes INTEGER NOT NULL DEFAULT 0,
                url_refresh_failures INTEGER NOT NULL DEFAULT 0,
                rate_limit_events INTEGER NOT NULL DEFAULT 0,
                breaker_opens INTEGER NOT NULL DEFAULT 0,
                content_mismatches INTEGER NOT NULL DEFAULT 0,
                downloaded_bytes INTEGER NOT NULL DEFAULT 0,
                largest_file_bytes INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS transport_site_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_key TEXT NOT NULL,
                task_id INTEGER,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'info',
                reason_code TEXT,
                details_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_transport_events_site
                ON transport_site_events(site_key, id DESC);
            CREATE TABLE IF NOT EXISTS transport_policy_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_key TEXT NOT NULL,
                action TEXT NOT NULL,
                old_value_json TEXT,
                new_value_json TEXT,
                note TEXT,
                client_address TEXT,
                request_id TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_transport_audit_site
                ON transport_policy_audit(site_key, id DESC);
            CREATE TABLE IF NOT EXISTS site_media_policies (
                site_key TEXT PRIMARY KEY,
                preset_key TEXT NOT NULL DEFAULT 'generic',
                source_order_json TEXT NOT NULL,
                identity_mode TEXT NOT NULL DEFAULT 'site_default',
                kind_order_json TEXT NOT NULL,
                quality_target TEXT NOT NULL DEFAULT 'auto',
                quality_fallback TEXT NOT NULL DEFAULT 'nearest_below',
                min_height INTEGER NOT NULL DEFAULT 360,
                max_height INTEGER NOT NULL DEFAULT 1080,
                allow_below_minimum INTEGER NOT NULL DEFAULT 1,
                allow_page_fallback INTEGER NOT NULL DEFAULT 1,
                play_selector TEXT,
                format_strategy TEXT NOT NULL DEFAULT 'auto',
                prefer_h264 INTEGER NOT NULL DEFAULT 0,
                merge_output_format TEXT NOT NULL DEFAULT 'mp4',
                manual_override INTEGER NOT NULL DEFAULT 1,
                strategy_version INTEGER NOT NULL DEFAULT 1,
                status_note TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        _ensure_media_policy_columns(connection)
        _ensure_episode_title_columns(connection)
        _ensure_episode_discovery_schema(connection)
        _ensure_video_trim_columns(connection)
        _ensure_external_scan_columns(connection)
        _ensure_series_delete_columns(connection)
        _ensure_video_format_columns(connection)
        _ensure_playback_cache_columns(connection)
        _ensure_video_satellite_schema(connection)
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info('videos')")
        }
        if "original_url" not in columns:
            connection.execute(
                "ALTER TABLE videos ADD COLUMN original_url TEXT"
            )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_videos_original_url "
            "ON videos(original_url)"
        )
        video_migrations = {
            "display_title_override": "TEXT",
            "download_source_url": "TEXT",
            "media_origin": "TEXT NOT NULL DEFAULT 'download'",
            "storage_volume_id": "INTEGER",
            "storage_relative_path": "TEXT",
            "storage_changed_at": "TEXT",
            "is_deleted": "INTEGER NOT NULL DEFAULT 0",
            "deleted_at": "TEXT",
            "access_level": "INTEGER NOT NULL DEFAULT 0",
        }
        for column, definition in video_migrations.items():
            if column not in columns:
                connection.execute(
                    "ALTER TABLE videos ADD COLUMN {} {}".format(
                        column,
                        definition,
                    )
                )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_videos_storage_volume "
            "ON videos(storage_volume_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_videos_storage_changed "
            "ON videos(storage_changed_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_videos_lifecycle "
            "ON videos(is_deleted, missing, id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_videos_access_level "
            "ON videos(access_level, missing, is_deleted)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_videos_admin_order "
            "ON videos(created_at DESC, id DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_videos_admin_state "
            "ON videos(is_deleted, access_level, storage_volume_id, "
            "created_at DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_videos_directory_state "
            "ON videos(directory, is_deleted, created_at DESC)"
        )
        category_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info('categories')")
        }
        if "external_slug" not in category_columns:
            connection.execute(
                "ALTER TABLE categories ADD COLUMN external_slug TEXT"
            )
        if "source_domain" not in category_columns:
            connection.execute(
                "ALTER TABLE categories ADD COLUMN source_domain TEXT"
            )
        if "external_type" not in category_columns:
            connection.execute(
                "ALTER TABLE categories ADD COLUMN external_type TEXT"
            )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_categories_external "
            "ON categories(source_domain, external_slug) "
            "WHERE external_slug IS NOT NULL"
        )

        relation_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info('video_categories')"
            )
        }
        if "source_kind" not in relation_columns:
            connection.execute(
                "ALTER TABLE video_categories "
                "ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'title'"
            )
        if "source_url" not in relation_columns:
            connection.execute(
                "ALTER TABLE video_categories ADD COLUMN source_url TEXT"
            )
        if "created_at" not in relation_columns:
            connection.execute(
                "ALTER TABLE video_categories ADD COLUMN created_at TEXT"
            )
        connection.execute(
            "UPDATE video_categories SET created_at = ? "
            "WHERE created_at IS NULL",
            (_now(),),
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_video_categories_source "
            "ON video_categories(source_kind)"
        )
        cache_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info('category_fetch_cache')"
            )
        }
        if "actors_json" not in cache_columns:
            connection.execute(
                "ALTER TABLE category_fetch_cache ADD COLUMN actors_json TEXT"
            )
        if "parser_version" not in cache_columns:
            connection.execute(
                "ALTER TABLE category_fetch_cache "
                "ADD COLUMN parser_version INTEGER NOT NULL DEFAULT 0"
            )
        if "actor_metadata_version" not in cache_columns:
            connection.execute(
                "ALTER TABLE category_fetch_cache "
                "ADD COLUMN actor_metadata_version INTEGER NOT NULL DEFAULT 0"
            )
        actor_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info('actors')")
        }
        actor_migrations = {
            "external_id": "INTEGER",
            "avatar_source_url": "TEXT",
            "avatar_path": "TEXT",
            "avatar_status": "TEXT NOT NULL DEFAULT 'pending'",
            "avatar_error": "TEXT",
            "avatar_fetched_at": "TEXT",
            "avatar_attempts": "INTEGER NOT NULL DEFAULT 0",
        }
        for column, definition in actor_migrations.items():
            if column not in actor_columns:
                connection.execute(
                    "ALTER TABLE actors ADD COLUMN {} {}".format(
                        column,
                        definition,
                    )
                )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_actors_external_id "
            "ON actors(source_domain, external_id) "
            "WHERE external_id IS NOT NULL"
        )
        download_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info('download_tasks')"
            )
        }
        download_migrations = {
            "metadata_url": "TEXT",
            "normalized_metadata_url": "TEXT",
            "pause_requested": "INTEGER NOT NULL DEFAULT 0",
            "paused_at": "TEXT",
            "resume_count": "INTEGER NOT NULL DEFAULT 0",
            "run_count": "INTEGER NOT NULL DEFAULT 0",
            "filename_hint": "TEXT",
            "partial_path": "TEXT",
            "checkpoint_path": "TEXT",
            "last_progress_at": "TEXT",
            "stall_detected_at": "TEXT",
            "stall_count": "INTEGER NOT NULL DEFAULT 0",
            "speed_bytes_per_sec": "REAL NOT NULL DEFAULT 0",
            "eta_seconds": "INTEGER",
            "elapsed_seconds": "INTEGER NOT NULL DEFAULT 0",
            "run_started_at": "TEXT",
            "delete_requested": "INTEGER NOT NULL DEFAULT 0",
            "deleted_at": "TEXT",
            "media_width": "INTEGER",
            "media_height": "INTEGER",
            "media_quality": "TEXT",
            "requested_quality": "TEXT NOT NULL DEFAULT 'auto'",
            "index_into_library": "INTEGER NOT NULL DEFAULT 1",
            "media_url_expires_at": "TEXT",
            "media_refresh_count": "INTEGER NOT NULL DEFAULT 0",
            "last_media_refresh_at": "TEXT",
            "media_identity": "TEXT",
            "resume_validation": "TEXT",
            "resume_total_bytes": "INTEGER",
            "transport_kind": "TEXT",
            "transport_site_key": "TEXT",
            "media_strategy_key": "TEXT",
            "media_strategy_version": "INTEGER",
            "media_policy_snapshot_json": "TEXT",
            "selection_reason": "TEXT",
            "selected_candidate_source": "TEXT",
            "selected_candidate_kind": "TEXT",
            "fallback_trace_json": "TEXT",
            "range_connection_count": "INTEGER NOT NULL DEFAULT 0",
            "range_completed_chunks": "INTEGER NOT NULL DEFAULT 0",
            "range_total_chunks": "INTEGER NOT NULL DEFAULT 0",
            "hls_connection_count": "INTEGER NOT NULL DEFAULT 0",
            "hls_completed_segments": "INTEGER NOT NULL DEFAULT 0",
            "hls_total_segments": "INTEGER NOT NULL DEFAULT 0",
            "episode_id": "INTEGER",
            "episode_batch_id": "TEXT",
            "output_stem": "TEXT",
            "display_title": "TEXT",
            "access_level": "INTEGER NOT NULL DEFAULT 0",
            "created_by_user_id": "INTEGER",
            "created_by_username": "TEXT",
        }
        for column, definition in download_migrations.items():
            if column not in download_columns:
                connection.execute(
                    "ALTER TABLE download_tasks ADD COLUMN {} {}".format(
                        column,
                        definition,
                    )
                )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_download_tasks_episode
            ON download_tasks(episode_id)
            """
        )
        connection.execute(
            """
            UPDATE download_tasks
            SET metadata_url = url,
                normalized_metadata_url = normalized_url
            WHERE metadata_url IS NULL
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_download_tasks_metadata_source
            ON download_tasks(normalized_metadata_url, deleted_at)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_download_tasks_owner
            ON download_tasks(created_by_user_id, created_at DESC)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_episode_batches_owner
            ON episode_download_batches(created_by_user_id, created_at DESC)
            """
        )
        connection.execute(
            """
            UPDATE videos
            SET download_source_url = original_url
            WHERE download_source_url IS NULL
            """
        )
        connection.execute(
            """
            UPDATE download_tasks
            SET elapsed_seconds = MAX(
                0,
                CAST(
                    (julianday(finished_at) - julianday(started_at)) * 86400
                    AS INTEGER
                )
            )
            WHERE finished_at IS NOT NULL
              AND started_at IS NOT NULL
              AND elapsed_seconds = 0
            """
        )
        now = _now()
        built_in_sites = (
            (
                "pornone",
                "PornOne",
                ["pornone.com"],
                "direct-range",
                "VERIFIED",
                "auto",
            ),
            (
                "porntrex",
                "Porntrex",
                ["porntrex.com"],
                "direct-range",
                "VERIFIED",
                "curl_cffi",
            ),
            (
                "xvideos",
                "xVideos",
                ["xvideos.com"],
                "hls",
                "VERIFIED",
                "auto",
            ),
            (
                "xhamster",
                "xHamster",
                ["xhamster.com"],
                "hls",
                "VERIFIED",
                "auto",
            ),
        )
        for site_key, name, domains, mode, state, tls_backend in built_in_sites:
            connection.execute(
                """
                INSERT OR IGNORE INTO transport_site_policies (
                    site_key, display_name, domains_json, transport_mode,
                    lifecycle_state, manual_override, is_builtin,
                    qualification_enabled, initial_connections,
                    max_connections, tls_backend, policy_version,
                    breaker_state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, 1, 1, 2, 4, ?, 1,
                          'CLOSED', ?, ?)
                """,
                (
                    site_key,
                    name,
                    json.dumps(domains),
                    mode,
                    state,
                    tls_backend,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO transport_site_stats (
                    site_key, updated_at
                ) VALUES (?, ?)
                """,
                (site_key, now),
            )


def baseline_schema_functions() -> tuple:
    """Return every function that participates in the frozen baseline."""

    return (
        _apply_legacy_schema,
        _ensure_media_policy_columns,
        _ensure_episode_title_columns,
        _ensure_episode_discovery_schema,
        _ensure_video_trim_columns,
        _ensure_external_scan_columns,
        _ensure_series_delete_columns,
        _ensure_video_format_columns,
        _ensure_playback_cache_columns,
        _ensure_video_satellite_schema,
    )


def init_db() -> None:
    from ..repositories.download_batches import reconcile_episode_batch_library
    from ..repositories.integrity import (
        _repair_orphan_video_dependencies,
        repair_video_satellite_integrity,
    )
    from ..repositories.storage import _repair_storage_paths_relative_to_mount
    from .migrations import run_migrations

    run_migrations(
        _apply_legacy_schema,
        baseline_functions=baseline_schema_functions(),
    )
    with connect() as connection:
        _repair_storage_paths_relative_to_mount(connection)
        _repair_orphan_video_dependencies(connection)
    reconcile_episode_batch_library()
    repair_video_satellite_integrity()
