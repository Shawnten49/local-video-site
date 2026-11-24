from __future__ import annotations

from .runner import BASELINE_VERSION, Migration


runtime_integrity_migration = Migration(
    BASELINE_VERSION + 1,
    "create runtime integrity runs",
    (
        """
        CREATE TABLE IF NOT EXISTS runtime_integrity_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT NOT NULL,
            issue_count INTEGER NOT NULL DEFAULT 0,
            checks_json TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_runtime_integrity_runs_created
        ON runtime_integrity_runs(finished_at DESC, id DESC)
        """,
    ),
)
