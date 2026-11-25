from __future__ import annotations

from .runner import BASELINE_VERSION, Migration


format_subtitle_selection_migration = Migration(
    BASELINE_VERSION + 9,
    "add format subtitle selection",
    (
        """
        ALTER TABLE video_format_jobs
        ADD COLUMN subtitle_mode TEXT NOT NULL DEFAULT 'auto'
        """,
        """
        ALTER TABLE video_format_jobs
        ADD COLUMN subtitle_streams_json TEXT NOT NULL DEFAULT '[]'
        """,
        """
        ALTER TABLE video_format_jobs
        ADD COLUMN default_subtitle_stream_index INTEGER
        """,
    ),
)
