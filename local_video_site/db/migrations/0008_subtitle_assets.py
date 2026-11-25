from __future__ import annotations

from .runner import BASELINE_VERSION, Migration


subtitle_assets_migration = Migration(
    BASELINE_VERSION + 7,
    "create subtitle assets and add extraction stage vocabulary",
    (
        """
        CREATE TABLE IF NOT EXISTS video_subtitle_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id INTEGER NOT NULL,
            source_stream_index INTEGER NOT NULL,
            language TEXT,
            title TEXT,
            extension TEXT NOT NULL,
            storage_path TEXT NOT NULL UNIQUE,
            default_track INTEGER NOT NULL DEFAULT 0,
            source_size INTEGER NOT NULL,
            source_mtime_ns INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(video_id, source_stream_index, extension),
            FOREIGN KEY (video_id)
                REFERENCES videos(id) ON DELETE CASCADE
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_video_subtitle_video
        ON video_subtitle_assets(video_id, source_stream_index)
        """,
        """
        INSERT OR IGNORE INTO schema_value_set_items (
            set_key, value, label, description, sort_order, is_default
        ) VALUES (
            'format_stage',
            'extracting_subtitles',
            '提取字幕资产',
            '提取并转换字幕后继续提交视频。',
            75,
            0
        )
        """,
        """
        UPDATE schema_value_sets
        SET version = version + 1,
            updated_at = strftime('%Y-%m-%dT%H:%M:%S+08:00', 'now', 'localtime')
        WHERE set_key = 'format_stage'
        """,
    ),
    compatible_checksums=(
        "647747a134c2f733e3a578f0f60aa88499131e6f146869f5ef75f371ec01c305",
    ),
)
