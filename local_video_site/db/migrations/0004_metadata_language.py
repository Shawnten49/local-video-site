from __future__ import annotations

from .runner import BASELINE_VERSION, Migration


metadata_language_migration = Migration(
    BASELINE_VERSION + 3,
    "add metadata language to category fetch cache",
    (
        """
        ALTER TABLE category_fetch_cache
        ADD COLUMN metadata_language TEXT NOT NULL DEFAULT ''
        """,
    ),
)
